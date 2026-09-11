"""Consolidation Group — the consolidation STRUCTURE. Not ownership.

PRD-8: Multi-level hierarchy via Frappe's native tree (is_tree=1, lft/rgt)
PRD-13: Goodwill method (partial/full)

F2: ownership_pct and consolidation_method are gone from this doctype. Ownership
is temporal and lives in exactly one place, Ownership Period — a group's share of
a subsidiary changes on a date, and a doctype field cannot say when. Carrying the
percentage here as well gave two grains with no rule about which won, and the dbt
side invented one (`if(x != 0, ...)`) that quietly treated a real 0% as "unset".

This controller now publishes two things, both pure structure:

* ``epm_gold.consolidation_groups`` — one row per node (who and where).
* ``epm_staging.consolidation_ancestry`` — the LINK CLOSURE. One row per
  (ancestor group, entity, link on the chain between them), so dbt can resolve a
  multi-level ownership chain at a given date without a recursive CTE. This is
  what makes consolidation multi-level: before F2 the tree was stored and never
  traversed, so GROUP_CORP's consolidated result contained only its own direct
  entities and none of GROUP_EMEA's, at any percentage.
"""
import frappe
from frappe.utils.nestedset import NestedSet

from konsol.clickhouse import sync_doctype, sync_table


class ConsolidationGroup(NestedSet):
    # Structure only. reporting_currency is the group's presentation currency
    # and belongs to the node; ownership does not.
    CH_TABLE = "epm_gold.consolidation_groups"
    CH_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "data_area_id": "data_area_id",
        "entity_name": "entity_name",
        "reporting_currency": "reporting_currency",
    }

    # PRD-8 / F2: the link closure, computed by a tree walk.
    CH_STAGING_TABLE = "epm_staging.consolidation_ancestry"
    CH_STAGING_COLUMNS = [
        "consolidation_group", "data_area_id",
        "link_group", "link_data_area_id", "link_depth", "depth", "path",
    ]

    # The flat hierarchy view, kept for structure reporting. It no longer
    # carries an ownership column — see the module note.
    CH_HIERARCHY_TABLE = "epm_staging.consolidation_hierarchy"
    CH_HIERARCHY_COLUMNS = [
        "consolidation_group", "data_area_id", "parent_group",
        "hierarchy_level", "path",
    ]

    def on_update(self):
        super().on_update()
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        self._sync_hierarchy()

    def on_trash(self):
        super().on_trash()
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        self._sync_hierarchy()

    # -- the tree walk ------------------------------------------------------

    @classmethod
    def _nodes(cls):
        """Every group node, keyed by Frappe doc name."""
        docs = frappe.get_all(
            "Consolidation Group",
            fields=["name", "consolidation_group", "data_area_id",
                    "parent_consolidation_group"],
            limit_page_length=0,
        )
        return docs, {d.name: d for d in docs}

    @staticmethod
    def _label(node):
        """How a node reads in a path: its entity code, else its group code.

        A leaf's ``consolidation_group`` is its PARENT's code, so using it for
        both produced paths like `GROUP_CORP/GROUP_EMEA/GROUP_EMEA/DEMF` —
        the middle segment repeated.
        """
        return node.data_area_id or node.consolidation_group

    @classmethod
    def build_rows(cls, docs, by_name):
        """Return (hierarchy_rows, ancestry_rows). Pure — no ClickHouse, no I/O.

        Split out from resync_staging so the tree arithmetic is testable
        without a site: the ancestry is the part consolidation correctness now
        rests on.
        """
        hierarchy, ancestry = [], []
        for d in docs:
            ancestors = cls._get_ancestors(d, by_name)  # nearest-first
            chain = [by_name[a] for a in ancestors]
            path = "/".join([cls._label(n) for n in reversed(chain)]
                            + [cls._label(d)])
            hierarchy.append([
                d.consolidation_group,
                d.data_area_id or "",
                by_name[d.parent_consolidation_group].consolidation_group
                if d.parent_consolidation_group and d.parent_consolidation_group in by_name
                else "",
                len(ancestors) + 1,
                path,
            ])

            # Ancestry is about where an ENTITY's numbers roll to. A group node
            # is a destination, never a contributor.
            if not d.data_area_id:
                continue
            for i, ancestor in enumerate(chain):
                # Nodes strictly below this ancestor, top-down, ending at self.
                links = list(reversed(chain[:i])) + [d]
                for link_depth, link in enumerate(links, start=1):
                    ancestry.append([
                        ancestor.consolidation_group,
                        d.data_area_id,
                        link.consolidation_group,
                        link.data_area_id or "",
                        link_depth,
                        len(links),
                        path,
                    ])
        return hierarchy, ancestry

    @classmethod
    def resync_staging(cls, force=False):
        """Rebuild the ancestry closure and the flat hierarchy from every node.

        Returns what ``sync_table`` wrote for the ancestry — None when the write
        failed or was skipped.
        """
        docs, by_name = cls._nodes()
        hierarchy, ancestry = cls.build_rows(docs, by_name)
        sync_table(cls.CH_HIERARCHY_TABLE, cls.CH_HIERARCHY_COLUMNS, hierarchy,
                   force=force)
        return sync_table(cls.CH_STAGING_TABLE, cls.CH_STAGING_COLUMNS, ancestry,
                          force=force)

    def _sync_hierarchy(self):
        """PRD-8: kept as the hook on_update/on_trash call; the work lives in
        resync_staging() so the reconcile can run it without an instance."""
        type(self).resync_staging()

    @staticmethod
    def _get_ancestors(doc, by_name):
        """Walk parent_consolidation_group links to root; ancestors nearest-first."""
        ancestors = []
        current = doc.parent_consolidation_group
        seen = set()
        while current and current in by_name and current not in seen:
            seen.add(current)
            ancestors.append(current)
            current = by_name[current].parent_consolidation_group
        return ancestors
