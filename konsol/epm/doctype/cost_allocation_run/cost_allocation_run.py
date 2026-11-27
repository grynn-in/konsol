"""Cost Allocation Run (konsol#269).

K7: Compute Weights, draft only. It reads the Active rule set, the submitted
statistic values for the run's entity, scenario and fiscal year, and the
account tree, then snapshots everything the build needs onto the run. A run
that cannot be computed saves nothing: the refusal lists every missing value.

The frappe reads are small helpers so host tests can stub them; the row
shaping is pure.

K8: submit is the approval. The gates run in before_submit / before_cancel and
the warehouse sync is queued after the commit (header, accounts, weights).
The coefficients table is written per run after the commit (RF10, option B,
Deepak Pai, 17 Nov 2026: _sync_run_coefficients); reconcile rebuilds it
whole (resync_staging), and an hourly check repairs any run out of step
(check_coefficients_staging).

2b-1 (F7, F8; Deepak Pai, 27 Nov 2026, konsol#269 Phase 2b plan section 1, A
on Q1-Q7): Compute fills the same weight rows from each driver's source.
Uploaded Statistic rows are unchanged; Fixed Weights rows take the window
covering the run's declared period; Account Set rows sum the scenario trial
balance of the driver's accounts (and descendants) per receiver, through the
same ClickHouse call as the sender-pool preview, and the run snapshots the
accounts it read in driver_accounts: one row per expanded account with the
declared account it came from (2b-1 DA), so the warehouse drift test, which
has no account tree, sums by main_account. Every source's values are snapshotted
at Compute and re-checked at submit (_refuse_stale_driver_values, beside
_refuse_stale_statistics); drift after approval is a warehouse warning.
"""
import datetime
import functools
import json
import re
from decimal import Decimal
from fractions import Fraction

import frappe
from frappe.model.document import Document

from konsol import clickhouse, period_status
from konsol import cost_allocation_model as model
from konsol.workflows import set_cancelled_state
from konsol.epm.doctype.cost_allocation_run_account.cost_allocation_run_account import (
    CostAllocationRunAccount,
)
from konsol.epm.doctype.cost_allocation_run_driver_account.cost_allocation_run_driver_account import (
    CostAllocationRunDriverAccount,
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


def _driver_sources(drivers):
    """{driver: row} for the named drivers: source, dimension, value_sign and
    the Fixed Weights and Account Set child rows (empty lists where unused)."""
    names = sorted(drivers)
    if not names:
        return {}
    out = {}
    for r in frappe.get_all("Cost Allocation Driver", filters={"name": ["in", names]},
                            fields=["name", "source", "dimension", "value_sign"], order_by="name"):
        out[r["name"]] = {**r, "fixed_weights": [], "accounts": []}
    for r in frappe.get_all("Cost Allocation Driver Fixed Weight",
                            filters={"parent": ["in", names]},
                            fields=["parent", "member", "value", "effective_from", "effective_to"],
                            parent_doctype="Cost Allocation Driver", order_by="parent, idx",
                            limit_page_length=0):
        if r["parent"] in out:
            out[r["parent"]]["fixed_weights"].append(r)
    for r in frappe.get_all("Cost Allocation Driver Account", filters={"parent": ["in", names]},
                            fields=["parent", "main_account"],
                            parent_doctype="Cost Allocation Driver", order_by="parent, idx",
                            limit_page_length=0):
        if r["parent"] in out:
            out[r["parent"]]["accounts"].append(r)
    return out


def _period_dates(fiscal_year, fiscal_period):
    """(start, end) of the run's declared period. Fixed Weights windows are
    matched against these, never against month arithmetic; an undeclared
    period refuses here with period_status's message."""
    return period_status.period_dates(fiscal_year, fiscal_period)


def _query_derived(sql, params):
    """Run the Account Set query; its TabSeparated text. The same ClickHouse
    call as the sender-pool preview (_query_sender_pools)."""
    return clickhouse.execute(sql, params)


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
    """Plain dicts from child Documents or query rows. frappe._dict answers any
    attribute with None, so hasattr(row, "as_dict") is True for get_all rows:
    test that it is callable (CA-2B1 BUG-2)."""
    return [r.as_dict() if callable(getattr(r, "as_dict", None)) else dict(r) for r in table or []]


def _date(value):
    """A date from what Frappe hands a Date field: a date, a datetime or an
    ISO string; None when blank."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


# --- pure shaping ---------------------------------------------------------------

def value_maps(stat_rows):
    """({(driver, period, member): value}, {(driver, period, member): statistic})."""
    values, names = {}, {}
    for r in stat_rows:
        key = (r["driver"], r["fiscal_period"], r["member"])
        values[key] = r["value"]
        names[key] = r["statistic"]
    return values, names


def weight_rows(resolved, step_weights):
    """Child rows for Cost Allocation Run Weight from resolved driver values.
    Each resolved row says where its value came from (statistic, source,
    effective_from); the weight is the step's exact share as a float."""
    return [{
        "step": r["step"], "sender_member": r["sender_member"],
        "receiver_member": r["receiver_member"], "driver": r["driver"],
        "statistic": r.get("statistic"), "source": r.get("source") or model.UPLOADED,
        "effective_from": r.get("effective_from"),
        "value_period": r["value_period"], "carried_forward": 1 if r["carried_forward"] else 0,
        "driver_value": r["driver_value"],
        "weight": float(step_weights[(r["step"], r["receiver_member"])]),
    } for r in resolved]


def parse_sums(text):
    """{member: (Decimal total, row_count)} from the derived query's
    TabSeparated rows (member, total, row_count)."""
    out = {}
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        member, total, count = line.split("\t")
        out[member] = (Decimal(total), int(count))
    return out


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
    resolved, driver_accounts = _resolve_sources(run, rs, steps)
    try:
        step_weights = model.weights(resolved)
    except model.CostAllocationError as exc:
        frappe.throw(_PREFIX + "<br>".join(exc.errors))

    weights = weight_rows(resolved, step_weights)
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
    run.set("driver_accounts", driver_accounts)
    run.save()
    return {"carried_forward_count": carried, "weights": len(weights)}


def _resolve_sources(run, rs, steps):
    """One resolved row per step x receiver, filled per its driver's source
    (2b-1 F7). Returns (rows in step order, the driver_accounts rows).

    Uploaded Statistic steps go through resolve_driver_values together, as
    before. A Fixed Weights step takes each receiver's window covering the
    whole declared period (fixed_windows). An Account Set driver is read from
    the warehouse once, over the receivers of every step that uses it, and
    each step takes its receivers' values (derived_values); the run snapshots
    one driver_accounts row per EXPANDED account with the declared account it
    came from (2b-1 DA; a leaf declared directly names itself). Where two
    declarations overlap, the first in driver order keeps the account, and
    the derived query receives each account once. Every refusal from
    the pure functions is collected and thrown once with the run's prefix,
    and nothing is saved. A warehouse that cannot be read refuses at once,
    naming the driver: never a silent zero."""
    used = sorted({s.driver for s in steps})
    sources = _driver_sources(used)
    errors, source_of = [], {}
    for name in used:
        info = sources.get(name)
        if info is None:
            errors.append(f"Driver {name} does not exist.")
        elif info["source"] not in model.SOURCES:
            errors.append(f"Driver {name} has source {info['source']!r}, "
                          f"not one of {', '.join(model.SOURCES)}.")
        elif info["dimension"] != rs.allocation_dimension:
            # The rule set refuses this at its own save (model.validate_rule_set);
            # a driver edited since must not slip through Compute (rv1 F3).
            errors.append(f"Driver {name} is on dimension {info['dimension']}, not "
                          f"{rs.allocation_dimension} (the rule set's allocation dimension).")
        else:
            source_of[name] = info["source"]

    stat_steps = [s for s in steps if source_of.get(s.driver) == model.UPLOADED]
    stat_rows = {}
    if stat_steps:
        values, names = value_maps(
            _statistic_values(rs.entity, run.scenario, run.fiscal_year, run.fiscal_period))
        carry = _driver_carry_forward(sorted({s.driver for s in stat_steps}))
        try:
            for r in model.resolve_driver_values(stat_steps, run.fiscal_period, values, carry):
                r["statistic"] = names[(r["driver"], r["value_period"], r["receiver_member"])]
                r["source"], r["effective_from"] = model.UPLOADED, None
                stat_rows.setdefault(r["step"], []).append(r)
        except model.CostAllocationError as exc:
            errors.extend(exc.errors)

    sums_of, driver_accounts = {}, []
    for name in [n for n in used if source_of.get(n) == model.ACCOUNT_SET]:
        info = sources[name]
        declared = [r["main_account"] for r in _rows(info["accounts"])]
        members = sorted({m for st in steps if st.driver == name for m in st.receivers})
        expanded = account_rows(declared, _descendants)
        driver_accounts += [{"driver": name, "main_account": r["main_account"],
                             "declared_account": r["declared_account"],
                             "value_sign": info["value_sign"]} for r in expanded]
        try:
            sql, params = model.derived_query(
                rs.entity, run.scenario, run.fiscal_year, run.fiscal_period,
                info["dimension"], members, [r["main_account"] for r in expanded])
        except model.CostAllocationError as exc:
            errors.extend(f"Driver {name}: {e}" for e in exc.errors)
            continue
        try:
            sums_of[name] = parse_sums(_query_derived(sql, params))
        except Exception as exc:  # noqa: BLE001 — any warehouse failure refuses; never a silent zero
            frappe.throw(f"{_PREFIX}the scenario trial balance could not be read for driver "
                         f"{name}: {exc}. Nothing was saved.")

    dates, rows = None, []
    for st in steps:
        source = source_of.get(st.driver)
        if source == model.UPLOADED:
            rows.extend(stat_rows.get(st.step, []))
            continue
        if source is None or (source == model.ACCOUNT_SET and st.driver not in sums_of):
            continue   # already refused above
        base = {"step": st.step, "sender_member": st.sender_member, "driver": st.driver,
                "value_period": run.fiscal_period, "carried_forward": False,
                "statistic": None, "source": source, "effective_from": None}
        try:
            if source == model.FIXED:
                if dates is None:
                    dates = tuple(_date(d) for d in _period_dates(run.fiscal_year, run.fiscal_period))
                windows = model.fixed_windows(
                    _fixed_rows(sources[st.driver]["fixed_weights"]), st.receivers, *dates)
                rows += [{**base, "receiver_member": m, "driver_value": windows[m]["value"],
                          "effective_from": windows[m]["effective_from"]} for m in st.receivers]
            else:
                vals = model.derived_values(sums_of[st.driver], st.receivers,
                                            sources[st.driver]["value_sign"])
                rows += [{**base, "receiver_member": m, "driver_value": float(vals[m])}
                         for m in st.receivers]
        except model.CostAllocationError as exc:
            errors.extend(f"Step {st.step} (driver {st.driver}): {e}" for e in exc.errors)
    errors.extend(_too_large(r) for r in rows if abs(float(r["driver_value"] or 0)) >= DRIVER_VALUE_BOUND)
    if errors:
        frappe.throw(_PREFIX + "<br>".join(errors))
    return rows, driver_accounts


# Run Weight.driver_value is a Float: decimal(21,9), 12 integer digits. Every
# source's value lands in that column, so the bound applies to all of them at
# Compute, before anything is saved (rv1 F2).
DRIVER_VALUE_BOUND = 10 ** 12


def _too_large(row):
    return (f"Driver {row['driver']}, {row['receiver_member']}: value {_text(row['driver_value'])} "
            "is too large for a driver value (12 integer digits at most); use a smaller unit, "
            "e.g. thousands, by scaling the accounts' meaning, or a Fixed Weights driver.")


def _fixed_rows(table):
    """A driver's fixed-weight rows with their dates as dates, for fixed_windows."""
    return [{"member": r["member"], "value": r["value"],
             "effective_from": _date(r["effective_from"]), "effective_to": _date(r.get("effective_to"))}
            for r in _rows(table)]


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

    The pool reads gold_scenario_trial_balance by scenario_id. For a forecast
    scenario that table holds the DECLARED current round's rows only (konsol#348
    S6-Q1 b: the Current Forecast of the scenario and year), so a forecast pool
    is the current round's, never the sum of the year's approved rounds; a year
    with no declaration has no rows and its pool is empty.

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
        "Cost Allocation Run Driver Account": CostAllocationRunDriverAccount,
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
        self.set("driver_accounts", [])
        for field in ("computed_at", "computed_by", "computed_for_rule_set",
                      "computed_for_scenario", "computed_for_fiscal_year",
                      "computed_for_fiscal_period", "rule_set_modified"):
            setattr(self, field, None)
        self.values_carried_forward = 0
        self.coefficient_row_count = 0

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
        self._refuse_stale_driver_values(_rows(weights))
        # RF11: the hourly check compares staging with this count, SQL only.
        # before_submit runs before docstatus 1 is saved: not an update-after-submit.
        self.coefficient_row_count = len(self._refuse_unbuildable_coefficients(_rows(weights)))
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
        still be submitted and hold the value the weight was computed from.
        Rows from another source cite no statistic; _refuse_stale_driver_values
        checks those."""
        weights = [w for w in weights if w.get("statistic")]
        cited = sorted({w["statistic"] for w in weights})
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

    def _refuse_stale_driver_values(self, weights):
        """Fixed Weights and Account Set rows hold values read at Compute (2b-1
        F8; Q3 A: snapshotted at Compute, re-checked at approval). Every driver
        the run uses is re-read FOR UPDATE: its source must still be the one
        its rows were computed from (rows written before 2b-1 carry no source
        and are Uploaded Statistic rows). A Fixed Weights driver's rows are
        re-read FOR UPDATE and resolved again for the period; an Account Set
        declared accounts and sign must match the run's driver_accounts
        snapshot (its declared_account column, 2b-1 DA), then the derived
        query runs again. Any difference refuses,
        naming the member, the stored value and the current one; a warehouse
        that cannot be read refuses too, never passes."""
        by_driver = {}
        for w in weights:
            by_driver.setdefault(w["driver"], []).append(w)
        if not by_driver:
            return
        names = tuple(sorted(by_driver))
        current = {r["name"]: r for r in frappe.db.sql(
            "SELECT name, source, dimension, value_sign FROM `tabCost Allocation Driver` "
            "WHERE name IN %(drivers)s FOR UPDATE", {"drivers": names}, as_dict=True)}
        stale, fixed, derived = [], [], []
        for name in names:
            was = sorted({w.get("source") or model.UPLOADED for w in by_driver[name]})
            now = current.get(name)
            if now is None:
                stale.append(f"driver {name} no longer exists")
            elif was != [now["source"]]:
                stale.append(f"driver {name}: source changed from {', '.join(was)} to {now['source']}")
            elif now["dimension"] != self.allocation_dimension:
                # rv1 F3: the re-query would read another breakdown than the run
                # allocates on; the rule set's rule covers every source.
                stale.append(f"driver {name}: dimension is now {now['dimension']}, "
                             f"the run allocates on {self.allocation_dimension}")
            elif now["source"] == model.FIXED:
                fixed.append(name)
            elif now["source"] == model.ACCOUNT_SET:
                derived.append(name)
        if fixed:
            stale += self._stale_fixed_rows(fixed, by_driver)
        for name in derived:
            stale += self._stale_derived_rows(name, current[name], by_driver[name])
        if stale:
            frappe.throw(
                f"{_PREFIX}driver values changed after the weights were computed: "
                + "; ".join(stale) + ". Recompute the weights.")

    def _stale_fixed_rows(self, drivers, by_driver):
        """Locking read of the drivers' fixed-weight rows, resolved again for the
        run's period; the differences from the stored rows."""
        rows = frappe.db.sql(
            "SELECT parent, member, value, effective_from, effective_to "
            "FROM `tabCost Allocation Driver Fixed Weight` "
            "WHERE parent IN %(drivers)s AND parenttype = 'Cost Allocation Driver' FOR UPDATE",
            {"drivers": tuple(drivers)}, as_dict=True)
        start, end = (_date(d) for d in _period_dates(self.fiscal_year, self.fiscal_period))
        stale = []
        for name in drivers:
            mine = _fixed_rows([r for r in rows if r["parent"] == name])
            receivers = sorted({w["receiver_member"] for w in by_driver[name]})
            try:
                windows = model.fixed_windows(mine, receivers, start, end)
            except model.CostAllocationError as exc:
                stale.append(f"driver {name}: " + " ".join(exc.errors))
                continue
            for w in by_driver[name]:
                row, stored_from = windows[w["receiver_member"]], _date(w.get("effective_from"))
                if _stored(row["value"]) != _nine(w["driver_value"]) or row["effective_from"] != stored_from:
                    stale.append(
                        f"driver {name}, {w['receiver_member']}: stored {_text(w['driver_value'])} "
                        f"from {stored_from}, now {_text(row['value'])} from {row['effective_from']}")
        return stale

    def _stale_derived_rows(self, name, now, rows):
        """The driver's declared accounts and sign against the run's snapshot,
        then the derived query again; the differences from the stored rows.
        The snapshot holds one row per expanded account (2b-1 DA); approval
        expands the driver's accounts the same way and compares (main_account,
        declared_account, sign). A snapshot written before declared_account
        existed has none and refuses: recompute it.
        The accounts are read FOR UPDATE like the fixed rows: under REPEATABLE
        READ a plain read may see a snapshot older than the header's (rv1 F1)."""
        accounts = frappe.db.sql(
            "SELECT main_account FROM `tabCost Allocation Driver Account` "
            "WHERE parent = %(driver)s AND parenttype = 'Cost Allocation Driver' "
            "ORDER BY idx FOR UPDATE", {"driver": name}, pluck=True)
        # Expand exactly as Compute did and compare the expanded rows, so a
        # group declared beside its own leaf passes and a tree change since
        # Compute refuses (review of konsolidat#403, F1/F2).
        expanded = account_rows(accounts, _descendants)
        current = {(r["main_account"], r["declared_account"], now["value_sign"]) for r in expanded}
        snapshot = {(r["main_account"], r.get("declared_account"), r["value_sign"])
                    for r in _rows(self.get("driver_accounts")) if r["driver"] == name}
        if current != snapshot:
            return [f"driver {name}: its accounts or value sign changed since Compute"]
        members = sorted({w["receiver_member"] for w in rows})
        try:
            sql, params = model.derived_query(
                self.entity, self.scenario, self.fiscal_year, self.fiscal_period,
                now["dimension"], members, [r["main_account"] for r in expanded])
        except model.CostAllocationError as exc:
            return [f"driver {name}: " + " ".join(exc.errors)]
        try:
            sums = parse_sums(_query_derived(sql, params))
        except Exception as exc:  # noqa: BLE001 — a warehouse that cannot be read refuses, never passes
            frappe.throw(f"{_PREFIX}the scenario trial balance could not be read to check driver "
                         f"{name}: {exc}. Try again when the warehouse answers.")
        try:
            vals = model.derived_values(sums, members, now["value_sign"])
        except model.CostAllocationError as exc:
            return [f"driver {name}: " + " ".join(exc.errors)]
        return [f"driver {name}, {w['receiver_member']}: stored {_text(w['driver_value'])}, "
                f"now {_text(vals[w['receiver_member']])}"
                for w in rows if _stored(vals[w["receiver_member"]]) != _nine(w["driver_value"])]

    def _refuse_unbuildable_coefficients(self, weights):
        """Build the coefficient rows now, so an invalid run is refused at submit
        and cannot stop the coefficients sync for every other run. Returns them."""
        try:
            return _coefficient_rows(self.name, self.method, weights)
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
        if scenario == "actual":
            period_status.assert_open(self.fiscal_year, self.fiscal_period,
                                      action=f"cancel cost allocation run {self.name}")
        # After the gate: the installed workflow's docstatus-2 state (#529).
        set_cancelled_state(self)

    # -- warehouse -----------------------------------------------------------

    def on_submit(self):
        self._sync()

    def on_cancel(self):
        self._sync()

    def after_delete(self):
        """after_delete, not on_trash (#120). Only a cancelled run can have
        warehouse rows; a deleted draft has none, so nothing is synced and no
        build is requested (RF11 F5)."""
        if self.docstatus == 2:
            self._sync()

    def _sync(self):
        """Header and children after the commit; the run's own coefficient
        rows once per transaction after the commit (RF10)."""
        clickhouse.sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        for doctype, controller in self.CHILD_CONTROLLERS.items():
            clickhouse.sync_doctype_after_commit(
                doctype, controller.CH_TABLE, controller.CH_FIELD_MAP)
        clickhouse.after_commit_once(("cost_allocation_coefficients", self.name),
                                     functools.partial(_sync_run_coefficients, self.name))

    @classmethod
    def resync_staging(cls, force=False):
        """reconcile_all's entry: rebuild epm_staging.cost_allocation_coefficients
        from every submitted run, synchronously (TRUNCATE+INSERT through
        sync_table, which stamps the reconcile run id on the watermark). It
        requests no build. Returns the rows written (None when an unforced
        sync failed).

        The exact weights are rebuilt from the stored ``driver_value`` column
        with ``model.weights()``, which turns each value into a Fraction through
        str(), so 0.1 is the decimal 0.1. The float ``weight`` column is never
        read: a float cannot hold 1/3 and its steps would not sum to exactly 1,
        which the coefficient rounding refuses.
        """
        runs = _submitted_coefficients()
        columns = list(model.COEFFICIENT_COLUMNS)
        rows = [[r[c] for c in columns] for _run, run_rows in runs for r in run_rows]
        return clickhouse.sync_table(
            cls.CH_STAGING_TABLE, columns, rows,
            source_max_modified=_stamp_time([run for run, _rows in runs]), force=force)


# -- coefficient writes per run (RF10) ------------------------------------------
#
# Each submit, cancel or delete writes ITS OWN run's rows after the commit
# (Deepak Pai, 17 Nov 2026, PR #541 option B; rejected: A, the background
# resync job with its marker, lock and timeouts). Frappe's commit runs COMMIT,
# then START TRANSACTION, then the after-commit callbacks
# (frappe/database/database.py, Database.commit), so the callback's first read
# opens a fresh REPEATABLE READ snapshot that holds this commit.
#
# Residual windows. There is no lock: each is repaired by the hourly
# check_coefficients_staging, and none publishes wrong numbers.
# - Two writers for the same run interleave. Each runs DELETE then INSERT
#   from its own committed read. Doubling needs two writers that both read
#   the run as submitted: the after-commit callback and the hourly repair. If
#   both DELETEs land before either INSERT, both INSERTs land and the run's
#   rows are doubled. The allocation build then fails
#   assert_cost_allocation_coefficients_close (a sender's coefficients no
#   longer sum to its pool), so nothing is published, and the next hourly
#   check sees the wrong count and repairs the run. A submit's callback and a
#   fast cancel's cannot double the rows (the cancel reads the run as
#   cancelled): if the submit's INSERT lands after the cancel's DELETE, the
#   cancelled run's rows stay until the next hourly check removes them.
# - A reconcile rebuild races a per-run write: the rebuild swaps in the
#   snapshot it read, which can drop a per-run write made after that read (or
#   keep rows the per-run write deleted). The hourly check repairs the run.

BUILD_REQUEST = "konsol.tasks.request_consolidation_build"
#: more runs than this out of step after the re-check: rebuild the whole
#: table once (resync_staging) instead of repairing run by run
COEFFICIENT_REBUILD_THRESHOLD = 20


def _submitted_coefficients(names=None):
    """[(run, its coefficient rows)] for every submitted run, or for the
    submitted runs among ``names``, in name order. Pure reads and the model:
    it writes nothing. A run the model refuses raises with its name."""
    filters = {"docstatus": 1}
    if names is not None:
        filters["name"] = ["in", list(names)]
    runs = frappe.get_all("Cost Allocation Run", filters=filters,
                          fields=["name", "method", "modified"], order_by="name",
                          limit_page_length=0)
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
    out = []
    for run in runs:
        try:
            out.append((run, _coefficient_rows(run["name"], run["method"],
                                               by_run.get(run["name"], []))))
        except model.CostAllocationError as exc:
            frappe.throw(f"{_PREFIX}{run['name']}: " + "<br>".join(exc.errors))
    return out


def _stamp_time(runs):
    """The newest ``modified`` of ``runs``, as the watermark stores it."""
    modified = [r["modified"] for r in runs if r.get("modified")]
    return max(modified).strftime("%Y-%m-%d %H:%M:%S") if modified else None


def _json_value(value):
    return str(value) if isinstance(value, Decimal) else value


def _replace_run_rows(run_name, rows):
    """Delete the run's rows, then insert ``rows``. Raises on any ClickHouse failure.
    Idempotent: run twice, it leaves the same rows.

    The delete is a lightweight DELETE with lightweight_deletes_sync = 2, so
    it returns only once the rows are masked (ClickHouse 24.8). The run name
    is a query parameter, and the rows travel as JSONEachRow data, so no
    value is formatted into the SQL. One INSERT is one block, so a run's rows
    land together."""
    table = CostAllocationRun.CH_STAGING_TABLE
    clickhouse.execute(
        f"DELETE FROM {table} WHERE run = {{run:String}} SETTINGS lightweight_deletes_sync = 2",
        {"param_run": run_name})
    if rows:
        columns = list(model.COEFFICIENT_COLUMNS)
        body = "\n".join(json.dumps({c: _json_value(r[c]) for c in columns}) for r in rows)
        clickhouse.execute(f"INSERT INTO {table} ({', '.join(columns)}) FORMAT JSONEachRow\n{body}")


def _sync_run_coefficients(run_name, errors=None):
    """After-commit callback of CostAllocationRun._sync, and the hourly
    check's repair. Reads the run's committed state: submitted, it replaces
    the run's coefficient rows; cancelled, deleted or missing, it deletes
    them. After a successful write it always requests the allocation build,
    naming the run (RF11 F3).

    Guarded: a failed write is logged to file and as a deferred Error Log
    naming the run (an insert made after the request's commit would never be
    committed), nothing is raised out of the commit, and no build is
    requested; the hourly check repairs the run. The table count and the
    watermark stamp after the write are best-effort: a failure there is a
    file-log warning and the build is still requested. Returns the run's rows
    written, or None when the write failed; then ``errors`` (a dict, if
    given) gets the reason under the run's name."""
    try:
        found = _submitted_coefficients([run_name])
        run, rows = found[0] if found else (None, [])
        _replace_run_rows(run_name, rows)
    except Exception as exc:  # noqa: BLE001 — never raise out of a commit
        if errors is not None:
            errors[run_name] = str(exc).replace(f"{_PREFIX}{run_name}: ", "", 1)
        _log_failure(run_name, "Cost Allocation coefficients not written",
                     f"Run {run_name}: its coefficient rows could not be written to staging; "
                     "no build requested. The hourly check repairs it.")
        return None
    _stamp_after_write(run_name, run)
    _request_build(run_name)
    return len(rows)


def _stamp_after_write(run_name, run):
    """Stamp the watermark with the table's row count. Best-effort: the rows
    are already written, so a failure only warns; the next write or
    reconcile stamps it."""
    table = CostAllocationRun.CH_STAGING_TABLE
    try:
        total = int(clickhouse.execute(f"SELECT count() FROM {table}") or 0)
        clickhouse._stamp_watermark(table, total, _stamp_time([run] if run else []))
    except Exception:  # noqa: BLE001 — the write succeeded; the build still goes
        frappe.logger().warning(
            f"Run {run_name}: coefficients written, but the watermark of {table} "
            "was not stamped.", exc_info=True)


def _request_build(run_name):
    """Queue the allocation build request naming the run. Already after the
    commit, so no enqueue_after_commit; no deduplicate, which would drop this
    request behind a started job with the same id. The job's build request
    debounces the builds themselves (konsol.tasks). A failure is logged with
    the call to run by hand."""
    try:
        frappe.enqueue(BUILD_REQUEST, queue="default", doctype="Cost Allocation Run",
                       name=run_name, trigger_method="coefficients_sync")
    except Exception:  # noqa: BLE001 — never raise out of a commit
        _log_failure(run_name, "Cost Allocation build not requested",
                     f"Run {run_name}: the coefficients were written, but the allocation "
                     "build could not be requested. Request it by hand: "
                     f'{BUILD_REQUEST}("Cost Allocation Run", "{run_name}", '
                     '"coefficients_sync")')


def _log_failure(run_name, title, message):
    frappe.logger().error(message, exc_info=True)
    try:
        frappe.log_error(title=title, message=message, defer_insert=True)
    except Exception:  # noqa: BLE001 — Redis down too: the file log has it
        pass


def _held_counts():
    """{run: rows} in staging. Raises when ClickHouse is unreachable."""
    out = clickhouse.execute(
        f"SELECT run, count() FROM {CostAllocationRun.CH_STAGING_TABLE} "
        "GROUP BY run FORMAT TSV")
    held = {}
    for line in (out or "").splitlines():
        if line:
            run, count = line.rsplit("\t", 1)
            held[run] = int(count)
    return held


def _expected_counts(runs, failed, corrections=None):
    """{run: expected rows} for submitted ``runs`` (name, coefficient_row_count).
    The stored count is used as is. A run submitted before the field existed
    (0 or NULL) is counted through the model, one run at a time: a run the
    model refuses goes into ``failed`` with its reason, and the rest go on.
    The model's count for such a run goes into ``corrections`` {run: count},
    to be stored once, so the model path runs once per legacy run (RF12 R1)."""
    expected = {}
    for r in runs:
        name, count = r["name"], r.get("coefficient_row_count")
        if not count:
            try:
                found = _submitted_coefficients([name])
                count = len(found[0][1]) if found else 0
                if corrections is not None and count:
                    corrections[name] = count
            except Exception as exc:  # noqa: BLE001 — one run must not stop the check
                failed[name] = str(exc).replace(f"{_PREFIX}{name}: ", "", 1)
                continue
        expected[name] = int(count)
    return expected


def _submitted_counts(failed, names=None, corrections=None, current=None):
    filters = {"docstatus": 1}
    if names is not None:
        filters["name"] = ["in", list(names)]
    runs = frappe.get_all("Cost Allocation Run", filters=filters,
                          fields=["name", "coefficient_row_count"], order_by="name",
                          limit_page_length=0)
    if current is not None:
        current.update({r["name"]: int(r.get("coefficient_row_count") or 0) for r in runs})
    return _expected_counts(runs, failed, corrections)


def _out_of_step(runs, expected, held, failed):
    return sorted(r for r in runs if r not in failed and expected.get(r, 0) != held.get(r, 0))


def check_coefficients_staging():
    """Hourly safety net (RF9-RF12), run by the scheduler only. SQL only:
    compares each submitted run's stored coefficient_row_count with
    ``SELECT run, count() ... GROUP BY run`` in staging. The model runs only
    for a run submitted before that field existed, and then once: its count
    is stored.

    A mismatch is never repaired from the first read: under REPEATABLE READ
    it is a snapshot, and a run approved or cancelled since has had its rows
    rewritten by its own after-commit callback. So the check rolls back, then
    re-reads each mismatched run's docstatus and stored count and its
    ClickHouse count, and repairs only the runs still out of step (F1). More
    than COEFFICIENT_REBUILD_THRESHOLD of those: one full rebuild
    (resync_staging) and one build request instead; if the rebuild fails,
    the runs are repaired one by one (R2). Each repair requests its own
    run's build, and a stored count that differs from the rows written is
    corrected (R1). One Error Log line lists what was repaired and what was
    not. On a match it does nothing. An unreachable ClickHouse is a file-log
    warning only: the next hour checks again.

    Commit: the corrected counts are written with set_value and are not
    committed here. The scheduler runs this as a background job, and
    frappe's execute_job commits when the job returns (utils/background_jobs.py),
    as konsol's other scheduled jobs rely on. This file takes no commit call
    at all (test_lifecycle_no_commit_and_no_save_in_the_hooks)."""
    failed = {}
    corrections, stored = {}, {}
    expected = _submitted_counts(failed, corrections=corrections, current=stored)
    try:
        held = _held_counts()
    except Exception:  # noqa: BLE001 — unreachable ClickHouse: check again next hour
        frappe.logger().warning("Cost Allocation coefficients check skipped", exc_info=True)
        return
    mismatched = _out_of_step(set(expected) | set(held), expected, held, failed)
    if mismatched:
        # End the snapshot: the re-reads below see every commit made since.
        frappe.db.rollback()
        # Keep the first read's legacy-count corrections for the runs that
        # matched; only the mismatched runs are re-read (tester CA-1 bug 1:
        # dropping them ran the model path every hour while any run mismatched).
        corrections = {r: n for r, n in corrections.items() if r not in mismatched}
        stored = {r: n for r, n in stored.items() if r not in mismatched}
        expected = _submitted_counts(failed, mismatched, corrections, stored)
        try:
            held = _held_counts()
        except Exception:  # noqa: BLE001
            frappe.logger().warning("Cost Allocation coefficients check skipped", exc_info=True)
            return
        mismatched = _out_of_step(mismatched, expected, held, failed)
    not_repaired = [f"{run}: {reason}" for run, reason in sorted(failed.items())]
    rebuild_failed = None
    if len(mismatched) > COEFFICIENT_REBUILD_THRESHOLD:
        rebuild_failed = _rebuild_all(mismatched, not_repaired)
        if rebuild_failed is None:
            return
    repaired = []
    for run in mismatched:
        counts = f"(expected {expected.get(run, 0)}, held {held.get(run, 0)}"
        errors = {}
        try:
            written = _sync_run_coefficients(run, errors)
        except Exception as exc:  # noqa: BLE001 — one run must not stop the others
            written, errors[run] = None, str(exc)
        if written is None:
            not_repaired.append(f"{run}: {errors.get(run, 'write failed')} {counts})")
            continue
        note = ""
        if run in stored and stored[run] != written:
            corrections[run] = written
            note = f"; stored count {stored[run]} -> {written}"
        repaired.append(f"{run} {counts}{note})")
    _store_counts(corrections, stored)
    if repaired or not_repaired or rebuild_failed:
        lead = f"The full rebuild failed ({rebuild_failed}); repaired run by run. " if rebuild_failed else ""
        frappe.log_error(
            title="Cost Allocation coefficients out of step with submitted runs",
            message=(f"{lead}Repaired: {', '.join(repaired) or 'none'}; "
                     f"not repaired: {'; '.join(not_repaired) or 'none'}."))


def _store_counts(corrections, stored):
    """Write each corrected coefficient_row_count (no modified bump: the
    watermark follows ``modified``). The caller's job commits."""
    for run, n in sorted(corrections.items()):
        if stored.get(run) != n:
            frappe.db.set_value("Cost Allocation Run", run, "coefficient_row_count", n,
                                update_modified=False)


def _backfill_counts():
    """After a full rebuild: set each submitted run's stored count to the rows
    the rebuild wrote for it (ClickHouse GROUP BY run). Best-effort."""
    try:
        held = _held_counts()
        runs = frappe.get_all("Cost Allocation Run", filters={"docstatus": 1},
                              fields=["name", "coefficient_row_count"], order_by="name",
                              limit_page_length=0)
        stored = {r["name"]: int(r.get("coefficient_row_count") or 0) for r in runs}
        _store_counts({run: held.get(run, 0) for run in stored}, stored)
    except Exception:  # noqa: BLE001 — counts self-correct on a later check
        frappe.logger().warning("Cost Allocation coefficient counts not back-filled",
                                exc_info=True)


def _rebuild_all(mismatched, not_repaired):
    """Too many runs out of step to repair one by one: rebuild the table from
    every submitted run once, log one line, request one build and back-fill
    the stored counts. Returns None when done. When the rebuild fails it
    returns the reason, and the caller repairs run by run."""
    try:
        written = CostAllocationRun.resync_staging(force=True)
    except Exception as exc:  # noqa: BLE001 — fall back to per-run repair
        frappe.logger().error("Cost Allocation coefficients rebuild failed", exc_info=True)
        return str(exc) or type(exc).__name__
    _backfill_counts()
    frappe.log_error(
        title="Cost Allocation coefficients out of step with submitted runs",
        message=(f"{len(mismatched)} runs out of step (more than "
                 f"{COEFFICIENT_REBUILD_THRESHOLD}): rebuilt the whole table, {written} rows "
                 f"({', '.join(mismatched)}); not repaired: "
                 f"{'; '.join(not_repaired) or 'none'}."))
    _request_build(mismatched[0])
    return None


def _nine(value):
    """A driver value at the 9 places both the statistic and the weight store.
    A Fraction (from the model's derived values) is taken exactly."""
    if isinstance(value, Fraction):
        value = Decimal(value.numerator) / Decimal(value.denominator)
    return Decimal(str(value or 0)).quantize(Decimal("0.000000001"))


def _stored(value):
    """A current value as the Float column would store it: through a double,
    then 9 places. The approval check compares what WAS stored with what WOULD
    be stored now; near 1e12 a double cannot hold 4 places, so comparing the
    exact Fraction with the stored float refused every unchanged run (rv1 F2)."""
    return _nine(float(value or 0))


def _text(value):
    """A driver value for a message: 9 places, trailing zeros dropped."""
    return format(_nine(value), "f").rstrip("0").rstrip(".") or "0"


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
