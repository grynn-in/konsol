from frappe.model.document import Document


class TrialBalanceUpload(Document):
    """One bulk trial balance file and what became of it.

    Status, counts and the report are written by konsol/tb_bulk.py (check,
    then load), never typed: every field but the file is read-only. Each
    ready entity-period becomes an ordinary Trial Balance Submission, which
    is where the rules live.
    """

    def after_delete(self):
        """Keep the TBU- counter above this name: Frappe reverts it on
        delete, and the next upload would reuse the name (PR #603 F1)."""
        from konsol.name_series import keep_series_above

        keep_series_above("TBU-", self.name)

    @property
    def loaded_count(self):
        """Virtual: the submissions this upload loaded that are submitted
        now (konsol#332-2). Never stored; an old column of that name on an
        existing site is no longer read or written."""
        from konsol.tb_bulk import upload_state

        return upload_state(self)["loaded_count"]

    def onload(self):
        """The form's indicator is the upload as it stands now, sent as
        `status_now` (konsol#332-2-D1, Deepak Pai 12 Oct): an upload whose
        submissions were cancelled reads Partly Loaded or Cancelled. The
        stored status is the load job's own state, shown as "Job status" and
        never rewritten here."""
        from konsol.tb_bulk import upload_state

        self.set_onload("status_now", upload_state(self)["status"])
