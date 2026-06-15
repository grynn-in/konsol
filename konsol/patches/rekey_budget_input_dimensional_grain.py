"""Re-key Budget Input docs to the dimensional grain (spec #51).

The budget unique key gained the ``in_budget`` dimensions, so a doc's name
changed from ``BUD-{scenario}-{area}-{fy}-{account}`` to one that also encodes
the dimension values. Rename existing docs to the new deterministic name.

No customer is live (the table is demo/empty), so there is no collision
recovery path: if two existing docs map to the SAME new name — i.e. they were
already clobbered into one account-only row under the old grain — abort loudly
rather than silently merge.
"""
import frappe


def execute():
    if not frappe.db.table_exists("Budget Input"):
        return

    from konsol.epm.budget_grain import budget_dimension_names, budget_name

    names = frappe.get_all("Budget Input", pluck="name")
    if not names:
        return

    dims = budget_dimension_names()
    planned = {}  # new_name -> old_name
    for old in names:
        doc = frappe.get_doc("Budget Input", old)
        values = {
            "scenario_id": doc.scenario_id,
            "data_area_id": doc.data_area_id,
            "fiscal_year": doc.fiscal_year,
            "main_account": doc.main_account,
        }
        for d in dims:
            values[d] = doc.get(d)
        new = budget_name(values)
        if new in planned and planned[new] != old:
            frappe.throw(
                f"Budget Input grain migration: docs {planned[new]!r} and {old!r} "
                f"both map to {new!r} — they were merged under the old account-only "
                f"grain and cannot be split. Resolve manually before migrating."
            )
        planned[new] = old

    renamed = 0
    for new, old in planned.items():
        if new != old:
            frappe.rename_doc("Budget Input", old, new, force=True, show_alert=False)
            renamed += 1

    if renamed:
        frappe.db.commit()
        print(f"rekey_budget_input_dimensional_grain: renamed {renamed} Budget Input doc(s)")
