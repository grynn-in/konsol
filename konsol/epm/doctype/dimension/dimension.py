"""Dimension — config doctype for EPM dimensions.

A save that changes what the warehouse sees applies the schema. That is a
save that moves the dimension into or out of Published, or that edits, while
Published, a field the schema step reads (``SCHEMA_FIELDS``). It runs
``apply_and_rebuild``: DDL, dbt vars, budget fields, then a governed
full-scope rebuild via Build Approval (preflight + approval + audit), not a
direct dbt build. Any other save is metadata only and requests nothing.

Until 25 September 2026 every save was metadata only and only Publish /
Unpublish applied the schema. So a Dimension that became Published another
way got no warehouse column: a config bundle (``upsert_dimension`` with
``status: Published``), the Desk form's status field, REST or a data import.
Deepak Pai decided on konsol#295: "A dimension that ends up Published gets its
column (and its dbt vars) automatically, however it got there." Rejected:
keeping saves side-effect free and making the bundle tell the admin to run
Apply Schema, a second step every onboarding would have to remember.

Publish / Unpublish now set the status and save; the save does the rest, once.
Writes that bypass the controller (``frappe.db.set_value``, raw SQL in a
patch) still apply nothing; run Apply Schema after one.
"""
import frappe
from frappe.model.document import Document

from konsol.schema_lifecycle import apply_and_rebuild, check_epm_admin
from konsol.tb_dimension_model import FLAG, _is_on, is_legal_dimension_name


#: The setting whose ON branch konsol declares and has not built. Named here
#: rather than spelled inline so the refusal below and its deletion are one
#: grep apart. See ``_refuse_unimplemented_survives_close``.
SURVIVES_CLOSE = "survives_close"

#: The field's label, as the form shows it. The message quotes this, not the
#: fieldname: the label is the only name of this setting an admin has seen.
SURVIVES_CLOSE_LABEL = "Survives Year-End Close"

#: The fields ``apply_schema_for_publish`` reads off a Published Dimension. A
#: save that changes one of them while Published applies the schema (#295).
#: Where each is read:
#:   dbt_config._build_dimensions_vars: dimension_name, source_column, label,
#:     cube_type, in_budget, allocation_role (``var('dimensions')``)
#:   schema_apply._apply_clickhouse_columns: dimension_name, cube_type
#:   schema_apply._sync_tb_dimension_columns: dimension_name, in_trial_balance
#:   schema_apply._sync_budget_custom_fields: dimension_name, label, in_budget
#: Add a field here when one of those readers starts reading it.
SCHEMA_FIELDS = (
    "dimension_name", "source_column", "label", "cube_type", "allocation_role",
)
#: The Check fields among them, compared with the intake's ``_is_on``. A Check
#: that arrived through REST or CSV is the TEXT "0", which is the stored 0.
SCHEMA_FLAGS = ("in_budget", FLAG)


class Dimension(Document):

    def validate(self):
        """Refuse a trial-balance dimension the warehouse cannot spell (#255).

        Scope first, because the rule below is not a rule about dimensions —
        it is a rule about trial-balance COLUMNS. A Dimension ticked
        ``in_trial_balance`` gets a ``dim_<name>`` column created for it and a
        header matched against it, and for that its name must be ``dim_``
        plus lower-case letters, digits and underscores, and nothing else.
        A Dimension that is NOT so ticked creates no such column, and konsol's
        other consumers of this same registry require no prefix at all:
        ``schema_apply`` spells fact-table columns with ``_SAFE_IDENTIFIER``
        (schema_apply.py:276 and :445, a looser rule that wants a plain
        lower-case identifier and no prefix) and
        ``budget_grain.budget_dimension_names()`` hands ``dimension_name``
        straight to Budget Line fieldnames. ``business_unit`` is a legal name
        for both and is what the existing suite already tests the budget grain
        against (test_hierarchy_node_ambiguity.py:332).

        Applying the column rule to the whole registry was an over-reach with
        teeth: ``unpublish()`` does its work through ``self.save()``, so a
        budget-only ``business_unit`` could not be RETIRED — the one operation
        that un-declaring a dimension depends on — and
        ``config_service.apply_config``, which has no per-row error handling,
        would abort a whole bundle on the first such row.

        The name rule itself already exists twice — ``_SAFE_TB_DIM_COLUMN`` in
        ``schema_apply``, which decides whether the column can be spelled at
        all, and ``_LEGAL_DIMENSION_NAME`` in ``tb_dimension_model``, which
        decides whether a file may carry the header — so this refusal IMPORTS
        it rather than restating it. A third copy is how the field drifted in
        the first place. The reading of the Check flag is imported from the
        same module for the same reason: a row that came through JSON, CSV or
        REST carries the TEXT ``"0"``, which is truthy in Python, and a
        controller that disagreed with the intake about what "ticked" means
        would hold a dimension the intake ignores to the intake's rule.

        Until now nothing checked the name where it is typed. ``dim_Cost_Center``
        saved happily, was refused at upload time and silently skipped at
        column-sync time, so the admin declared a dimension, ticked its flags,
        published it and uploaded a file before learning the name was never
        usable — with the error pointing at the file rather than at the name.

        Scoping the rule must not reopen that hole from the other side. Ticking
        ``in_trial_balance`` on an existing, perfectly good budget name is a
        save, and it is refused here like any other — naming the flag as the
        reason, because "rename it" is unanswerable to someone who did not
        touch the name, and offering unticking as the other way out, because
        renaming a budget dimension rewrites every Budget Line fieldname built
        from it and may well be the wrong fix.

        The name is REFUSED, never corrected. Lowercasing or stripping on the
        admin's behalf would let ``dim_Cost_Center`` and ``dim_cost_center``
        collapse onto one column and put two dimensions' values in one place —
        a silent-data bug worse than the refusal it would replace.

        Then refuse ``survives_close = 1``, which konsol declares and has not
        built. TEMPORARY: DELETE this refusal, its message and its tests when
        the year-end close learns to carry dimension values onto retained
        earnings (konsol#255). Until then the close implements the OFF branch
        only — the P&L collapses into one undimensioned retained-earnings row,
        23 September 2026, Deepak Pai — and nothing in konsol reads the field,
        so a ticked box changes no number anywhere. The field's
        ``depends_on: eval:doc.in_trial_balance`` cannot prevent that: it is
        form-level, and a patch, fixture, REST call or data import persists
        the tick without passing through a form at all. konsol#247's rule
        applies — refuse, or report; never accept and ignore — and refusing is
        the half that belongs at the point of entry.

        The message says the feature is unbuilt rather than the value invalid,
        because the admin did not mistype: they asked for something real that
        konsol declares and does not provide, and are owed both that fact and
        what the close does in the meantime. The tick is refused, never
        cleared, for the same reason the name is never corrected: a box that
        quietly unticks itself is the silent no-op wearing a different hat.

        Scoped to ``in_trial_balance`` with the early return above, which does
        double duty here. Outside the trial balance the setting is not unbuilt
        but meaningless — no ``dim_`` column, no close touching the dimension,
        and the field not even shown — and refusing there would trap a site
        that already holds a ticked one: unticking ``in_trial_balance`` is a
        save, and so is ``unpublish()``, so the stale tick could neither be
        walked away from nor retired.
        """
        if not _is_on(getattr(self, FLAG, 0)):
            # Not declared for the trial balance: no dim_ column is created
            # for it and no header is matched against it, so the trial
            # balance's naming rule has nothing to say about this dimension.
            return

        if not is_legal_dimension_name(self.dimension_name):
            frappe.throw(
                # repr, so padding and other invisible characters are visible:
                # a name wrong only in its whitespace must not read as correct.
                #
                # No example name here on purpose: a concrete dim_* literal in
                # a shipped string is a customer's dimension in konsol's code
                # (konsol#287), and the shape below says the same thing without
                # naming anyone's.
                f"Dimension name {self.dimension_name!r} is not a legal "
                f"trial-balance dimension name. This dimension is declared for "
                f"the trial balance (Include in Trial Balance is ticked), so "
                f"konsol has to create a warehouse column and match a file "
                f"header of exactly that name: it must be the four characters "
                f"dim_ followed by one or more lower-case letters, digits or "
                f"underscores, and nothing else — no capitals, spaces, "
                f"punctuation or surrounding blanks. Either rename it, or leave "
                f"the name alone and untick Include in Trial Balance if this "
                f"dimension belongs only to budgets or fact tables, where no "
                f"prefix is required. konsol will not lower-case or trim the "
                f"name for you, because two dimensions differing only in case "
                f"would end up sharing one column.",
                frappe.ValidationError,
            )

        # The flag is read with the intake's ``_is_on``, not ``bool()``: a
        # Check that arrived through JSON, CSV or REST carries the TEXT "0",
        # which is truthy in Python, so a bool() reading would refuse the
        # saves of rows whose box is plainly empty.
        if _is_on(getattr(self, SURVIVES_CLOSE, 0)):
            frappe.throw(
                f"{SURVIVES_CLOSE_LABEL} is not implemented yet, so konsol "
                f"will not store it ticked. Nothing is wrong with what you "
                f"asked for — the option is real and the field is shipped on "
                f"purpose — but no part of konsol reads it. The year-end "
                f"close currently zeroes every P&L balance into a single row "
                f"of retained earnings that carries no dimension values at "
                f"all, whatever this box says, so saving it ticked would "
                f"leave you with a setting that changes no number anywhere. "
                f"Untick {SURVIVES_CLOSE_LABEL} and leave it off until the "
                f"close is built to honour it (konsol#255); when that lands, "
                f"this refusal is deleted and the box will mean what its "
                f"label says.",
                frappe.ValidationError,
            )

    def on_update(self):
        """Apply the schema when this save changed what the warehouse sees (#295).

        ``on_update``, not ``validate`` or ``before_save``: Frappe runs it on
        insert and on save alike, after the row is written in this
        transaction, so ``apply_schema_for_publish`` reads the new row. It is
        not skipped by ``flags.ignore_validate`` either, as those two are.

        The admin check is here and not only in ``publish()``: otherwise a
        user who may not press Publish could reach the same DDL and rebuild
        by editing the status field. A save that applies nothing is not
        checked, so metadata edits keep the doctype's own permissions.
        """
        action = self._schema_action()
        if not action:
            return
        check_epm_admin()
        apply_and_rebuild(self, action)

    def _schema_action(self):
        """"Publish", "Unpublish" or None: what this save does to the schema.

        Read against the row as it stood before the save (Frappe's
        ``get_doc_before_save``; None on insert, which counts as not
        Published). ``flags.apply_schema`` is set by ``publish()`` and
        ``unpublish()`` so a re-publish with nothing changed still applies:
        that is the repair schema_lifecycle documents for a failed DDL.
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
        """Whether a field in SCHEMA_FIELDS or SCHEMA_FLAGS differs from `before`."""
        for field in SCHEMA_FIELDS:
            if (before.get(field) or "") != (self.get(field) or ""):
                return True
        for field in SCHEMA_FLAGS:
            if _is_on(before.get(field)) != _is_on(self.get(field)):
                return True
        return False

    @frappe.whitelist()
    def publish(self):
        """Publish this dimension. The save applies the schema, once (#295)."""
        check_epm_admin()
        self.status = "Published"
        self.flags.apply_schema = True
        try:
            self.save()
        finally:
            # A save refused before on_update must not leave the flag to
            # force the next, unrelated save of this object.
            self.flags.pop("apply_schema", None)

    @frappe.whitelist()
    def unpublish(self):
        """Unpublish (deactivate) this dimension. The save applies the schema, once (#295)."""
        check_epm_admin()
        self.status = "Inactive"
        self.flags.apply_schema = True
        try:
            self.save()
        finally:
            # A save refused before on_update must not leave the flag to
            # force the next, unrelated save of this object.
            self.flags.pop("apply_schema", None)
