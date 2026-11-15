"""Cost Allocation Run (konsol#269).

K7: Compute Weights, draft only. It reads the Active rule set, the submitted
statistic values for the run's entity, scenario and fiscal year, and the
account tree, then snapshots everything the build needs onto the run. A run
that cannot be computed saves nothing: the refusal lists every missing value.

The frappe reads are small helpers so host tests can stub them; the row
shaping is pure.

K8: submit is the approval. The gates run in before_submit / before_cancel and
the warehouse sync is queued after the commit (header, accounts, weights).
The coefficients table, rebuilt from every submitted run, is a job queued
after the commit (RF4, RF7: see resync_coefficients_job), with one locked
writer and an hourly check (RF9: _locked_resync, check_coefficients_staging).
"""
import functools
import re
import time
import uuid
from decimal import Decimal

import frappe
from frappe.model.document import Document

from konsol import clickhouse, period_status
from konsol import cost_allocation_model as model
from konsol.epm.doctype.cost_allocation_run_account.cost_allocation_run_account import (
    CostAllocationRunAccount,
)
from konsol.epm.doctype.cost_allocation_run_weight.cost_allocation_run_weight import (
    CostAllocationRunWeight,
)


def _load_rule_set(name):
    return frappe.get_doc("Cost Allocation Rule Set", name)


def _statistic_values(entity, scenario, fiscal_year, period):
    """Submitted statistic values for the entity, scenario and fiscal year, up
    to and including ``period``. Phase 1 carries forward only within the same
    fiscal year, so earlier periods of that year are all the model needs."""
    stats = frappe.get_all(
        "Cost Allocation Statistic",
        filters={"docstatus": 1, "entity": entity, "scenario": scenario,
                 "fiscal_year": fiscal_year},
        fields=["name", "driver"], order_by="name")
    if not stats:
        return []
    driver_of = {s["name"]: s["driver"] for s in stats}
    rows = frappe.get_all(
        "Cost Allocation Statistic Value",
        filters={"parent": ["in", list(driver_of)], "fiscal_period": ["<=", period]},
        fields=["parent", "fiscal_period", "member", "value"],
        parent_doctype="Cost Allocation Statistic", order_by="parent, idx")
    return [{"statistic": r["parent"], "driver": driver_of[r["parent"]],
             "fiscal_period": r["fiscal_period"], "member": r["member"],
             "value": r["value"]} for r in rows]


def _driver_carry_forward(drivers):
    """{driver: carry_forward flag} for the named drivers."""
    if not drivers:
        return {}
    rows = frappe.get_all("Cost Allocation Driver", filters={"name": ["in", list(drivers)]},
                          fields=["name", "carry_forward"], order_by="name")
    return {r["name"]: bool(r["carry_forward"]) for r in rows}


def _descendants(account):
    """The account and every Main Account below it (nested set lft/rgt)."""
    row = frappe.db.get_value("Main Account", account, ["lft", "rgt"], as_dict=True)
    if not row:
        return [account]
    return frappe.get_all("Main Account",
                          filters={"lft": [">=", row["lft"]], "rgt": ["<=", row["rgt"]]},
                          pluck="name", order_by="lft")


def _first_draft_state():
    """The active workflow's first state whose doc_status is 0, or None with no
    active workflow. A site may rename the states, so the name is read, not assumed."""
    from frappe.model.workflow import get_workflow_name

    name = get_workflow_name("Cost Allocation Run")
    if not name:
        return None
    for state in frappe.get_cached_doc("Workflow", name).states:
        if int(state.doc_status or 0) == 0:
            return state.state
    return None


def _now():
    return frappe.utils.now_datetime()


def _user():
    return frappe.session.user


def _rows(table):
    return [r.as_dict() if hasattr(r, "as_dict") else dict(r) for r in table or []]


# --- pure shaping ---------------------------------------------------------------

def value_maps(stat_rows):
    """({(driver, period, member): value}, {(driver, period, member): statistic})."""
    values, names = {}, {}
    for r in stat_rows:
        key = (r["driver"], r["fiscal_period"], r["member"])
        values[key] = r["value"]
        names[key] = r["statistic"]
    return values, names


def weight_rows(resolved, step_weights, names):
    """Child rows for Cost Allocation Run Weight from resolved driver values."""
    return [{
        "step": r["step"], "sender_member": r["sender_member"],
        "receiver_member": r["receiver_member"], "driver": r["driver"],
        "statistic": names[(r["driver"], r["value_period"], r["receiver_member"])],
        "value_period": r["value_period"], "carried_forward": 1 if r["carried_forward"] else 0,
        "driver_value": r["driver_value"],
        "weight": float(step_weights[(r["step"], r["receiver_member"])]),
    } for r in resolved]


def account_rows(declared, descendants_of):
    """Child rows for Cost Allocation Run Account: each declared account and
    its descendants, once each (the first declaration wins)."""
    seen, out = set(), []
    for acc in declared:
        for name in descendants_of(acc):
            if name not in seen:
                seen.add(name)
                out.append({"main_account": name, "declared_account": acc})
    return out


# --- compute --------------------------------------------------------------------

def compute_weights_on(run):
    """Compute and snapshot onto ``run`` (a draft). Returns a small summary."""
    draft = _first_draft_state()
    if run.docstatus != 0 or (draft is not None and run.status != draft):
        frappe.throw(f"Compute Weights works only on a run in status {draft or 'Draft'}: "
                     "an approver must not approve weights that changed after review.")
    rs = _load_rule_set(run.rule_set)
    if rs.status != "Active":
        frappe.throw(f"Rule set {run.rule_set} is {rs.status}; a run needs an Active rule set.")
    in_scope = {r["scenario"] for r in _rows(rs.scenarios)}
    if not rs.applies_to_all_scenarios and run.scenario not in in_scope:
        frappe.throw(f"Scenario {run.scenario} is not in the scope of rule set {run.rule_set}.")

    steps = model.build_steps(
        [{"step": r["step"], "sender_member": r["sender_member"], "driver": r["driver"]}
         for r in _rows(rs.rules)],
        [{"step": r["step"], "receiver_member": r["receiver_member"]} for r in _rows(rs.receivers)])
    values, names = value_maps(
        _statistic_values(rs.entity, run.scenario, run.fiscal_year, run.fiscal_period))
    carry = _driver_carry_forward(sorted({s.driver for s in steps}))
    try:
        resolved = model.resolve_driver_values(steps, run.fiscal_period, values, carry)
        step_weights = model.weights(resolved)
    except model.CostAllocationError as exc:
        frappe.throw("<br>".join(exc.errors))

    weights = weight_rows(resolved, step_weights, names)
    # RF4: build the coefficient rows now and discard them, so a configuration
    # the model cannot solve (a loop whose exit edges all have zero statistics)
    # is refused here with the model's message, not first at submit.
    try:
        _coefficient_rows(run.name, rs.method, weights)
    except model.CostAllocationError as exc:
        frappe.throw("<br>".join(exc.errors))
    accounts = account_rows([r["main_account"] for r in _rows(rs.accounts)], _descendants)
    carried = sum(1 for w in weights if w["carried_forward"])

    run.entity = rs.entity
    run.method = rs.method
    run.allocation_dimension = rs.allocation_dimension
    run.receiver_account_mode = rs.receiver_account_mode
    run.allocation_account = rs.allocation_account
    run.amounts_after_approval = rs.amounts_after_approval
    run.rule_set_modified = rs.modified
    run.computed_for_rule_set = run.rule_set
    run.computed_for_scenario = run.scenario
    run.computed_for_fiscal_year = run.fiscal_year
    run.computed_for_fiscal_period = run.fiscal_period
    run.computed_at = _now()
    run.computed_by = _user()
    run.values_carried_forward = 1 if carried else 0
    run.set("weights", weights)
    run.set("accounts", accounts)
    run.save()
    return {"carried_forward_count": carried, "weights": len(weights)}


@frappe.whitelist(methods=["POST"])
def compute_weights(name):
    """Compute Weights button: POST, because it writes."""
    return compute_weights_on(frappe.get_doc("Cost Allocation Run", name))


# --- K12: Preview Sender Pools (read-only) ----------------------------------------

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _declared_dimensions():
    """Names of the declared Dimensions: the only column names the pool query accepts."""
    return frappe.get_all("Dimension", pluck="name", order_by="name", limit_page_length=0)


def _array_literal(values):
    """A ClickHouse Array(String) literal for an HTTP query parameter."""
    return "[" + ",".join("'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'" for v in values) + "]"


def build_sender_pool_query(dimension, declared, scenario, entity, fiscal_year,
                            fiscal_period, accounts, senders):
    """(sql, params) for the pool of each sender member (pure).

    Every value travels as a ClickHouse query parameter. The one identifier, the
    dimension column, is refused unless it is a declared dimension name and a
    plain identifier, then backtick-quoted."""
    if dimension not in set(declared or []) or not _IDENTIFIER.match(dimension or ""):
        frappe.throw(f"{_PREFIX}dimension {dimension!r} is not a declared dimension.")
    sql = (
        f"SELECT `{dimension}` AS member, sum(amount) AS pool "
        "FROM epm_gold.gold_scenario_trial_balance "
        "WHERE scenario_id = {scenario:String} AND data_area_id = {entity:String} "
        "AND fiscal_year = {fiscal_year:UInt16} AND fiscal_period = {fiscal_period:UInt8} "
        "AND main_account IN {accounts:Array(String)} "
        f"AND `{dimension}` IN {{senders:Array(String)}} "
        f"GROUP BY `{dimension}` ORDER BY member FORMAT TabSeparated"
    )
    params = {
        "param_scenario": scenario, "param_entity": entity,
        "param_fiscal_year": str(fiscal_year), "param_fiscal_period": str(fiscal_period),
        "param_accounts": _array_literal(accounts), "param_senders": _array_literal(senders),
    }
    return sql, params


def _query_sender_pools(sql, params):
    """Run the pool query; [(member, amount text)]. The only ClickHouse call."""
    text = clickhouse.execute(sql, params)
    return [tuple(line.split("\t")) for line in text.splitlines() if line]


@frappe.whitelist(methods=["GET"])
def preview_sender_pools(name):
    """Each sender's pool on the run's accounts from the warehouse, and the total.
    Writes nothing, so GET is safe."""
    run = frappe.get_doc("Cost Allocation Run", name)
    run.check_permission("read")
    if not run.weights:
        frappe.throw(f"{_PREFIX}Compute Weights before previewing sender pools.")
    senders = []
    for w in run.weights:
        if w.sender_member not in senders:
            senders.append(w.sender_member)
    sql, params = build_sender_pool_query(
        run.allocation_dimension, _declared_dimensions(), run.scenario, run.entity,
        run.fiscal_year, run.fiscal_period, [a.main_account for a in run.accounts], senders)
    found = {m: Decimal(a) for m, a in _query_sender_pools(sql, params)}
    pools = [{"sender_member": s, "amount": float(found.get(s, 0))} for s in senders]
    return {"pools": pools, "total": float(sum(found.get(s, 0) for s in senders)),
            "has_rows": bool(found)}


_PREFIX = "Cost Allocation Run: "


class CostAllocationRun(Document):
    CH_TABLE = "epm_staging.cost_allocation_runs"
    # CH column -> Frappe field; the warehouse names the entity data_area_id
    # and the scenario scenario_id, as every staging table does.
    CH_FIELD_MAP = {
        "name": "name",
        "rule_set": "rule_set",
        "method": "method",
        "allocation_dimension": "allocation_dimension",
        "receiver_account_mode": "receiver_account_mode",
        "allocation_account": "allocation_account",
        "amounts_after_approval": "amounts_after_approval",
        "data_area_id": "entity",
        "scenario_id": "scenario",
        "fiscal_year": "fiscal_year",
        "fiscal_period": "fiscal_period",
        "computed_at": "computed_at",
    }
    #: computed, not field-mapped: reconcile_all finds it through resync_staging
    CH_STAGING_TABLE = "epm_staging.cost_allocation_coefficients"
    CHILD_CONTROLLERS = {
        "Cost Allocation Run Account": CostAllocationRunAccount,
        "Cost Allocation Run Weight": CostAllocationRunWeight,
    }

    # -- gates ---------------------------------------------------------------

    def before_insert(self):
        """An amended run starts empty. Frappe v16's client copy_doc ignores
        no_copy when amending (create_new.js: is_no_copy = !from_amend && ...),
        so the server clears what Compute Weights wrote."""
        if not self.get("amended_from"):
            return
        self.set("weights", [])
        self.set("accounts", [])
        for field in ("computed_at", "computed_by", "computed_for_rule_set",
                      "computed_for_scenario", "computed_for_fiscal_year",
                      "computed_for_fiscal_period", "rule_set_modified"):
            setattr(self, field, None)
        self.values_carried_forward = 0

    def before_submit(self):
        """Submit is the approval. The period must be declared on every
        scenario. The closed-period gate on submit applies to an actual
        scenario only (Deepak Pai, 14 Nov 2026, konsol#269 comment 6874924763;
        rejected: gating every scenario)."""
        scenario = frappe.db.get_value("Scenario", self.scenario, ["scenario_type", "is_active"],
                                       as_dict=True)
        if not scenario or not scenario.is_active:
            frappe.throw(f"{_PREFIX}scenario {self.scenario} is not active.")
        period_status.assert_declared(self.fiscal_year, self.fiscal_period)
        if scenario.scenario_type == "actual":
            period_status.assert_open(self.fiscal_year, self.fiscal_period,
                                      action=f"approve cost allocation run {self.name}")
        weights = list(self.get("weights") or [])
        if not weights:
            frappe.throw(f"{_PREFIX}Compute Weights before submitting.")
        rs = frappe.db.get_value(
            "Cost Allocation Rule Set", self.rule_set,
            ["modified", "status", "applies_to_all_scenarios"], as_dict=True)
        if not rs or rs.status != "Active":
            frappe.throw(f"{_PREFIX}rule set {self.rule_set} must be Active to submit a run.")
        if rs.modified != self.rule_set_modified:
            frappe.throw(
                f"{_PREFIX}rule set {self.rule_set} changed after the weights were computed. "
                "Recompute the weights, then submit.")
        self._refuse_stale_snapshot()
        if not rs.applies_to_all_scenarios:
            in_scope = frappe.get_all(
                "Cost Allocation Rule Set Scenario",
                filters={"parent": self.rule_set}, pluck="scenario",
                parent_doctype="Cost Allocation Rule Set", limit_page_length=0)
            if self.scenario not in in_scope:
                frappe.throw(f"{_PREFIX}scenario {self.scenario} is not in the scope of "
                             f"rule set {self.rule_set}.")
        self._refuse_stale_statistics(_rows(weights))
        self._refuse_unbuildable_coefficients(_rows(weights))
        self._refuse_shared_senders({w.sender_member for w in weights})

    def _refuse_stale_snapshot(self):
        """The weights belong to the rule set, scenario, year and period they were
        computed for. These fields stay editable after Compute."""
        stale = [label for label, now, then in (
            ("rule set", self.rule_set, self.get("computed_for_rule_set")),
            ("scenario", self.scenario, self.get("computed_for_scenario")),
            ("fiscal year", self.fiscal_year, self.get("computed_for_fiscal_year")),
            ("fiscal period", self.fiscal_period, self.get("computed_for_fiscal_period")))
            if now != then]
        if stale:
            frappe.throw(
                f"{_PREFIX}the weights were computed for another {', '.join(stale)}. "
                "Recompute the weights, then submit.")

    def _refuse_stale_statistics(self, weights):
        """Re-read every cited statistic value with FOR UPDATE: the statistic must
        still be submitted and hold the value the weight was computed from."""
        cited = sorted({w["statistic"] for w in weights if w.get("statistic")})
        if not cited:
            return
        rows = frappe.db.sql(
            "SELECT s.name AS statistic, s.docstatus AS docstatus, "
            "v.fiscal_period AS fiscal_period, v.member AS member, v.value AS value "
            "FROM `tabCost Allocation Statistic` s "
            "JOIN `tabCost Allocation Statistic Value` v "
            "ON v.parent = s.name AND v.parenttype = 'Cost Allocation Statistic' "
            "WHERE s.name IN %(statistics)s FOR UPDATE",
            {"statistics": tuple(cited)}, as_dict=True)
        now = {(r["statistic"], int(r["fiscal_period"]), r["member"]): r for r in rows}
        stale = []
        for w in weights:
            key = (w.get("statistic"), int(w.get("value_period") or 0), w["receiver_member"])
            row = now.get(key)
            if (row is None or row["docstatus"] != 1
                    or _nine(row["value"]) != _nine(w["driver_value"])):
                stale.append(f"{key[0]} (period {key[1]}, {key[2]})")
        if stale:
            frappe.throw(
                f"{_PREFIX}statistic values changed or were cancelled after the weights "
                "were computed: " + ", ".join(sorted(set(stale))) + ". Recompute the weights.")

    def _refuse_unbuildable_coefficients(self, weights):
        """Build the coefficient rows now, so an invalid run is refused at submit
        and cannot stop the coefficients sync for every other run."""
        try:
            _coefficient_rows(self.name, self.method, weights)
        except model.CostAllocationError as exc:
            frappe.throw(f"{_PREFIX}{self.name}: " + "<br>".join(exc.errors))

    def _refuse_shared_senders(self, senders):
        """A sender's pool is relieved once per entity, scenario and period.
        Read with FOR UPDATE: under REPEATABLE READ a plain read misses a run
        submitted after this transaction's first read."""
        rows = frappe.db.sql(
            "SELECT r.name AS name, w.sender_member AS sender_member "
            "FROM `tabCost Allocation Run` r "
            "JOIN `tabCost Allocation Run Weight` w "
            "ON w.parent = r.name AND w.parenttype = 'Cost Allocation Run' "
            "WHERE r.docstatus = 1 AND r.entity = %(entity)s AND r.scenario = %(scenario)s "
            "AND r.fiscal_year = %(fiscal_year)s AND r.fiscal_period = %(fiscal_period)s "
            "AND r.name != %(name)s FOR UPDATE",
            {"entity": self.entity, "scenario": self.scenario, "fiscal_year": self.fiscal_year,
             "fiscal_period": self.fiscal_period, "name": self.name or ""},
            as_dict=True)
        clashes = sorted({(r.name, r.sender_member) for r in rows if r.sender_member in senders})
        if clashes:
            frappe.throw(
                f"{_PREFIX}sender already allocated by a submitted run for this entity, "
                "scenario and period: "
                + ", ".join(f"{n} ({m})" for n, m in clashes)
                + ". Cancel that run first.")

    def before_cancel(self):
        """Cancel only while the period is open, and only for an actual scenario
        (Deepak Pai, 13 Nov 2026, konsol#269 Q4)."""
        scenario = frappe.db.get_value("Scenario", self.scenario, "scenario_type")
        if scenario != "actual":
            return
        period_status.assert_open(self.fiscal_year, self.fiscal_period,
                                  action=f"cancel cost allocation run {self.name}")

    # -- warehouse -----------------------------------------------------------

    def on_submit(self):
        self._sync()

    def on_cancel(self):
        self._sync()

    def after_delete(self):
        """after_delete, not on_trash (#120)."""
        self._sync()

    def _sync(self):
        """Header and children after the commit; the coefficients as ONE job
        after the commit (RF4), which then requests the allocation build."""
        clickhouse.sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        for doctype, controller in self.CHILD_CONTROLLERS.items():
            clickhouse.sync_doctype_after_commit(
                doctype, controller.CH_TABLE, controller.CH_FIELD_MAP)
        # RF7: one callback after the commit decides whether to enqueue, so the
        # decision sees the queue as it is once this change is committed.
        frappe.db.after_commit.add(functools.partial(_queue_coefficient_resync, self.name))

    @classmethod
    def resync_staging(cls, force=False):
        """reconcile_all's entry for the coefficients table (RF9). It writes
        nothing: it enqueues the locked resync job, with no build request.

        Not the lock and the rollback here: reconcile_all runs inside
        after_migrate, whose transaction still holds uncommitted work (roles,
        seeds, dimension Custom Fields; the migrate commits later), so a
        rollback would discard it, and a migrate must not wait on the lock.
        The job reads after its own rollback, so it sees every committed run.

        Returns "queued" when a job is queued (reconcile records that instead
        of a row count), or None when the enqueue failed (logged; reconcile
        reports the table as not synced and the hourly check repairs it).
        """
        job = _queue_coefficient_resync(None, request_build=False)
        return "queued" if job else None

    @classmethod
    def _write_coefficients(cls, force=False):
        """Rebuild epm_staging.cost_allocation_coefficients from every submitted
        run (TRUNCATE+INSERT). Called only by _locked_resync, under the lock
        and after the rollback: never call it directly.

        The exact weights are rebuilt from the stored ``driver_value`` column
        with ``model.weights()``, which turns each value into a Fraction through
        str(), so 0.1 is the decimal 0.1. The float ``weight`` column is never
        read: a float cannot hold 1/3 and its steps would not sum to exactly 1,
        which the coefficient rounding refuses.
        """
        runs = frappe.get_all(
            "Cost Allocation Run", filters={"docstatus": 1},
            fields=["name", "method", "modified"], order_by="name", limit_page_length=0)
        stored = []
        if runs:
            stored = frappe.get_all(
                "Cost Allocation Run Weight",
                filters={"parent": ["in", [r["name"] for r in runs]]},
                fields=["parent", "idx", "step", "sender_member", "receiver_member",
                        "driver", "driver_value"],
                parent_doctype="Cost Allocation Run", order_by="parent, idx",
                limit_page_length=0)
        by_run = {}
        for w in stored:
            by_run.setdefault(w["parent"], []).append(w)
        columns = list(model.COEFFICIENT_COLUMNS)
        rows = []
        for run in runs:
            try:
                rows.extend(_coefficient_rows(run["name"], run["method"], by_run.get(run["name"], [])))
            except model.CostAllocationError as exc:
                frappe.throw(f"{_PREFIX}{run['name']}: " + "<br>".join(exc.errors))
        modified = [r["modified"] for r in runs if r.get("modified")]
        source_max_modified = max(modified).strftime("%Y-%m-%d %H:%M:%S") if modified else None
        return clickhouse.sync_table(
            cls.CH_STAGING_TABLE, columns, [[r[c] for c in columns] for r in rows],
            source_max_modified=source_max_modified, force=force)


# -- coefficient resync job (RF4, RF7, RF9) -------------------------------------

RESYNC_JOB = "konsol.epm.doctype.cost_allocation_run.cost_allocation_run.resync_coefficients_job"
#: The job's timeouts (RF9, R4). They satisfy
#:   RESYNC_WAIT_SECONDS + RESYNC_RUN_SECONDS <= RESYNC_JOB_TIMEOUT <= RESYNC_LOCK_SECONDS
#: - RESYNC_JOB_TIMEOUT is passed to enqueue explicitly, so a job that waited
#:   its full RESYNC_WAIT_SECONDS still has RESYNC_RUN_SECONDS to run.
#: - A job takes the lock only after it started, and RQ kills it at
#:   RESYNC_JOB_TIMEOUT after the start, so a live holder never outlives its
#:   lock. The extra 60 s cover RQ's kill latency.
#: - A waiter cannot also outwait a dead holder's lock: that would need
#:   WAIT >= LOCK >= JOB >= WAIT + RUN. A waiter that times out re-enqueues
#:   instead (resync_coefficients_job), so the chain of waiters outlasts the
#:   dead holder's lock and no change is dropped.
RESYNC_WAIT_SECONDS = 600
RESYNC_RUN_SECONDS = 900
RESYNC_JOB_TIMEOUT = RESYNC_WAIT_SECONDS + RESYNC_RUN_SECONDS
RESYNC_LOCK_SECONDS = RESYNC_JOB_TIMEOUT + 60
#: the queued marker outlives any QUEUED job; once it expires, the next
#: commit simply enqueues
RESYNC_MARKER_SECONDS = 86400


#: _locked_resync's result when another resync held the lock for
#: RESYNC_WAIT_SECONDS (a sentinel, not an exception class: the controller
#: module defines one class)
_NO_LOCK = object()


def _queued_marker_key():
    return f"cost_allocation_coefficients_queued::{frappe.local.site}"


def _resync_lock_key():
    return f"konsol:cost_allocation_coefficients_resync:{frappe.local.site}"


def _job_status(job_id):
    """The RQ status of a job enqueued by _queue_coefficient_resync, as a
    plain string ('queued', 'started', ...), or None when RQ no longer has it."""
    from frappe.utils.background_jobs import get_job

    job = get_job(job_id)
    if job is None:
        return None
    status = job.get_status(refresh=False)     # fetched just now: current
    return getattr(status, "value", status)


def _marker_status():
    """(marker job id, its RQ status), or (None, None) without a marker."""
    marker = frappe.cache.get(_queued_marker_key())
    if not marker:
        return None, None
    marker = marker.decode()
    return marker, _job_status(marker)


def _queue_coefficient_resync(run_name, request_build=True):
    """After-commit callback of CostAllocationRun._sync (RF7), and the one
    enqueue path of reconcile, the lock-timeout retry and the hourly check
    (RF9). Returns the id of the job that will read after this point, or None
    when the enqueue failed.

    Skips only when the last job it enqueued is still QUEUED: RQ sets STARTED
    in prepare_job_execution, before execute_job runs the body, so a QUEUED
    job has not reached its read yet and will see this commit. Any other
    state (STARTED, FINISHED, FAILED, expired) enqueues a new job.

    Job ids: each enqueue gets a fresh id, and a Redis marker remembers the
    latest one. A stable id would make RQ overwrite the hash of a STARTED
    job with the same id, and Frappe's own ``deduplicate`` would refuse to
    enqueue while that job is STARTED (the RC8b loss). Two commits racing
    here can both enqueue: that costs one extra job, never a lost change.

    ``request_build=False`` (reconcile) does not set the marker: a later
    commit must not skip behind a job that requests no build.

    Guarded (R3): Frappe runs after-commit callbacks unguarded, so a Redis
    outage here would raise out of the commit. A failure is logged, naming the
    run, and the hourly check_coefficients_staging repairs the table."""
    try:
        marker, status = _marker_status()
        if marker and status == "queued":
            return marker
        job_id = f"cost_allocation_coefficients|{uuid.uuid4().hex}"
        kwargs = {} if request_build else {"request_build": False}
        frappe.enqueue(RESYNC_JOB, queue="long", timeout=RESYNC_JOB_TIMEOUT, job_id=job_id,
                       run=run_name, **kwargs)
        if request_build:
            frappe.cache.set(_queued_marker_key(), job_id, ex=RESYNC_MARKER_SECONDS)
        return job_id
    except Exception:  # noqa: BLE001 — never raise out of a commit
        message = (f"Run {run_name}: the coefficients resync job could not be queued. "
                   "The hourly check resyncs the coefficients.")
        # The file log always lands. The Error Log is a deferred insert: an
        # insert made after the request's commit would never be committed.
        frappe.logger().error(message, exc_info=True)
        try:
            frappe.log_error(title="Cost Allocation coefficients resync not queued",
                             message=message, defer_insert=True)
        except Exception:  # noqa: BLE001 — Redis cache down too: the file log has it
            pass
        return None


def _locked_resync(force=True):
    """The one writer of epm_staging.cost_allocation_coefficients (RF9, R2).

    Takes the site-scoped coefficient lock, rolls back (so under REPEATABLE
    READ the read opens a fresh snapshot after every commit before it), then
    rebuilds the table. Returns the rows written, or _NO_LOCK when the lock
    stays held for RESYNC_WAIT_SECONDS."""
    lock = frappe.cache.lock(_resync_lock_key(), timeout=RESYNC_LOCK_SECONDS,
                             blocking_timeout=RESYNC_WAIT_SECONDS)
    if not lock.acquire():
        return _NO_LOCK
    try:
        frappe.db.rollback()
        return CostAllocationRun._write_coefficients(force=force)
    finally:
        try:
            lock.release()
        except Exception:  # noqa: BLE001 — expired after RESYNC_LOCK_SECONDS: keep the real error
            frappe.logger().warning(
                f"Cost Allocation coefficients resync: lock {_resync_lock_key()} had expired")


def _latest_run():
    """The trigger for a job started without a run (by hand): the most
    recently modified submitted or cancelled run, the last change the
    coefficients reflect. None when the site has no such run."""
    names = frappe.get_all("Cost Allocation Run", filters={"docstatus": ["in", [1, 2]]},
                           pluck="name", order_by="modified desc", limit=1)
    return names[0] if names else None


def resync_coefficients_job(run=None, request_build=True):
    """RQ job queued by _queue_coefficient_resync (RF4, RF7, RF9).

    _locked_resync rebuilds the coefficients under the lock with force (a
    ClickHouse failure raises). Then, unless ``request_build`` is False
    (reconcile), the job requests the allocation build naming ``run``, or the
    latest submitted or cancelled run; with neither (R6) it requests no
    build. A failure is logged and raised, and no build is requested.

    A job that cannot get the lock within RESYNC_WAIT_SECONDS re-enqueues
    itself through the guarded path (a new id, the marker updated) and logs
    it, so the change it carries is not dropped (R1).

    Why no committed change C is lost: C's after-commit callback either
    enqueued a job J, or saw J QUEUED. RQ marks a job STARTED before the body
    runs, so in both cases J's body starts after C committed. J takes the lock
    and rolls back before reading, so under REPEATABLE READ its snapshot opens
    after C's commit and contains C. The lock serialises every writer (jobs
    and reconcile's job alike), so each reads and writes the full table after
    the one before it finished. A job that dies before it reads, or a callback
    that could not enqueue, is caught by the hourly check_coefficients_staging."""
    started = time.monotonic()
    try:
        written = _locked_resync(force=True)
    except Exception:
        frappe.log_error(title="Cost Allocation coefficients resync failed",
                         message=f"Run {run}: the coefficients resync failed; no build requested.")
        raise
    if written is _NO_LOCK:
        retry = _queue_coefficient_resync(run, request_build=request_build)
        frappe.log_error(
            title="Cost Allocation coefficients resync: lock timeout",
            message=(f"Run {run}: another resync held the lock for {RESYNC_WAIT_SECONDS}s. "
                     f"Re-queued as {retry}."))
        return
    trigger = run or _latest_run()
    if not request_build:
        outcome = "no build requested (reconcile)"
    elif trigger is None:
        outcome = "no build requested: no submitted or cancelled run"
    else:
        from konsol.tasks import request_consolidation_build
        request_consolidation_build("Cost Allocation Run", trigger, "coefficients_resync")
        outcome = "build requested"
    frappe.logger().info(
        f"Cost Allocation coefficients resync: run {trigger}, {written} rows, "
        f"{time.monotonic() - started:.2f}s, {outcome}")


def check_coefficients_staging():
    """Hourly safety net (RF9, R1): a resync lost to a killed worker, a flushed
    Redis or a failed enqueue becomes visible and is repaired.

    Compares the set of submitted run names with the distinct runs in
    epm_staging.cost_allocation_coefficients. That is exact: a submitted
    run's weights cannot change (no field is allow_on_submit), submit refuses
    a run without weights, and every weight yields at least its sender's
    `own` row. So the table is a function of that set, and the comparison
    costs one Frappe read and one ClickHouse GROUP BY.

    On a match it does nothing. While a resync is queued or running it waits
    for the next hour (that job reads after the commit it was queued for). On
    a mismatch it logs one Error Log line naming the missing and extra runs
    and enqueues the resync through the guarded path, naming the first
    missing run (None when only extra runs remain)."""
    try:
        expected = set(frappe.get_all("Cost Allocation Run", filters={"docstatus": 1},
                                      pluck="name", limit_page_length=0))
        out = clickhouse.execute(
            f"SELECT DISTINCT run FROM {CostAllocationRun.CH_STAGING_TABLE} FORMAT TSV")
        held = {line for line in out.splitlines() if line}
    except Exception:  # noqa: BLE001 — unreachable ClickHouse: check again next hour
        frappe.logger().warning("Cost Allocation coefficients check skipped", exc_info=True)
        return
    if held == expected:
        return
    _marker, status = _marker_status()
    if status in ("queued", "started"):
        return
    missing, extra = sorted(expected - held), sorted(held - expected)
    retry = _queue_coefficient_resync(missing[0] if missing else None)
    frappe.log_error(
        title="Cost Allocation coefficients out of step with submitted runs",
        message=(f"Missing: {', '.join(missing) or 'none'}; extra: {', '.join(extra) or 'none'}; "
                 f"resync queued as {retry}."))


def _nine(value):
    """A driver value at the 9 places both the statistic and the weight store."""
    return Decimal(str(value or 0)).quantize(Decimal("0.000000001"))


def _coefficient_rows(run_name, method, stored):
    """Coefficient rows for one run from its stored weight rows (pure)."""
    rules, receivers, seen = {}, [], set()
    for w in stored:
        rules.setdefault(w["step"], {"step": w["step"], "sender_member": w["sender_member"],
                                     "driver": w["driver"]})
        if (w["step"], w["receiver_member"]) not in seen:
            seen.add((w["step"], w["receiver_member"]))
            receivers.append({"step": w["step"], "receiver_member": w["receiver_member"]})
    steps = model.build_steps(list(rules.values()), receivers)
    exact = model.weights([{"step": w["step"], "receiver_member": w["receiver_member"],
                            "driver_value": w["driver_value"]} for w in stored])
    return model.coefficient_rows(run_name, steps, exact, method)
