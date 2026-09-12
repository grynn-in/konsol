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
import frappe


def lock_build_requests():
    frappe.db.sql("SELECT name FROM `tabDocType` WHERE name = 'Build Approval' FOR UPDATE")
