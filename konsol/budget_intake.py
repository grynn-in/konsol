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
     A refusal is an access denial, as the access check's is.

   - one amount basis per (scenario, year, entity, layer) in the file (Q15-c):
     two periods of one layer that give two bases are both refused, each
     naming the other.

An inactive Scenario is gathered and refused as inactive; only a Scenario that
does not exist is refused as undeclared.

The rules that span saved records run where every writer's save passes, the
Budget Entry and Budget Annual Input controllers, after they lock the Cycle:
``assert_one_basis`` (Q15-c: one basis per Cycle, entity and layer across all
its entries) and the one-writer-per-grain pair (Q16-c). Their reads are
locking reads: under REPEATABLE READ a plain read serves the transaction's
snapshot and misses what another writer committed while this one waited on
the Cycle.

``annual_check`` is the same entry for one Budget Annual Input (A6): access,
declared layer, the layer's writer role, a plan scenario, and the entity's
functional currency. ``assert_annual_access`` is its access check alone (a
delete is a write, but its layer and currency are not re-judged).
Layer codes are compared exactly: ``Base`` is not ``base``.
"""
import frappe

from konsol import budget_bulk_model as M
from konsol.entity_permissions import BYPASS_ROLES
from konsol.group_chart import chart_accounts
from konsol.period_status import PeriodNotDeclared, period_row
from konsol.tb_currency_model import currency_problems

#: Docstatus words for a Cycle that is not a draft.
_CYCLE_STATE = {1: "approved", 2: "cancelled"}

#: The index each budget locking read runs on (PR #522 review). A FOR UPDATE
#: with no index scans the table, and InnoDB next-key-locks every row it scans:
#: two cell saves in different Cycles each hold their own entry and then wait
#: on the other's row (deadlock 1213), and every writer queues behind the scan.
#: The reads filter on every column with equality, the last (main_account) by
#: equality or IN.
LOCK_INDEXES = {
    # lock_cycles
    "Budget Cycle": ("scenario_id", "fiscal_year"),
    # assert_one_basis, and budget_bulk._replace_slice's slice read
    "Budget Entry": ("cycle", "data_area_id", "layer"),
    # assert_no_entry_lines
    "Budget Entry Line": ("scenario_id", "fiscal_year", "data_area_id", "layer", "main_account"),
    # assert_no_annual_input, and Budget Annual Input's own grain check
    "Budget Annual Input": ("scenario_id", "fiscal_year", "data_area_id", "layer", "main_account"),
}
LOCK_INDEX_NAME = "budget_lock"


def add_lock_index(doctype):
    """Add `doctype`'s lock index if it is missing. Each doctype's
    on_doctype_update calls this (doctype sync); ensure_lock_indexes covers
    existing sites from after_migrate."""
    frappe.db.add_index(doctype, list(LOCK_INDEXES[doctype]), index_name=LOCK_INDEX_NAME)


def ensure_lock_indexes():
    """Every budget lock index, idempotently (add_index checks first)."""
    for doctype in LOCK_INDEXES:
        add_lock_index(doctype)


def plan(table, *, form_basis, name_lines):
    """Judge a budget table for the current user. Returns

        {"groups": {BudgetKey: [rows]}, "report": [item per group], "ok": bool}

    ``groups`` is in file order. Each report item is budget_bulk_model's
    check_group row {scenario, fiscal_year, entity, layer, fiscal_period, rows,
    errors, ok} plus ``cycle`` (the Budget Cycle's name, or None), ``currency``
    (the entity's functional currency) and ``amount_basis`` (resolved from the
    file or `form_basis`, '' when none). A group with any error is not ready;
    a writer writes nothing for it.

    ``denials`` lists the access refusals among ``errors``: the layer's writer
    role and _assert_budget_write_access on each row. They are also in
    ``errors``, so a report names them with the rest, and an API writer raises
    them as a PermissionError (HTTP 403), not a ValidationError.

    `form_basis` is the upload's Amount Basis ('' when it has none); it has no
    default, so a writer states what it means.

    `name_lines` says whether a problem names its file line ("Line 3: ...").
    A file writer (the CSV check and load) passes True. A writer whose rows
    are not file lines (an Excel cell, an API row) passes False: each row's
    line is None, the convention the shared checks read as "no line known",
    and a problem names its account instead or nothing. A batch caller then
    names its own row. No default.
    """
    groups = M.split_table(table, _budget_dimensions())
    if not name_lines:
        for rows in groups.values():
            for row in rows:
                row["line"] = None
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
            minor_unit=facts["minor_units"].get(currency), form_basis=form_basis,
            from_file=name_lines)
        cycle, cycle_problem = _cycle(facts["cycles"].get((key.scenario, key.fiscal_year), ()), key)
        item["cycle"] = cycle
        item["currency"] = currency or ""
        basis, _ = M.resolve_basis(M.group_basis(rows), form_basis)
        item["amount_basis"] = basis or ""
        errors = item["errors"]
        if cycle_problem:
            errors.append(cycle_problem)
        errors.extend(_duplicates(rows))
        # The layer's writer role is an access rule: a refusal is a denial.
        denials = _role_problems(key.layer, facts["layers"].get(key.layer), bypass, roles, user)
        for row in rows:
            data = dict(row, data_area_id=key.data_area_id)
            try:
                _assert_budget_write_access(data)
            except frappe.PermissionError as error:
                denials.append(_at_line(row, error))
        errors.extend(denials)
        item["denials"] = denials
        report.append(item)
    _refuse_mixed_bases(report)
    for item in report:
        item["ok"] = not item["errors"]
    return {"groups": groups, "report": report, "ok": all(i["ok"] for i in report)}


def _refuse_mixed_bases(report):
    """One amount basis per (scenario, year, entity, layer) within the table
    (Q15-c). A group whose basis differs from another period's of its layer is
    refused, naming that period and its basis. A group with no basis is
    refused already, so it is left out."""
    by_slice = {}
    for item in report:
        if item["amount_basis"]:
            slice_key = (item["scenario"], item["fiscal_year"], item["entity"], item["layer"])
            by_slice.setdefault(slice_key, []).append(item)
    for items in by_slice.values():
        for item in items:
            other = next((o for o in items if o["amount_basis"] != item["amount_basis"]), None)
            if other:
                item["errors"].append(
                    f"Period {item['fiscal_period']} gives amount basis \"{item['amount_basis']}\" but "
                    f"period {other['fiscal_period']} gives \"{other['amount_basis']}\" for entity "
                    f"{item['entity']}, layer {item['layer']}: one Cycle, entity and layer hold one "
                    "amount basis across all their periods. Give every period of the layer the same basis.")


def assert_one_basis(cycle, entity, layer, basis, *, name, replacing=()):
    """Refuse a Budget Entry whose amount basis differs from another entry of
    its (cycle, entity, layer) (Q15-c), naming that entry, its period and its
    basis. `name` is the entry being saved (it does not conflict with
    itself); `replacing` names entries the same write is rewriting (a Budget
    Upload replaces a slice whole), whose old basis does not count.

    A locking read (the caller holds the Cycle lock): it sees an entry another
    writer committed while this one waited. Writes nothing."""
    if not basis:
        return
    rows = frappe.db.get_values(
        "Budget Entry", {"cycle": cycle, "data_area_id": entity, "layer": layer},
        ["name", "fiscal_period", "amount_basis"], as_dict=True,
        order_by="fiscal_period asc, name asc", for_update=True)
    skip = set(replacing) | {name}
    for row in rows:
        if row.name in skip or (row.amount_basis or "") == basis:
            continue
        frappe.throw(
            f"Budget Entry {row.name} (period {row.fiscal_period}) holds amount basis "
            f"\"{row.amount_basis}\" for Cycle {cycle}, entity {entity}, layer {layer}; this entry "
            f"gives \"{basis}\". One Cycle, entity and layer hold one amount basis across all their "
            f"periods. Write this period as \"{row.amount_basis}\", or reload the layer's every period "
            "with one basis in a Budget Upload.",
            frappe.ValidationError,
        )


def judge_one(table, *, form_basis):
    """Judge a table holding ONE Budget Entry's rows for a writer that is not a
    file (an Excel cell, an API row, a Desk save of a Budget Entry): its
    problems name no file line. Returns (report item, rows) when the rows are
    ready; otherwise raises with every problem the plan found, as
    frappe.PermissionError when it found an access denial (HTTP 403: a denial
    wins, the other problems are not for a user who may not write the rows),
    else frappe.ValidationError. Writes nothing."""
    try:
        result = plan(table, form_basis=form_basis, name_lines=False)
    except ValueError as error:
        frappe.throw(str(error), frappe.ValidationError)
    if len(result["report"]) != 1:
        frappe.throw(f"These rows make {len(result['report'])} Budget Entries; one was expected.",
                     frappe.ValidationError)
    (item,) = result["report"]
    if item["denials"]:
        frappe.throw("\n".join(item["denials"]), frappe.PermissionError)
    if not item["ok"]:
        frappe.throw("\n".join(item["errors"]), frappe.ValidationError)
    (rows,) = result["groups"].values()
    return item, rows


def _at_line(row, message):
    """`message` prefixed with the row's file line, or alone when it has none."""
    line = row.get("line")
    return f"Line {line}: {message}" if line is not None else str(message)


def _role_problems(layer, role, bypass, roles, user):
    if role and not bypass and role not in roles:
        return [f"Layer {layer} takes rows only from holders of the {role} role, and "
                f"{user} does not hold it. Ask for the role, or write into another layer."]
    return []


def assert_may_remove(entity, layer, rows):
    """Removing budget lines is a write to them: the layer's writer role and
    the access check on each row (entity, account, dimensions). `rows` are
    dicts with main_account and the dim_* values. Raises frappe.PermissionError
    naming every refusal. Writes nothing."""
    from konsol.api import _assert_budget_write_access

    user = frappe.session.user
    roles = set(frappe.get_roles(user))
    bypass = user == "Administrator" or bool(BYPASS_ROLES & roles)
    found = frappe.get_all("Budget Layer", filters={"name": layer}, fields=["name", "writer_role"],
                           order_by="name asc", limit_page_length=0)
    denials = _role_problems(layer, (found[0].writer_role or "") if found else "", bypass, roles, user)
    for row in rows:
        try:
            _assert_budget_write_access(dict(row, data_area_id=entity))
        except frappe.PermissionError as error:
            denials.append(f"Removing account {row.get('main_account')}: {error}")
    if denials:
        frappe.throw("\n".join(denials), frappe.PermissionError)


def assert_annual_access(row):
    """The entity, account and dimension check for one annual figure (konsol#413).
    Raises frappe.PermissionError."""
    from konsol.api import _assert_budget_write_access

    _assert_budget_write_access(row)


def annual_check(row):
    """Judge one Budget Annual Input before it is saved. `row` is a dict with
    scenario_id, data_area_id, main_account, layer, currency and the dim_*
    values. Raises frappe.PermissionError when the user may not write it (the
    access check, or the layer's writer role), else
    frappe.ValidationError naming every other problem. Writes nothing.

    Not judged here: the period (an annual figure has none), the Cycle (the
    controller's lock guard), the grain, and the Main Account's status (the
    controller refuses a non-Published account by name)."""
    assert_annual_access(row)
    user = frappe.session.user
    roles = set(frappe.get_roles(user))
    bypass = user == "Administrator" or bool(BYPASS_ROLES & roles)
    layers = {r.name: r.writer_role or "" for r in frappe.get_all(
        "Budget Layer", fields=["name", "writer_role"], order_by="sort_order asc, name asc",
        limit_page_length=0)}
    found = frappe.get_all("Scenario", filters={"name": row.get("scenario_id")},
                           fields=["name", "scenario_type", "is_active"], order_by="name asc",
                           limit_page_length=0)
    scenario = ({"scenario_type": found[0].scenario_type, "is_active": found[0].is_active}
                if found else None)
    entity = frappe.get_all("Entity", filters={"name": row.get("data_area_id")},
                            fields=["name", "functional_currency"], order_by="name asc",
                            limit_page_length=0)
    functional = (entity[0].functional_currency or "") if entity else ""
    layer = row.get("layer")
    denials = _role_problems(layer, layers.get(layer), bypass, roles, user)
    if denials:
        frappe.throw("\n".join(denials), frappe.PermissionError)
    problems = M.layer_problems(layer, layers)
    problems += M.scenario_problems(row.get("scenario_id"), scenario)
    problems += currency_problems(row.get("data_area_id"), functional, [(None, row.get("currency"))])
    if problems:
        frappe.throw("\n".join(problems), frappe.ValidationError)


def lock_cycles(scenario_id, fiscal_year):
    """Lock every Budget Cycle row of (scenario, year), in name order, and
    return their names: a Budget Annual Input derives its Cycle from the pair
    rather than linking one, and an amended pair holds more than one. The
    Annual Input writers take this first, the lock order every budget writer
    keeps (the Cycle row before the record it saves). No Cycle, nothing
    locked: then no Budget Entry of the pair exists to conflict with.
    Skipped in install and migrate, as assert_cycle_draft is."""
    if frappe.flags.in_install or frappe.flags.in_migrate:
        return []
    rows = frappe.db.get_values("Budget Cycle", {"scenario_id": scenario_id, "fiscal_year": fiscal_year},
                                ["name"], as_dict=True, order_by="name asc", for_update=True)
    return [r.name for r in rows]


def _dim_fields(doctype):
    """The budget dimension fields `doctype` carries (Data dim_* fields, off
    the meta, as its controller reads them)."""
    return {f.fieldname for f in frappe.get_meta(doctype).fields
            if (f.fieldname or "").startswith("dim_") and f.fieldtype == "Data"}


def _grain_key(account, row, dims):
    """(account, dim values) with a blank dimension and an unset one the same
    value, as Budget Annual Input's own grain reads them."""
    return (account,) + tuple(row.get(d) or "" for d in dims)


def _named_dims(dims, key):
    named = ", ".join(f"{d} {v}" for d, v in zip(dims, key[1:]) if v)
    return f" with {named}" if named else ""


def assert_no_annual_input(scenario_id, fiscal_year, entity, layer, lines, *, entry, period):
    """Refuse a Budget Entry line on a grain a Budget Annual Input already
    gives (Q16-c): a grain (scenario, year, entity, layer, account, dim_*
    values) takes its numbers from one writer. `lines` are dicts with
    main_account and the dim_* values; `entry` and `period` name the entry
    being saved. Raises frappe.ValidationError naming the Annual Input and the
    way out. A locking read (the caller holds the Cycle lock). Writes nothing."""
    accounts = sorted({line.get("main_account") for line in lines if line.get("main_account")})
    if not accounts:
        return
    annual_dims = _dim_fields("Budget Annual Input")
    dims = sorted(annual_dims | {k for line in lines for k in line if k.startswith("dim_")})
    rows = frappe.db.get_values(
        "Budget Annual Input",
        {"scenario_id": scenario_id, "fiscal_year": fiscal_year, "data_area_id": entity, "layer": layer,
         "main_account": ["in", accounts]},
        ["name", "main_account", *sorted(annual_dims)], as_dict=True, order_by="name asc", for_update=True)
    annual = {}
    for row in rows:
        annual.setdefault(_grain_key(row.main_account, row, dims), row.name)
    for line in lines:
        key = _grain_key(line.get("main_account"), line, dims)
        if key in annual:
            name = annual[key]
            frappe.throw(
                f"Budget Annual Input {name} already gives account {key[0]}{_named_dims(dims, key)} for "
                f"{scenario_id} {fiscal_year}, entity {entity}, layer {layer} as an annual figure, so "
                f"Budget Entry {entry} (period {period}) cannot give it too: a budget line takes its "
                f"numbers from one writer. Delete Budget Annual Input {name}, or enter the year as "
                "monthly entries.",
                frappe.ValidationError,
            )


def assert_no_entry_lines(row, *, name):
    """Refuse a Budget Annual Input on a grain a Budget Entry line already
    gives, in any period (Q16-c). `row` holds scenario_id, fiscal_year,
    data_area_id, layer, main_account and the dim_* values; `name` is the
    Annual Input. Raises frappe.ValidationError naming the entry and period.
    A locking read (the caller holds the Cycle lock). Writes nothing.

    Only lines under a Cycle that is not cancelled count (19-b): under an
    amended pair the cancelled original's entries stay as the audit trail,
    out of the warehouse, and do not block. (The other direction needs no
    filter: a Budget Entry is saved only under a draft Cycle of its pair, so
    an Annual Input of that pair always counts against it.)"""
    if not row.get("main_account"):
        return
    line_dims = _dim_fields("Budget Entry Line")
    dims = sorted(line_dims | {k for k in row if k.startswith("dim_")})
    lines = frappe.db.get_values(
        "Budget Entry Line",
        {"parenttype": "Budget Entry", "scenario_id": row.get("scenario_id"),
         "fiscal_year": row.get("fiscal_year"), "data_area_id": row.get("data_area_id"),
         "layer": row.get("layer"), "main_account": row.get("main_account")},
        ["parent", "cycle", "fiscal_period", "main_account", *sorted(line_dims)], as_dict=True,
        order_by="fiscal_period asc, parent asc", for_update=True)
    key = _grain_key(row.get("main_account"), row, dims)
    lines = [line for line in lines if _grain_key(line.main_account, line, dims) == key]
    cycles = sorted({line.cycle for line in lines})
    live = {c.name for c in frappe.db.get_values(
        "Budget Cycle", {"name": ["in", cycles]}, ["name", "docstatus"], as_dict=True,
        order_by="name asc", for_update=True) if int(c.docstatus) in (0, 1)} if cycles else set()
    for line in lines:
        if line.cycle in live:
            frappe.throw(
                f"Budget Entry {line.parent} (period {line.fiscal_period}) already gives account "
                f"{key[0]}{_named_dims(dims, key)} for {row.get('scenario_id')} {row.get('fiscal_year')}, "
                f"entity {row.get('data_area_id')}, layer {row.get('layer')} as a monthly figure, so "
                f"Budget Annual Input {name} cannot give it too: a budget line takes its numbers from one "
                "writer. Keep entering the year as monthly entries, or remove the account's lines from "
                "the layer's Budget Entries first.",
                frappe.ValidationError,
            )


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
        "scenarios": {r.name: {"scenario_type": r.scenario_type, "is_active": r.is_active} for r in frappe.get_all(
            "Scenario", fields=["name", "scenario_type", "is_active"],
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
    values within one Budget Entry, naming both lines (or, when the rows have
    no lines, the account alone)."""
    seen, out = {}, []
    for row in rows:
        dims = tuple(sorted((k, row.get(k) or "") for k in row if k.startswith("dim_")))
        ident = (row.get("main_account"), dims)
        if ident in seen:
            named = ", ".join(f"{d} {v}" for d, v in dims if v)
            where = (f"Lines {seen[ident]} and {row.get('line')} both hold"
                     if row.get("line") is not None else "Two rows hold")
            out.append(f"{where} account {ident[0]}"
                       + (f" with {named}" if named else "")
                       + ". Keep one row per account and dimension combination.")
        else:
            seen[ident] = row.get("line")
    return out
