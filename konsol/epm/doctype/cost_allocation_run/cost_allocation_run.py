"""Cost Allocation Run (konsol#269).

K7: Compute Weights, draft only. It reads the Active rule set, the submitted
statistic values for the run's entity, scenario and fiscal year, and the
account tree, then snapshots everything the build needs onto the run. A run
that cannot be computed saves nothing: the refusal lists every missing value.

The frappe reads are small helpers so host tests can stub them; the row
shaping is pure. The submit/cancel gates (K8) come later.
"""
import frappe
from frappe.model.document import Document

from konsol import cost_allocation_model as model


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
    if run.docstatus != 0:
        frappe.throw("Compute Weights works only on a draft run.")
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


class CostAllocationRun(Document):
    """Submit/cancel gates arrive with K8."""
