"""Stamp calendar_changed_at on every existing EPM Fiscal Year (konsol#340).

Freshness reads calendar_changed_at for EPM Fiscal Year. A year saved before
the field existed has none, and MAX() skips a NULL, so a calendar change made
before the upgrade and not yet built would vanish from the indicator.

Stamped with ``modified``, not ``creation`` and not left NULL. ``modified`` is
at or after the year's last calendar change, so it cannot hide one; the cost
is that a year closed, locked or reopened after the last full build shows the
numbers as stale until the next full build. ``creation`` would hide every
edit made after the year was created; NULL would hide the year entirely.

update_modified is not involved: one UPDATE, ``modified`` unchanged. A second
run finds nothing to stamp. patches.txt has no sections, so this runs
pre_model_sync: it reloads EPM Fiscal Year first, for the new column.
"""
import frappe


def execute():
    frappe.reload_doc("epm", "doctype", "epm_fiscal_year")
    frappe.db.sql(
        "UPDATE `tabEPM Fiscal Year` SET calendar_changed_at = modified "
        "WHERE calendar_changed_at IS NULL")
    left = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabEPM Fiscal Year` WHERE calendar_changed_at IS NULL")[0][0]
    if left:
        raise frappe.ValidationError(
            f"stamp_fiscal_year_calendar_changed_at: {left} year(s) still unstamped")
    print("stamp_fiscal_year_calendar_changed_at: every EPM Fiscal Year stamped")
