"""Rename Cash Flow Category rows to deterministic CFC-{main_account} names (#68).

The doctype shipped with ``autoname: hash``, but the fixture names records
``CFC-{main_account}`` and ``validate`` already enforces one live mapping per
``main_account``. The mismatch made fixture re-import non-idempotent: a fixture
record (e.g. ``CFC-1200``) would try to INSERT a second mapping for an account
that already has a hash-named row, tripping the unique-account guard and aborting
``bench migrate``.

This patch renames every existing row to ``CFC-{main_account}`` so the live name
matches what the fixture (and the new ``format:CFC-{main_account}`` autoname)
produce — turning re-import into an in-place update. ``main_account`` is unique
per row (enforced by validate), so the target names don't collide.

Runs before fixtures (all patches do), so the fixture import that follows finds
matching names. Idempotent: rows already correctly named are skipped; re-running
matches nothing.

Note: pairing the rename with autoname ``format:CFC-{main_account}`` tightens the
model to ONE row per account ever (not just one *live* row, which ``validate``
already enforced). That matches how this config is used — a single editable
cash-flow mapping per balance-sheet account — but means an Inactive historical
row and a new mapping for the same account can no longer coexist.
"""
import frappe


def execute():
    if not frappe.db.table_exists("Cash Flow Category"):
        return
    try:
        columns = frappe.db.get_table_columns("Cash Flow Category")
    except Exception:
        columns = []
    if "main_account" not in columns:
        return

    rows = frappe.db.get_all(
        "Cash Flow Category", fields=["name", "main_account"]
    )
    for row in rows:
        account = (row.main_account or "").strip()
        if not account:
            # No account to derive a name from — leave it (validate would have
            # blocked a publish anyway); skip rather than crash the migrate.
            continue
        target = f"CFC-{account}"
        if row.name == target:
            continue
        if frappe.db.exists("Cash Flow Category", target):
            # Shouldn't happen (main_account is unique), but never clobber an
            # existing row: skip and let the unique-account guard surface it.
            continue
        frappe.rename_doc(
            "Cash Flow Category",
            row.name,
            target,
            force=True,
            ignore_permissions=True,
            show_alert=False,
            rebuild_search=False,  # one-time migrate — skip the per-row search reindex
        )
    frappe.db.commit()
