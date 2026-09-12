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

konsol#110: Entity writes through to ``epm_staging.entities``, the governed
entity registry. The warehouse used to learn which entities exist, and what
currency each keeps its books in, only from ERP extraction
(silver_legal_entities) — so a subsidiary with no connector, submitting its
trial balance as a file, had no row there and consolidation INNER JOINed it
away. Identity now flows down from konsol; the ERP is the fallback.
"""

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet

from konsol.clickhouse import sync_doctype


class Entity(NestedSet):
    nsm_parent_field = "parent_entity"

    # Every row is sent — groups and Disposed entities included. A disposed
    # entity still has historical periods to consolidate, and dbt decides what
    # to read (silver_entity_currencies takes leaves only).
    #
    # The warehouse column is accounting_currency because that is the name the
    # consolidation joins on; the field is functional_currency because that is
    # what an accountant calls it. Same split as Entity Fiscal Calendar's
    # erp_data_area -> data_area_id.
    CH_TABLE = "epm_staging.entities"
    CH_FIELD_MAP = {
        "data_area_id": "name",
        "entity_name": "entity_name",
        "parent_entity": "parent_entity",
        "is_group": "is_group",
        "status": "status",
        "accounting_currency": "functional_currency",
        "country": "country",
        "erp_source": "erp_source",
    }

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

    def on_update(self):
        # Nested-set bookkeeping first: if the tree update refuses, nothing
        # should reach the warehouse. Fires on insert as well as save.
        super().on_update()
        self._resync()

    def after_delete(self):
        """after_delete, NOT on_trash.

        ``sync_doctype`` re-sends the whole table from ``frappe.get_all``, and
        on_trash runs BEFORE the row is removed — so a delete would re-publish
        the entity it just deleted (konsol#120). NestedSet.on_trash still runs
        untouched; it is what refuses to delete a node that has children.
        """
        self._resync()

    def after_rename(self, olddn, newdn, merge=False):
        """rename_doc never calls on_update.

        It rewrites ``entity_code`` (the autoname field) and every child's
        ``parent_entity`` with raw SQL, so without this the warehouse keeps the
        old code — and the children point at a parent it no longer has — until
        the next migrate reconciles. Entity has allow_rename off, but
        rename_doc(force=True) and merges still arrive here.
        """
        super().after_rename(olddn, newdn, merge)
        self._resync()

    def _resync(self):
        """The one sync call. frappe.get_all ignores permissions, so a user
        whose User Permissions scope them to one entity still re-sends every
        row — a TRUNCATE+INSERT of only the rows they can see would delete the
        rest of the registry."""
        return sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

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
