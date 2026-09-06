"""Guarded DocType renames for patches.txt.

``frappe.rename_doc(..., ignore_if_exists=True)`` guards the *target*, not the
*source*: if the source DocType is not on this site it raises
``DoesNotExistError``. Because ``patch_handler`` stops at the first failure,
one inapplicable rename silently blocks every patch after it — which is how a
site ends up with ``Run Step`` renamed but ``Build Approval`` never created,
and ``control_api`` failing with ``('DocType', 'Build Approval')``.

Sites are not all the same vintage. A site created before a rename has the old
name, a site created after has the new one, and a site created in between has
some of each — so every rename in this app has to be a no-op when it does not
apply, not an exception.

These run in the ``pre_model_sync`` phase: konsol's patches.txt has no section
headers, so Frappe's old-format fallback treats them all as pre-sync. That is
what makes the "target must not already exist" check safe — at this point the
new DocType can only exist because an earlier rename created it, never because
the app's JSON has been synced.
"""

import frappe


def safe_rename(old: str, new: str) -> bool:
    """Rename a DocType if — and only if — the rename still applies.

    Returns True when a rename happened, False when it was skipped. Skips when:

      * the source DocType does not exist (this site is a later vintage, or
        never had it at all);
      * the target already exists (the rename has already run, or both names
        are live and merging them would be destructive);
      * the source's table is missing, which is a broken site rather than one
        needing a rename — worth leaving alone rather than crashing migrate.
    """
    if not frappe.db.exists("DocType", old):
        return False

    if frappe.db.exists("DocType", new):
        return False

    if not frappe.db.table_exists(old):
        frappe.logger().warning(
            f"konsol: skipping rename {old!r} -> {new!r}; DocType row exists but its table does not"
        )
        return False

    frappe.rename_doc("DocType", old, new, force=True, ignore_if_exists=True)
    frappe.logger().info(f"konsol: renamed DocType {old!r} -> {new!r}")
    return True
