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
        """Auto-set risk level and populate sync info from EPM Settings."""
        self.risk_level = SCOPE_RISK.get(self.build_scope, "high")

        if not self.requested_by:
            self.requested_by = frappe.session.user

        # Populate sync info from EPM Settings
        self._populate_sync_info()

    def on_update(self):
        """Handle workflow transitions.

        - Draft with low risk → auto-approve → enqueue build
        - Draft with high risk → Pending Review
        - Approved → enqueue build

        Uses db_set() for state transitions to avoid recursive on_update.
        """
        if self.workflow_state == "Draft":
            if self.risk_level == "low":
                # Auto-approve: update state without re-triggering on_update
                frappe.db.set_value(
                    self.doctype, self.name,
                    {"workflow_state": "Approved", "approved_by": "Administrator"},
                    update_modified=True,
                )
                self.reload()
                self._enqueue_build()
            else:
                frappe.db.set_value(
                    self.doctype, self.name,
                    "workflow_state", "Pending Review",
                    update_modified=True,
                )
                frappe.publish_realtime(
                    "build_request_pending",
                    {"name": self.name, "scope": self.build_scope},
                )

        elif self.workflow_state == "Approved":
            self._enqueue_build()

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
