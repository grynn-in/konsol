"""Entity — the legal entities and roll-up nodes the group consolidates.

Until now an entity was a bare string. ``data_area_id`` appeared as a Data
field on six doctypes with no validation, nothing to link to, and nowhere to
record what an entity *is* — its functional currency, its country, which ERP
its ledger comes from, whether it is still trading. There was also nothing for
a Frappe User Permission to point at, which is why entity-level access control
had to be hand-rolled in ``api.py`` and defaults to unrestricted.

``entity_code`` is deliberately the same string the warehouse uses for
``data_area_id``. The name is different because the concept is: in Frappe this
is an entity, in ClickHouse it is a partition key. Keeping the value identical
is what lets the two sides join without a mapping table.

This is the *management* hierarchy — a strict tree of divisions and regions.
Legal ownership is a DAG (an entity can have several parents through split
holdings or a JV) and does not belong here; that is Entity Ownership's job.
"""

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet


class Entity(NestedSet):
    nsm_parent_field = "parent_entity"

    def before_naming(self):
        """Normalise before the name is derived, not after.

        `autoname: field:entity_code` computes the name from the field *before*
        validate runs, and the field is then kept in sync with the name — so
        normalising in validate is silently undone. Doing it here means the
        code and the record name are the same normalised string.
        """
        self._normalise_code()

    def validate(self):
        # Also here, for renames and edits that never go through naming.
        self._normalise_code()
        self._guard_self_parent()
        self._guard_leaf_parenting()

    def _normalise_code(self):
        """The code is a join key, so whitespace and case drift break joins
        silently rather than loudly."""
        if self.entity_code:
            self.entity_code = self.entity_code.strip().upper()

    def _guard_self_parent(self):
        if self.parent_entity and self.parent_entity == self.name:
            frappe.throw(_("An entity cannot be its own parent."))

    def _guard_leaf_parenting(self):
        """Only roll-up nodes may have children. A legal entity holding another
        entity is an ownership relationship, not a management one, and belongs
        in Entity Ownership."""
        if self.is_group or not self.parent_entity:
            return
        parent_is_group = frappe.db.get_value("Entity", self.parent_entity, "is_group")
        if not parent_is_group:
            frappe.throw(
                _("{0} is a legal entity, so it cannot be a parent. "
                  "Mark it as a group, or record the relationship as ownership instead.")
                .format(self.parent_entity)
            )
