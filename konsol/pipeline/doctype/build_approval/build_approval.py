"""Build Approval controller.

Manages workflow transitions for governed dbt builds.
Low-risk scopes (staging) auto-approve; high-risk scopes require EPM Admin approval.
With the Build Approval Workflow active, a new row takes its Request transition
(after_insert); without one, before_save moves it (konsol#215).
"""
import frappe
from frappe.model.document import Document


# Scope → risk mapping
SCOPE_RISK = {
    "staging": "low",
    "actuals": "high",
    "scenarios": "high",
    "consolidation": "high",
    # konsol#182: a chart publish reclassifies every statement
    "chart": "high",
    "full": "high",
}


# konsol.build_lock.BUILD_WRITER_FLAG, by name: set only around konsol's own
# saves that move a build out of Running (build_lock.build_writer).
BUILD_WRITER_FLAG = "konsol_build_writer"
RUNNING_BUILD_MESSAGE = (
    "This build is running. Wait for it to finish, or for the reaper to fail it "
    "after 30 minutes, then reset it."
)


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
        # Only konsol's build path moves a build out of Running: the build
        # job's finish and stopped-before-dbt saves, marked with
        # build_lock.build_writer(), and the reaper, which writes with
        # SQL and so never reaches this hook. A manual move (a reset to
        # Draft, or to Pending Review) left the job, still alive, to finish
        # over it (#140 review). konsol #168 covers the other manual moves.
        if (before and before.workflow_state == "Running" and self.workflow_state != "Running"
                and not frappe.flags.get(BUILD_WRITER_FLAG)):
            frappe.throw(RUNNING_BUILD_MESSAGE, frappe.ValidationError, title="Build is running")
        # The one exception is the build starting (Approved -> Running): the
        # build reads every change absorbed so far, so the flag an Approved
        # build carried is spent (#140). A request after the start flags the
        # Running row again.
        starting = before and before.workflow_state == "Approved" and self.workflow_state == "Running"
        # Sent back to Draft to run again (below). A row that finished is the
        # other exception: its flag was spent already.
        resetting = before and self.workflow_state == "Draft" and before.workflow_state != "Draft"
        rerun = resetting and before.started_at and before.workflow_state in ("Completed", "Failed")
        if before and before.rebuild_requested and not self.rebuild_requested and not starting and not rerun:
            self.rebuild_requested = 1
        if resetting:
            if before.started_at:
                # A row that started runs again as new: its old start would
                # hide its next start failure from the sweep, and a lost job
                # from the reaper (#140 re-review). Version history keeps the
                # old values.
                self.started_at = None
                self.completed_at = None
                self.duration_seconds = 0
                if rerun:
                    # Finished (Completed, or Failed after it started): that
                    # run's finish, or the reaper, already requested the
                    # follow-up its flag asked for, so keeping it would
                    # duplicate that. A Cancelled row's flag was never acted
                    # on (its changes were dropped with it), so it keeps it.
                    # A Running row can't be reset (above).
                    self.rebuild_requested = 0
            # The old error goes, since a start-failure message left on a row
            # that runs again would make the failed-start sweep take it for a
            # new one (#140 review). A row that never started keeps its flag:
            # it hasn't built, and if its next start fails, the changes it
            # absorbed are still owed. Only the start spends it
            # (tasks.run_governed_build), so keeping it costs no build.
            self.error_message = None
            # The next move to Approved records who approved this run, not
            # the last one (konsol#215).
            self.approved_by = None
        self.risk_level = SCOPE_RISK.get(self.build_scope, "high")

        if not self.requested_by:
            self.requested_by = frappe.session.user

        # With the Build Approval Workflow active, a new row leaves Draft by
        # its Request transition (after_insert), so the workflow's roles and
        # conditions decide (konsol#215). A site without one keeps the old
        # auto-transition — only on first save or explicit state reset.
        if (not workflow_active() and self.workflow_state == "Draft"
                and (self.is_new() or self.has_value_changed("workflow_state"))):
            if self.risk_level == "low":
                self.workflow_state = "Approved"
                self.approved_by = "Administrator"
            else:
                self.workflow_state = "Pending Review"

        if self.workflow_state == "Approved" and not self.approved_by and self.has_value_changed("workflow_state"):
            self.approved_by = "Administrator" if self.risk_level == "low" else frappe.session.user

        # Populate sync info from EPM Settings
        self._populate_sync_info()

    def after_insert(self):
        """Take the workflow's Request transition on the new row (konsol#215)."""
        if workflow_active() and self._take_request():
            # Frappe runs on_update on this instance next: the Request's own
            # save already enqueued or notified, so it skips once.
            self.flags.request_applied = True

    def _take_request(self):
        """Move a Draft row on by the workflow's Request transition; True if taken.

        Request is konsol's step, not the user's: creating a Build Approval
        is already permission-checked (or deliberately ignore_permissions by
        konsol's own code), and get_transitions/apply_workflow check READ
        permission, which a user whose submission queued the request (an
        Entity Accountant) may not have. So it runs as Administrator.

        Applied to a fresh load, so the workflow saves it as an existing row
        moving from Draft (a new row may only be in the first state). That
        inner save's on_update enqueues the build or notifies the reviewers,
        and this instance is reloaded to hold the new state. A workflow that
        offers no Request leaves the row in Draft.
        """
        from frappe.model.workflow import apply_workflow, get_transitions

        from konsol.build_lock import as_administrator

        with as_administrator():
            fresh = frappe.get_doc("Build Approval", self.name)
            if fresh.workflow_state != "Draft":
                return False
            offered = any(t.get("action") == "Request" for t in get_transitions(fresh))
            if offered:
                apply_workflow(fresh, "Request")
        if not offered:
            frappe.msgprint(f"You may not request a {self.build_scope} build.")
            return False
        self.load_from_db()
        return True

    def on_update(self):
        """Post-save side effects: enqueue builds, notify on pending review.

        Only fires on the save where the state actually changed — editing an
        already-Approved doc won't re-enqueue a duplicate build.
        """
        # The Request's own save already did this (_take_request). Spent here:
        # frappe's load_from_db keeps flags, and a later save of this same
        # instance (control_api's Approve) must still enqueue.
        if self.flags.pop("request_applied", False):
            return
        if not self.has_value_changed("workflow_state"):
            return

        # Run Again: Draft is a debounce state the reaper does not watch, so
        # a row left there would absorb every later request for its scope and
        # never build. It moves on at once by Request (konsol#215 review 2),
        # whose save does the enqueue or notify. No request_applied here:
        # this on_update is already past it, and a flag left set would skip
        # a later save of this instance.
        before = self.get_doc_before_save()
        if (self.workflow_state == "Draft" and before
                and before.workflow_state in ("Completed", "Failed", "Cancelled")):
            if workflow_active():
                self._take_request()
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


def workflow_active():
    """Whether the site has an active Workflow for Build Approval."""
    return bool(frappe.db.get_value("Workflow", {"document_type": "Build Approval", "is_active": 1}))


def governed_build_job_id(name):
    """The RQ job id of a Build Approval's governed build."""
    return f"konsol-governed-build::{name}"

