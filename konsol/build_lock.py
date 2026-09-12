"""One lock for every build request (#133 review).

Both debounces (tasks.on_consolidation_doc_update and
schema_lifecycle._request_governed_build) are check-then-insert. They take this
lock, then read the pending Build Approvals with FOR UPDATE, then insert.

It locks EVERY Build Scope row, in name order, rather than the requested
scope's row. The "full" scope has no Build Scope row (the controller forbids
one), so a per-scope lock on it locked nothing but a gap, and two simultaneous
full requests deadlocked (1213, reproduced live). One lock in one order can't
deadlock against itself, needs no row per scope, and serialises only a few
milliseconds of work: build requests are rare.
"""
import frappe


def lock_build_requests():
    frappe.db.sql("SELECT name FROM `tabBuild Scope` ORDER BY name FOR UPDATE")
