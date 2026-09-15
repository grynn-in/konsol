"""One lock for every build request (#133 review).

Both debounces (tasks.on_consolidation_doc_update and
schema_lifecycle._request_governed_build) are check-then-insert. They take this
lock, then read the pending Build Approvals with FOR UPDATE, then insert.

It locks ONE row that always exists: Build Approval's own DocType row.
- Not the requested scope's Build Scope row: "full" has none (the controller
  forbids one), so that locked only a gap, and two simultaneous full requests
  deadlocked (1213, reproduced live). An empty Build Scope table did the same
  for every scope.
- Not every Build Scope row in name order: each migrate re-imports the
  build_scope fixture, deleting and re-inserting the rows in FILE order in one
  transaction, so the two orders could deadlock (#133 re-review).
Normal reads of tabDocType don't lock. The row is written only by a migrate
syncing a changed Build Approval JSON (it commits per file) or a developer-mode
save of the DocType, and each holds it only until its own commit. One row can't deadlock against itself, and
it serialises only a few milliseconds of work: build requests are rare.
"""
from contextlib import contextmanager

import frappe

# Set only around konsol's own saves that move a Build Approval out of Running
# (the build job's finish and its stopped-before-dbt path). The start-failure
# save is marked too, defensively: it only ever moves Approved -> Failed.
# BuildApproval.before_save refuses any other move off Running (#140). It
# lives in frappe.flags, which is local to one request or job.
BUILD_WRITER_FLAG = "konsol_build_writer"


@contextmanager
def build_writer():
    """Mark the saves inside as konsol's build path, which may move a Build
    Approval out of Running, and make them as Administrator: the Build
    Approval Workflow gives the build job's transitions (Start, Fail to
    Start, Complete, Fail) to the Administrator role only (konsol#215).

    Both are restored on exit, error or not, so they can't leak past the
    save they wrap. frappe.set_user rewrites the session in place (user, sid,
    data) and clears form_dict, so the caller's session is put back whole
    (as schema_apply._switch_to_administrator does)."""
    previous = frappe.flags.get(BUILD_WRITER_FLAG)
    session = frappe.local.session
    saved = {"user": session.user, "sid": session.sid, "data": session.data}
    form_dict = frappe.local.form_dict
    frappe.flags[BUILD_WRITER_FLAG] = True
    frappe.set_user("Administrator")
    try:
        yield
    finally:
        frappe.set_user(saved["user"])   # also resets the permission caches
        session.update(saved)
        frappe.local.form_dict = form_dict
        frappe.flags[BUILD_WRITER_FLAG] = previous


def lock_build_requests():
    frappe.db.sql("SELECT name FROM `tabDocType` WHERE name = 'Build Approval' FOR UPDATE")


# Every state the debounce absorbs into.
FLAGGED_STATES = ("Draft", "Pending Review", "Approved", "Running")


def flag_running_build(row):
    """A request absorbed by a pending or Running build: flag it (#129, #140).

    Running: the build may already have read its inputs, so the change that
    asked would miss gold. The flag makes the build request one more when it
    finishes (tasks._finish_governed_build).

    Draft, Pending Review, Approved: the build hasn't read anything yet, but
    it may fail to start, and then nothing reads the change (#140). A high-risk
    scope can wait in Pending Review for hours. Only the build's start clears
    the flag (it reads everything absorbed so far), so the flag costs no
    extra build; a start failure, a lost job or a reset to Draft keeps it,
    and the reaper requests the build it owes.

    ``row`` comes from the debounce's locking read: this transaction already
    holds it. It doesn't bump ``modified``: the job holds the doc and saves it
    at the end, and a newer stamp would fail that save.
    """
    if row.get("workflow_state") in FLAGGED_STATES:
        frappe.db.sql("UPDATE `tabBuild Approval` SET rebuild_requested = 1 WHERE name = %s", row["name"])

