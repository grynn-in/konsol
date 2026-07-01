"""Re-key stale Historical Equity Rate records from GROUP_EMEA to GROUP_CORP (#130).

DEMF/GBMF HER doctype records were keyed to GROUP_EMEA, but the operative
consolidation (the dbt `consolidation_groups` seed / gold) is flat under
GROUP_CORP — so those equity rates never matched in gold and DEMF/GBMF equity
silently fell back to the closing rate.

Done purely at the DB level (rename + set_value), NOT via the doc lifecycle, on
purpose: the doctype's on_submit/on_cancel/on_trash do a TRUNCATE+INSERT re-sync
of `epm_staging.historical_equity_rates` from ONLY the doctype's rows — which
would wipe the demo-data-sourced AMG rows that live in the same CH table (the
dual-writer problem tracked separately). DB-level ops avoid triggering that sync.

Idempotent: no GROUP_EMEA records => no-op; a row whose GROUP_CORP twin already
exists is dropped at the DB level.
"""
import frappe

_OLD = "GROUP_EMEA"
_NEW = "GROUP_CORP"


def execute():
    if not frappe.db.table_exists("Historical Equity Rate"):
        return
    if not frappe.db.exists("Consolidation Group", {"consolidation_group": _NEW}):
        return

    stale = frappe.get_all(
        "Historical Equity Rate",
        filters={"consolidation_group": _OLD},
        fields=["name", "data_area_id", "main_account", "rate_date"],
    )
    if not stale:
        return

    from konsol.epm.budget_grain import digest_name

    for row in stale:
        new_name = digest_name(
            "HER", [_NEW, row.data_area_id, row.main_account, row.rate_date]
        )
        if new_name != row.name and frappe.db.exists("Historical Equity Rate", new_name):
            # A GROUP_CORP twin already exists — drop the stale row (DB-level, no on_trash sync).
            frappe.db.delete("Historical Equity Rate", {"name": row.name})
            continue
        if new_name != row.name:
            frappe.rename_doc(
                "Historical Equity Rate", row.name, new_name,
                force=True, show_alert=False,
            )
        frappe.db.set_value(
            "Historical Equity Rate", new_name,
            "consolidation_group", _NEW, update_modified=False,
        )

    frappe.db.commit()
