"""Reshape Budget Input (per-grain docs) → Budget Cycle / Sheet / Line.

Pre-reshape, one ``Budget Input`` = one (scenario, entity, fy, main_account,
+dims) with a tall ``periods`` child and a per-doc workflow. This pivots that
data into the new shape:

    Budget Cycle  (scenario × fiscal_year)        — the single lock gate
      └ Budget Sheet  (cycle × entity × layer)    — wide lines live here
          └ Budget Line  (account + dims, period_01..12)

The pivot is **non-destructive and idempotent**: old ``Budget Input`` docs are
left in place (for verification + rollback), migrated cycles stay Open (the
manager locks them after review), and re-running overwrites the same cells
rather than doubling. Dropping the old doctypes and purging stale D365
``EPM-BUD-*`` model ids are deliberate cutover steps — see ``purge_legacy_d365``
below and the reshape plan — not part of this automated pivot.
"""
import frappe


def _period_field(fiscal_period):
    return "period_%02d" % int(fiscal_period)


def _get_or_create_cycle(scenario_id, fiscal_year, cache):
    key = (scenario_id, fiscal_year)
    if key in cache:
        return cache[key]
    existing = frappe.get_all(
        "Budget Cycle",
        filters={"scenario_id": scenario_id, "fiscal_year": fiscal_year},
        limit=1,
        pluck="name",
    )
    if existing:
        cache[key] = existing[0]
        return existing[0]
    doc = frappe.new_doc("Budget Cycle")
    doc.scenario_id = scenario_id
    doc.fiscal_year = fiscal_year
    doc.status = "Open"
    doc.insert()
    cache[key] = doc.name
    return doc.name


def _load_or_new_sheet(cycle_name, entity, layer, cache):
    key = (cycle_name, entity, layer)
    if key in cache:
        return cache[key]
    existing = frappe.get_all(
        "Budget Sheet",
        filters={"cycle": cycle_name, "data_area_id": entity, "layer": layer},
        limit=1,
        pluck="name",
    )
    if existing:
        sheet = frappe.get_doc("Budget Sheet", existing[0])
    else:
        sheet = frappe.new_doc("Budget Sheet")
        sheet.cycle = cycle_name
        sheet.data_area_id = entity
        sheet.layer = layer
    cache[key] = sheet
    return sheet


def _find_or_append_line(sheet, old, dims):
    for line in sheet.lines:
        if line.main_account != old.main_account:
            continue
        if all((line.get(d) or "") == (old.get(d) or "") for d in dims):
            return line
    values = {"main_account": old.main_account}
    for d in dims:
        values[d] = old.get(d) or ""
    return sheet.append("lines", values)


def execute():
    if not frappe.db.table_exists("Budget Input"):
        return

    from konsol.epm.budget_grain import budget_dimension_names

    dims = budget_dimension_names()
    cycle_cache = {}
    sheet_cache = {}

    names = frappe.get_all("Budget Input", pluck="name")
    for old_name in names:
        old = frappe.get_doc("Budget Input", old_name)
        if not old.get("periods"):
            continue
        for layer in {p.layer for p in old.periods}:
            cycle_name = _get_or_create_cycle(
                old.scenario_id, int(old.fiscal_year), cycle_cache)
            sheet = _load_or_new_sheet(cycle_name, old.data_area_id, layer, sheet_cache)
            line = _find_or_append_line(sheet, old, dims)
            for p in old.periods:
                if p.layer != layer:
                    continue
                line.set(_period_field(p.fiscal_period), p.amount or 0)

    for sheet in sheet_cache.values():
        sheet.save()

    print(
        "reshape_budget_input_to_cycle: pivoted {0} Budget Input doc(s) into "
        "{1} cycle(s), {2} sheet(s). Old docs retained; lock cycles after "
        "review and run purge_legacy_d365() at cutover.".format(
            len(names), len(cycle_cache), len(sheet_cache))
    )


def purge_legacy_d365(force=False):
    """Delete pre-reshape ``EPM-BUD-*`` budget entries from D365 (cutover step).

    The regrained per-sheet model ids (``EPM-<sheet>``) won't match the old
    per-grain ids, so without this the old and new entries coexist as a double
    budget. Run ONCE at cutover (``bench execute
    konsol.patches.reshape_budget_input_to_cycle.purge_legacy_d365``) after the
    new sheet pushes are verified. Guarded per entity on the write-back config;
    a no-op when write-back is disabled. ``force`` is reserved for re-runs.
    """
    if not frappe.db.table_exists("Budget Input"):
        return {"status": "skipped", "reason": "no Budget Input table"}

    from konsol.d365_writeback import (
        budget_model_id, get_config, get_token, purge_budget_model,
    )

    purged, skipped = [], []
    token_by_entity = {}
    for old in frappe.get_all("Budget Input", fields=["name", "data_area_id"]):
        cfg = get_config(entity_id=old.data_area_id)
        if not cfg.get("enabled"):
            skipped.append(old.name)
            continue
        token = token_by_entity.get(old.data_area_id) or get_token(cfg)
        token_by_entity[old.data_area_id] = token
        # Legacy ids were "EPM-<budget_input_name>"; budget_model_id is the same
        # "EPM-" + name shape, so it reproduces the old tag for the delete.
        purge_budget_model(cfg, token, budget_model_id(old.name))
        purged.append(old.name)

    return {"status": "ok", "purged": len(purged), "skipped": len(skipped)}
