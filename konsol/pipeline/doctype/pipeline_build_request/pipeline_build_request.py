"""Pipeline Build Request controller.

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


class PipelineBuildRequest(Document):
    def before_save(self):
        """Auto-set risk level, apply workflow transitions, populate sync info.

        State transitions happen here so we can set self.workflow_state directly
        (written with the save — no recursion, no db_set).
        """
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
        )
        frappe.logger().info(
            f"Governed build enqueued: {self.name} (scope={self.build_scope})"
        )
