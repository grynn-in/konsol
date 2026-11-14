"""Fill the scenario_type copy on existing planning rows (#443).

Decided by Deepak Pai, 7 Nov 2026, on #443 (option B; rejected A, C, D):
Budget Cycle and Budget Annual Input carry a read-only copy of
their Scenario's type, which the Planning sidebar filters on. Frappe fills a
fetched field only when the row is saved, so rows that existed before the field
would be listed under neither Budget nor Forecast. This copies the type onto
every row whose copy is missing or differs; ``modified`` is left alone.

migrate runs every konsol patch before the model sync (patches.txt has no
[post_model_sync] section), so the doctypes are reloaded first to add the
column. konsol#348 S2-C Z1 retired the third planning doctype, the bottom-up
sheet, so it is no longer reloaded or backfilled: the retirement patch drops
its rows. konsol/tests/test_scenario_type_copies.py runs it against a stub site.
"""
import frappe

FIELD = "scenario_type"


def execute():
    for name in ("budget_cycle", "budget_annual_input"):
        frappe.reload_doc("epm", "doctype", name)

    types = {r["name"]: r.get(FIELD) for r in frappe.get_all("Scenario", fields=["name", FIELD])}

    for doctype in ("Budget Cycle", "Budget Annual Input"):
        for r in frappe.get_all(doctype, fields=["name", "scenario_id", FIELD]):
            _set(doctype, r, types.get(r.get("scenario_id")))


def _set(doctype, row, want):
    if want and row.get(FIELD) != want:
        frappe.db.set_value(doctype, row["name"], FIELD, want, update_modified=False)
