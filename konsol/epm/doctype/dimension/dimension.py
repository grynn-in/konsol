"""Dimension — config doctype for EPM dimensions.

Saves are pure metadata — no side effects. Use Publish/Unpublish to apply
schema changes (DDL, dbt vars, budget fields) and request a governed full-scope
rebuild via Build Approval (preflight + approval + audit), not a direct
dbt build.
"""
import frappe
from frappe.model.document import Document

from konsol.schema_lifecycle import apply_and_rebuild, check_epm_admin
from konsol.tb_dimension_model import FLAG, _is_on, is_legal_dimension_name


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

    @frappe.whitelist()
    def publish(self):
        """Publish this dimension: apply schema + trigger dbt rebuild."""
        check_epm_admin()
        self.status = "Published"
        self.save()
        apply_and_rebuild(self, "Publish")

    @frappe.whitelist()
    def unpublish(self):
        """Unpublish (deactivate) this dimension: apply schema + trigger dbt rebuild."""
        check_epm_admin()
        self.status = "Inactive"
        self.save()
        apply_and_rebuild(self, "Unpublish")
