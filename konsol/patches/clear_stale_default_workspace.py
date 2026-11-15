"""Clear a User's default_workspace when it names a Workspace that no longer exists (#530).

``delete_konsolidat_workspace`` deleted Workspace "Konsolidat" and left every
User who had it as ``default_workspace`` pointing at it. Any full save of such
a User then failed with ``LinkValidationError: Could not find Default
Workspace: Konsolidat``: a role change, a profile edit, a patch calling
``User.add_roles``.

This patch sets ``default_workspace`` to NULL on every User whose value names
a Workspace that does not exist, whatever the name, and prints the count and
the user names per missing Workspace. It writes the column directly: a
``User.save()`` is the call that fails. A rerun changes nothing
(konsol/tests/test_clear_stale_default_workspace_patch.py).

``clear_default_workspace`` is also what a patch that deletes a Workspace
calls first, so it does not leave the same dangling link behind.
"""


def clear_default_workspace(workspace):
    """Set ``default_workspace`` to NULL on every User naming ``workspace``,
    without saving the User; returns the user names cleared, sorted."""
    # Imported here, not at module top: the patches that delete a Workspace
    # import this function, and the host tests run each of them against its
    # own stub ``frappe``.
    import frappe

    users = frappe.get_all("User", filters={"default_workspace": workspace},
                           pluck="name", order_by="name")
    if users:
        frappe.db.set_value("User", {"name": ["in", users]}, "default_workspace", None,
                            update_modified=False)
    return sorted(users)


def execute():
    import frappe

    pointing = frappe.get_all("User", filters={"default_workspace": ["is", "set"]},
                              fields=["name", "default_workspace"], order_by="name")
    missing = sorted({row["default_workspace"] for row in pointing
                      if not frappe.db.exists("Workspace", row["default_workspace"])})
    if not missing:
        print("clear_stale_default_workspace: every User's default_workspace exists; nothing to clear")
        return
    for workspace in missing:
        users = clear_default_workspace(workspace)
        print(f"clear_stale_default_workspace: cleared default_workspace on {len(users)} user(s) "
              f"pointing at missing Workspace {workspace!r}: {', '.join(users)}")
