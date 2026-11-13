"""Budget intake: the ONE entry every budget writer calls (konsol#348 V1).

The upload (U1/U2), the Excel cell save (X1), a Desk edit of a Budget Entry
(T3) and every later writer hand their rows to ``plan`` as a table (a header
plus rows, the budget file contract in konsol.budget_bulk_model) and write
only what the plan says is ready. ``plan`` itself writes nothing.

What it does, in order:

1. Splits the table (budget_bulk_model.split_table) into one group per
   Budget Entry key (scenario, fiscal_year, entity, layer, period). A
   structural problem raises ValueError listing every one, as the trial
   balance reader does.
2. Gathers the facts once: the group chart, the declared periods, each
   entity's functional currency and its minor unit, the declared Budget
   Layers, the active Scenarios and the Budget Cycles. Every list read states
   its order (Frappe v16 sorts by creation otherwise).
3. Runs budget_bulk_model.check_group on each group, then adds:
   - the Cycle: a (scenario, year) with no Budget Cycle is refused, naming the
     Cycle to create; nothing is created here (Q6-b). A Cycle that is not a
     draft (docstatus 0) is refused. Pending Approval is V2's rule.
   - duplicate rows: the same account and dim_* values twice in one group
     (one entity, period and layer) is refused, naming both lines. T1 refuses
     the same on save; this names the file lines.
   - access: konsol.api._assert_budget_write_access on every row (entity,
     account, dimensions). A refused row refuses its group, naming its line.
   - the layer's writer_role: a layer that names one takes rows only from its
     holders. System Manager and Administrator bypass, as the access check does.

An inactive Scenario is not gathered, so it is refused as an undeclared one.
Layer codes are compared exactly: ``Base`` is not ``base``.
"""
import frappe

from konsol import budget_bulk_model as M
from konsol.entity_permissions import BYPASS_ROLES
from konsol.group_chart import chart_accounts
from konsol.period_status import PeriodNotDeclared, period_row

#: Docstatus words for a Cycle that is not a draft.
_CYCLE_STATE = {1: "approved", 2: "cancelled"}


def plan(table, *, form_basis):
    """Judge a budget table for the current user. Returns

        {"groups": {BudgetKey: [rows]}, "report": [item per group], "ok": bool}

    ``groups`` is in file order. Each report item is budget_bulk_model's
    check_group row {scenario, fiscal_year, entity, layer, fiscal_period, rows,
    errors, ok} plus ``cycle`` (the Budget Cycle's name, or None), ``currency``
    (the entity's functional currency) and ``amount_basis`` (resolved from the
    file or `form_basis`, '' when none). A group with any error is not ready;
    a writer writes nothing for it.

    `form_basis` is the upload's Amount Basis ('' when it has none); it has no
    default, so a writer states what it means.
    """
    groups = M.split_table(table, _budget_dimensions())
    facts = _facts(groups)
    user = frappe.session.user
    roles = set(frappe.get_roles(user))
    bypass = user == "Administrator" or bool(BYPASS_ROLES & roles)
    from konsol.api import _assert_budget_write_access

    report = []
    for key, rows in groups.items():
        currency = facts["functional"].get(key.data_area_id, "")
        item = M.check_group(
            key, rows, period=facts["periods"].get((key.fiscal_year, key.fiscal_period)),
            layers=list(facts["layers"]), scenario=facts["scenarios"].get(key.scenario),
            chart=facts["chart"], functional_currency=currency,
            minor_unit=facts["minor_units"].get(currency), form_basis=form_basis)
        cycle, cycle_problem = _cycle(facts["cycles"].get((key.scenario, key.fiscal_year), ()), key)
        item["cycle"] = cycle
        item["currency"] = currency or ""
        basis, _ = M.resolve_basis(M.group_basis(rows), form_basis)
        item["amount_basis"] = basis or ""
        errors = item["errors"]
        if cycle_problem:
            errors.append(cycle_problem)
        errors.extend(_duplicates(rows))
        role = facts["layers"].get(key.layer)
        if role and not bypass and role not in roles:
            errors.append(f"Layer {key.layer} takes rows only from holders of the {role} role, and "
                          f"{user} does not hold it. Ask for the role, or write into another layer.")
        for row in rows:
            data = dict(row, data_area_id=key.data_area_id)
            try:
                _assert_budget_write_access(data)
            except frappe.PermissionError as error:
                errors.append(f"Line {row.get('line')}: {error}")
        item["ok"] = not errors
        report.append(item)
    return {"groups": groups, "report": report, "ok": all(i["ok"] for i in report)}


def _budget_dimensions():
    """The site's Dimension rows as the budget header judges them (in_budget).
    Every row, not only the accepted ones, so a refusal can say which of
    declare / publish / tick is missing (as konsol.tb_dimension explains)."""
    return frappe.get_all("Dimension", fields=["dimension_name", "status", M.DIMENSION_FLAG],
                          order_by="dimension_name asc", limit_page_length=0)


def _facts(groups):
    keys = list(groups)
    entities = sorted({k.data_area_id for k in keys})
    scenarios = sorted({k.scenario for k in keys})
    years = sorted({k.fiscal_year for k in keys})
    functional = {e.name: e.functional_currency or "" for e in frappe.get_all(
        "Entity", filters={"name": ["in", entities]}, fields=["name", "functional_currency"],
        order_by="name asc", limit_page_length=0)}
    minor_units = {c.name: c.minor_unit for c in frappe.get_all(
        "ISO Currency", filters={"name": ["in", sorted({c for c in functional.values() if c})]},
        fields=["name", "minor_unit"], order_by="name asc", limit_page_length=0)}
    cycles = {}
    for c in frappe.get_all("Budget Cycle",
                            filters={"scenario_id": ["in", scenarios], "fiscal_year": ["in", years]},
                            fields=["name", "scenario_id", "fiscal_year", "docstatus"],
                            order_by="creation asc, name asc", limit_page_length=0):
        cycles.setdefault((c.scenario_id, int(c.fiscal_year)), []).append(c)
    return {
        "chart": chart_accounts(),
        "periods": {(k.fiscal_year, k.fiscal_period): _period(k.fiscal_year, k.fiscal_period) for k in keys},
        "functional": functional,
        "minor_units": minor_units,
        # sort_order is the layers' declared order: the refusal lists them in it.
        "layers": {r.name: r.writer_role or "" for r in frappe.get_all(
            "Budget Layer", fields=["name", "writer_role"], order_by="sort_order asc, name asc",
            limit_page_length=0)},
        "scenarios": {r.name: {"scenario_type": r.scenario_type} for r in frappe.get_all(
            "Scenario", filters={"is_active": 1}, fields=["name", "scenario_type"],
            order_by="name asc", limit_page_length=0)},
        "cycles": cycles,
    }


def _period(year, period):
    """The declared period, or None when it is not declared."""
    try:
        return period_row(year, period)
    except PeriodNotDeclared:
        return None


def _cycle(found, key):
    """(cycle name or None, problem or None) for one (scenario, year). A draft
    is taken when there is one; otherwise the latest is named and refused."""
    drafts = [c for c in found if int(c.docstatus) == 0]
    if drafts:
        return drafts[0].name, None
    if not found:
        return None, (f"No Budget Cycle for scenario {key.scenario}, fiscal year {key.fiscal_year}. "
                      f"Create the Budget Cycle for {key.scenario} {key.fiscal_year} first: a budget "
                      "write never creates one.")
    latest = found[-1]
    state = _CYCLE_STATE.get(int(latest.docstatus), f"docstatus {latest.docstatus}")
    return latest.name, (f"Budget Cycle {latest.name} ({key.scenario} {key.fiscal_year}) is {state}: "
                         "a budget is written only while its Cycle is a draft.")


def _duplicates(rows):
    """One sentence per row that repeats an earlier row's account and dim_*
    values within one Budget Entry, naming both lines."""
    seen, out = {}, []
    for row in rows:
        dims = tuple(sorted((k, row.get(k) or "") for k in row if k.startswith("dim_")))
        ident = (row.get("main_account"), dims)
        if ident in seen:
            named = ", ".join(f"{d} {v}" for d, v in dims if v)
            out.append(f"Lines {seen[ident]} and {row.get('line')} both hold account {ident[0]}"
                       + (f" with {named}" if named else "")
                       + ". Keep one row per account and dimension combination.")
        else:
            seen[ident] = row.get("line")
    return out
