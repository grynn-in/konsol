"""Dimension — config doctype for EPM dimensions.

Saves are pure metadata — no side effects. Use Publish/Unpublish to apply
schema changes (DDL, dbt vars, budget fields) and request a governed full-scope
rebuild via Build Approval (preflight + approval + audit), not a direct
dbt build.
"""
import frappe
from frappe.model.document import Document

from konsol.schema_lifecycle import apply_and_rebuild, check_epm_admin
from konsol.tb_dimension_model import is_legal_dimension_name


class Dimension(Document):

    def validate(self):
        """Refuse a dimension_name the rest of the system cannot use (#255).

        The name is not a label: it IS the ClickHouse column and it IS the
        trial-balance header, so ``dim_`` plus lower-case letters, digits and
        underscores is the whole of what it may be. That rule already exists
        twice — ``_SAFE_TB_DIM_COLUMN`` in ``schema_apply``, which decides
        whether the column can be spelled at all, and ``_LEGAL_DIMENSION_NAME``
        in ``tb_dimension_model``, which decides whether a file may carry the
        header — so this refusal IMPORTS it rather than restating it. A third
        copy is how the field drifted in the first place (``_SAFE_IDENTIFIER``,
        schema_apply.py:276, is a fourth and looser rule on the same field).

        Until now nothing checked the name where it is typed. ``dim_Cost_Center``
        saved happily, was refused at upload time and silently skipped at
        column-sync time, so the admin declared a dimension, ticked its flags,
        published it and uploaded a file before learning the name was never
        usable — with the error pointing at the file rather than at the name.

        The name is REFUSED, never corrected. Lowercasing or stripping on the
        admin's behalf would let ``dim_Cost_Center`` and ``dim_cost_center``
        collapse onto one column and put two dimensions' values in one place —
        a silent-data bug worse than the refusal it would replace.
        """
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
                f"dimension name. It must be the four characters dim_ followed "
                f"by one or more lower-case letters, digits or underscores, and "
                f"nothing else — no capitals, spaces, punctuation or surrounding "
                f"blanks. Rename it; konsol will not lower-case or trim it for "
                f"you, because two dimensions differing only in case would end "
                f"up sharing one column.",
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
