"""Build Approval controller.

Manages workflow transitions for governed dbt builds.
Low-risk scopes (staging) auto-approve; high-risk scopes require EPM Admin approval.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


# Scope → risk mapping
SCOPE_RISK = {
    "staging": "low",
    "actuals": "high",
    "scenarios": "high",
    "consolidation": "high",
    "full": "high",
}


class BuildApproval(Document):
    def before_save(self):
        """Auto-set risk level, apply workflow transitions, populate sync info.

        State transitions happen here so we can set self.workflow_state directly
        (written with the save — no recursion, no db_set).
        """
        # A request absorbed by this build sets rebuild_requested without
        # bumping modified (build_lock.flag_running_build), so a form opened
        # earlier passes the timestamp check. A save must never clear it
        # (#139 review). _doc_before_save was loaded FOR UPDATE by
        # check_if_latest, so it holds the current flag.
        before = self.get_doc_before_save()
        # The one exception is the build starting (Approved -> Running): the
        # build reads every change absorbed so far, so the flag an Approved
        # build carried is spent (#140). A request after the start flags the
        # Running row again.
        starting = before and before.workflow_state == "Approved" and self.workflow_state == "Running"
        if before and before.rebuild_requested and not self.rebuild_requested and not starting:
            self.rebuild_requested = 1
        # Sent back to Draft to run again: that run builds everything, so the
        # follow-up the flag promised would be a duplicate.
        if before and self.workflow_state == "Draft" and before.workflow_state != "Draft":
            self.rebuild_requested = 0
        self.risk_level = SCOPE_RISK.get(self.build_scope, "high")

        if not self.requested_by:
            self.requested_by = frappe.session.user

        # Workflow transitions — only on first save or explicit state reset
        if self.workflow_state == "Draft" and (self.is_new() or self.has_value_changed("workflow_state")):
            if self.risk_level == "low":
                self.workflow_state = "Approved"
                self.approved_by = "Administrator"
            else:
                self.workflow_state = "Pending Review"

        # Populate sync info from EPM Settings
        self._populate_sync_info()

    def on_update(self):
        """Post-save side effects: enqueue builds, notify on pending review.

        Only fires on the save where the state actually changed — editing an
        already-Approved doc won't re-enqueue a duplicate build.
        """
        if not self.has_value_changed("workflow_state"):
            return

        if self.workflow_state == "Approved":
            self._enqueue_build()
        elif self.workflow_state == "Pending Review":
            frappe.publish_realtime(
                "build_request_pending",
                {"name": self.name, "scope": self.build_scope},
            )

    def _populate_sync_info(self):
        """Read Airbyte sync status from EPM Settings into display fields."""
        try:
            settings = frappe.get_single("EPM Settings")
            self.sync_time_display = str(settings.last_airbyte_sync_at or "Never")
            self.sync_status_display = settings.last_airbyte_sync_status or "Unknown"
            self.sync_rows_display = str(settings.last_airbyte_sync_rows or 0)
        except Exception:
            self.sync_time_display = "N/A"
            self.sync_status_display = "N/A"
            self.sync_rows_display = "N/A"

    def _enqueue_build(self):
        """Enqueue the governed dbt build as a background job."""
        frappe.enqueue(
            "konsol.tasks.run_governed_build",
            queue="default",
            timeout=600,
            build_request=self.name,
            # After the commit, not now. This runs inside the transaction that
            # inserted or approved this row (since #128 often inside a job).
            # Enqueued immediately, an idle worker could start the build before
            # the commit, fail on DoesNotExist, and leave the row Approved, a
            # state the debounce treats as in-flight forever (#125, #128 review).
            enqueue_after_commit=True,
            # Named, so the reaper can ask RQ whether it is still waiting (#125).
            # deduplicate: skip if that job is still queued or running, and
            # otherwise delete the finished one first. RQ reuses the old key
            # with its 10-minute result TTL, so a re-approval soon after a run
            # could expire before a worker reached it and never build.
            job_id=governed_build_job_id(self.name),
            deduplicate=True,
        )
        frappe.logger().info(
            f"Governed build enqueued: {self.name} (scope={self.build_scope})"
        )


def governed_build_job_id(name):
    """The RQ job id of a Build Approval's governed build."""
    return f"konsol-governed-build::{name}"

