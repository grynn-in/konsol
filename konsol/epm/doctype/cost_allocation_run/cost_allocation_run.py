"""Cost Allocation Run (konsol#269).

K7: Compute Weights, draft only. It reads the Active rule set, the submitted
statistic values for the run's entity, scenario and fiscal year, and the
account tree, then snapshots everything the build needs onto the run. A run
that cannot be computed saves nothing: the refusal lists every missing value.

The frappe reads are small helpers so host tests can stub them; the row
shaping is pure.

K8: submit is the approval. The gates run in before_submit / before_cancel and
the warehouse sync is queued after the commit (header, accounts, weights, and
the coefficients table rebuilt from every submitted run).
"""
import re
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
    if run.docstatus != 0 or run.status != "Draft":
        frappe.throw("Compute Weights works only on a run in status Draft: an approver "
                     "must not approve weights that changed after review.")
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

    def before_submit(self):
        """Submit is the approval. The period must be declared on every
        scenario. The closed-period gate on submit applies to an actual
        scenario only: that skip for budget and forecast runs is the build's
        own extension and awaits a decision (konsol#269 review, finding 6)."""
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
        """Cancel only while the period is open, and only for an actual scenario (Q4)."""
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
        """Header and children, then the coefficients, once, after the commit."""
        clickhouse.sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        for doctype, controller in self.CHILD_CONTROLLERS.items():
            clickhouse.sync_doctype_after_commit(
                doctype, controller.CH_TABLE, controller.CH_FIELD_MAP)
        clickhouse.after_commit_once(("cost_allocation_run_coefficients",), type(self).resync_staging)

    @classmethod
    def resync_staging(cls, force=False):
        """Rebuild epm_staging.cost_allocation_coefficients from every submitted
        run (TRUNCATE+INSERT).

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
