"""Governed reference data — one write-through mechanism for the doctypes that
feed epm_staging.

Dimension Mapping and Cash Flow Category are edited freely as metadata and
become warehouse data only when Published. Each of them had the same four-line
``sync_doctype_filtered(... filters={"status": "Published"})`` call copied into
publish(), unpublish() and after_delete() — six copies across the two
controllers, two of them mis-indented — and each copy was free to disagree with
``reconcile_all``, which is exactly what happened: reconcile synced every row,
Draft and Inactive included, so one ``bench migrate`` re-filled the tables with
rows publish() would never send.

There is one sync here, it reads ``CH_SYNC_FILTERS``, and
``konsol.clickhouse.resolve_sync_filters`` reads the same attribute on the
reconcile path. The two cannot drift apart again.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype
from konsol.schema_lifecycle import check_epm_admin, request_governed_rebuild

_PUBLISHED = "Published"


class GovernedReferenceDocument(Document):
    """Base for a Draft/Published/Inactive doctype that writes through to
    ClickHouse.

    Subclass contract:

    ``CH_TABLE``        the epm_staging table it owns
    ``CH_FIELD_MAP``    {clickhouse_column: frappe_fieldname}
    ``CH_SYNC_FILTERS`` which rows belong in the warehouse (default: Published)
    ``BUILD_SCOPE``     optional dbt build scope for the governed rebuild
    """

    CH_SYNC_FILTERS = {"status": _PUBLISHED}
    BUILD_SCOPE = None

    # -- the one sync -------------------------------------------------------

    def _resync(self):
        """Re-send the warehouse-eligible rows of this doctype. The only copy."""
        return sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def _request_rebuild(self, action):
        if self.BUILD_SCOPE:
            return request_governed_rebuild(self, action, scope=self.BUILD_SCOPE)
        return request_governed_rebuild(self, action)

    # -- lifecycle ----------------------------------------------------------

    def on_update(self):
        """Any save that changes what the warehouse should hold re-syncs it.

        publish()/unpublish() are the governed path, but nothing forces a
        caller through them: a fixture, a data import or a script doing
        ``frappe.get_doc({...,"status":"Published"}).insert()`` writes a
        Published row that no hook ever sent to ClickHouse, and the crosswalk
        stayed short until the next migrate reconciled it. Verified in the F3
        end-to-end run.

        Editing a row that is neither now nor previously Published costs
        nothing, and ``sync_table`` no-ops during install/import/migrate, so
        fixture loading is unaffected.
        """
        before = self.get_doc_before_save()
        if self.status == _PUBLISHED or (before and before.status == _PUBLISHED):
            self._resync()

    @frappe.whitelist()
    def publish(self):
        """Publish: save (which re-syncs) + request a governed rebuild."""
        check_epm_admin()
        self._before_publish()
        self.status = _PUBLISHED
        self.save()
        self._request_rebuild("Publish")

    @frappe.whitelist()
    def unpublish(self):
        """Unpublish (Inactive): save (which re-syncs) + governed rebuild."""
        check_epm_admin()
        self.status = "Inactive"
        self.save()
        self._request_rebuild("Unpublish")

    def _before_publish(self):
        """Hook for subclass publish-readiness checks. Default: nothing."""

    def after_delete(self):
        """Re-sync after a *Published* row is removed, so it stops being applied.

        after_delete, not on_trash: on_trash runs before the row is gone, so the
        TRUNCATE+INSERT would put the deleted doc straight back. Skipped during
        install/migrate/import — reconcile_all repairs the table wholesale after
        migrate, and no build should be enqueued mid-migrate.
        """
        if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
            return
        if self.status == _PUBLISHED:
            self._resync()
            self._request_rebuild("Delete")
