"""Measure — config doctype for EPM measures.

A save that changes what dbt reads applies the schema. That is a save that
moves the measure into or out of Published, or that edits, while Published,
a field the schema step reads (``SCHEMA_FIELDS``). It runs
``apply_and_rebuild``: dbt vars (``base_measures``), then a governed
full-scope rebuild via Build Approval (preflight + approval + audit), not a
direct dbt build. Measures add no ClickHouse column. Any other save is
metadata only and requests nothing.

Until 11 October 2026 every save was metadata only and only Publish /
Unpublish applied the schema, so a Measure that became Published another way
(a config bundle with ``status: Published``, the Desk status field, REST, a
data import) left ``base_measures`` stale. Deepak Pai decided on konsol#299,
option #299-1: a Measure gets the same ``on_update`` transition hook as
Dimension (konsol#295). Rejected: #299-2, publish() only, documented.

Publish / Unpublish now set the status and save; the save does the rest, once.
Install, migrate and patches apply nothing per row; install.regenerate_dbt_vars
rewrites the vars once at their end.
Writes that bypass the controller (``frappe.db.set_value``, raw SQL in a
patch) still apply nothing; run Apply Schema after one.
"""
import frappe
from frappe.model.document import Document

from konsol.schema_lifecycle import apply_and_rebuild, check_epm_admin


#: The fields ``dbt_config._build_measures_vars`` reads off a Published
#: Measure. A save that changes one of them while Published applies the
#: schema (#299). It also reads measure_name, which is not listed because a
#: save cannot change it: autoname is field:measure_name, and Frappe's
#: ``_sync_autoname_field`` puts it back to the name before the write.
#: Add a field here when that reader starts reading it.
SCHEMA_FIELDS = ("expression", "label", "cube_type")


class Measure(Document):

    def on_update(self):
        """Apply the schema when this save changed what dbt reads (#299).

        ``on_update``: Frappe runs it on insert and on save alike, after the
        row is written in this transaction, so ``regenerate_vars`` reads the
        new row, and ``flags.ignore_validate`` does not skip it.

        The admin check is here and not only in ``publish()``: otherwise a
        user who may not press Publish could reach the same rebuild by
        editing the status field. A save that applies nothing is not
        checked, so metadata edits keep the doctype's own permissions.
        """
        # The Build Approval this save queued or debounced onto, for publish()
        # and unpublish() to return (as Dimension, konsol#301). Reset first,
        # so a save that applies nothing never reports an earlier one.
        self.flags.build_approval = None
        # Not inside install, migrate or a patch (#351 review): seeding the
        # defaults ran this once per Published row, each a ClickHouse apply
        # and a full-refresh Build Approval. install.regenerate_dbt_vars
        # rewrites the vars once, file only, at the end of install and
        # migrate instead.
        if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
            return
        action = self._schema_action()
        if not action:
            return
        check_epm_admin()
        self.flags.build_approval = apply_and_rebuild(self, action)

    def _schema_action(self):
        """"Publish", "Unpublish" or None: what this save does to the schema.

        Read against the row as it stood before the save (Frappe's
        ``get_doc_before_save``; None on insert, which counts as not
        Published). ``flags.apply_schema`` is set by ``publish()`` and
        ``unpublish()`` so a re-publish with nothing changed still applies:
        that is the repair schema_lifecycle documents for a failed apply.
        """
        before = self.get_doc_before_save()
        was = bool(before) and before.get("status") == "Published"
        now = self.status == "Published"
        forced = self.flags.pop("apply_schema", False)
        if now and (forced or not was or self._schema_fields_changed(before)):
            return "Publish"
        if (was and not now) or forced:
            return "Unpublish"
        return None

    def _schema_fields_changed(self, before):
        """Whether a field in SCHEMA_FIELDS differs from `before`."""
        for field in SCHEMA_FIELDS:
            if (before.get(field) or "") != (self.get(field) or ""):
                return True
        return False

    @frappe.whitelist()
    def publish(self):
        """Publish this measure. The save applies the schema, once (#299).

        Returns the Build Approval the save queued, or the pending one it was
        debounced onto.
        """
        check_epm_admin()
        self.status = "Published"
        self.flags.apply_schema = True
        try:
            self.save()
        finally:
            # A save refused before on_update must not leave the flag to
            # force the next, unrelated save of this object.
            self.flags.pop("apply_schema", None)
        return self.flags.build_approval

    @frappe.whitelist()
    def unpublish(self):
        """Unpublish (deactivate) this measure. The save applies the schema, once (#299).

        Returns the Build Approval the save queued, or the pending one it was
        debounced onto.
        """
        check_epm_admin()
        self.status = "Inactive"
        self.flags.apply_schema = True
        try:
            self.save()
        finally:
            # A save refused before on_update must not leave the flag to
            # force the next, unrelated save of this object.
            self.flags.pop("apply_schema", None)
        return self.flags.build_approval
