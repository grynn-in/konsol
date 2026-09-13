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

from konsol.clickhouse import after_commit_once, sync_doctype_after_commit, sync_table


class ConsolidationGroup(NestedSet):
    # Structure only. reporting_currency is the group's presentation currency
    # and belongs to the node; ownership does not.
    CH_TABLE = "epm_gold.consolidation_groups"
    CH_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "data_area_id": "data_area_id",
        "entity_name": "entity_name",
        "reporting_currency": "reporting_currency",
        # konsol#159 (decision 4): where a group books the difference left
        # when a pair's two sides don't match, and the tolerance it is shown
        # against. Group nodes only.
        "ic_difference_account": "ic_difference_account",
        "ic_difference_tolerance": "ic_difference_tolerance",
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

    def validate(self):
        self._validate_entity_in_one_node()
        self._validate_reporting_currency()
        self._validate_ic_difference()

    def _validate_ic_difference(self):
        """The intercompany-difference account belongs to a group node, is not
        itself intercompany (its postings would be eliminated in turn), and
        the tolerance is not negative."""
        self.ic_difference_account = (self.ic_difference_account or "").strip()
        has_settings = bool(self.ic_difference_account or float(self.ic_difference_tolerance or 0))
        if not self._is_group_node():
            if has_settings:
                frappe.throw("Only a group node books intercompany differences; "
                             "set the account and tolerance on the group.")
            return
        # gold_ic_reconciliation reads a group's settings from the group's own
        # row, the one with no entity (data_area_id = ''), as
        # gold_consolidated_trial_balance does its reporting currency (#172).
        # On a group node that also carries an entity they would be ignored
        # silently (#173 re-review), so they are refused there. #172 does not
        # refuse such a node, so neither does this; the group needs a node
        # without an entity to carry them (re-review L6).
        if self.data_area_id:
            if has_settings:
                frappe.throw(f"This node carries entity {self.data_area_id}, and consolidation reads a "
                             "group's intercompany difference settings only from the group's node "
                             "without an entity (the one it reads the reporting currency from). The "
                             f"group {self.consolidation_group} needs such a node to carry them.")
            return
        if float(self.ic_difference_tolerance or 0) < 0:
            frappe.throw("The intercompany difference tolerance cannot be negative.")
        if not self.ic_difference_account:
            return
        # Only when the account changes: an unchanged one was checked when it
        # was set, and Intercompany Account refuses to flag a difference
        # account afterwards. The chart check reads ClickHouse and refuses when
        # it is down, which must not block every other edit of the node.
        before = self.get_doc_before_save()
        if before and (before.ic_difference_account or "").strip() == self.ic_difference_account:
            return
        self._check_new_difference_account()

    def _check_new_difference_account(self):
        """A new difference account is not an Intercompany Account and is in
        the group chart.

        #173 re-review L5: this and Intercompany Account's publish check read
        each other's rows, so a group setting account X and a publish flagging
        X at once could each pass. Both take the same serialising lock first
        (Intercompany Account's tabDocType row, the konsol.build_lock pattern),
        then read by equality on indexed columns with FOR UPDATE, which reads
        the latest committed rows under REPEATABLE READ.
        """
        account = self.ic_difference_account
        if frappe.db.table_exists("Intercompany Account"):
            frappe.db.sql("SELECT `name` FROM `tabDocType` WHERE `name` = %s FOR UPDATE",
                          ("Intercompany Account",))
            flagged = []
            for column in ("main_account", "counterpart_account"):
                flagged += frappe.db.sql(
                    f"SELECT `name` FROM `tabIntercompany Account` WHERE `{column}` = %s "
                    "AND `status` = 'Published' FOR UPDATE", (account,), as_dict=True)
            if flagged:
                frappe.throw(
                    f"{account} is an Intercompany Account ({', '.join(sorted({f.name for f in flagged}))}). "
                    "Book differences to an account that is not eliminated itself.")
        # In the group chart (#173 review, A3).
        from konsol.tb_bulk import _chart_accounts

        if account not in _chart_accounts():
            frappe.throw(f"{account} is not in the group chart.")

    def on_update(self):
        self._warn_if_no_ownership_period()
        super().on_update()
        self._queue_sync()

    def on_trash(self):
        super().on_trash()   # the tree check; it may refuse the delete

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so
        the full-table re-send put it straight back (#120)."""
        self._queue_sync()

    def _queue_sync(self):
        """After the commit, once per transaction (konsol#124)."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        after_commit_once(("consolidation_hierarchy",), type(self).resync_staging)

    # -- validation ---------------------------------------------------------

    def _validate_entity_in_one_node(self):
        """An entity belongs to exactly one node of the tree.

        Nothing enforced this before: the autoname is
        ``CG-{consolidation_group}-{data_area_id}``, so CG-GROUP_CORP-DEMF and
        CG-GROUP_EMEA-DEMF could coexist, and each simply contributed to its own
        group. Since F2 that is far worse — every ancestor of BOTH nodes gets a
        chain for the same entity, so a shared ancestor counts it twice, and
        gold_entity_ownership groups on (group, entity, period) so the two
        chains' links merge into one nonsensical product. The live stack had
        exactly this shape after a stray `dbt seed`.
        """
        if not self.data_area_id:
            return  # roll-up node: no entity to duplicate
        other = frappe.db.exists(
            "Consolidation Group",
            {"data_area_id": self.data_area_id, "name": ["!=", self.name]},
        )
        if other:
            frappe.throw(
                f"Entity '{self.data_area_id}' is already a node of the "
                f"consolidation tree ({other}). An entity belongs to one node: "
                f"two would make every shared ancestor consolidate it twice."
            )

    def _is_group_node(self):
        """A node that consolidates: marked a group, or carrying no entity.

        gold_consolidated_trial_balance reads a group's currency from its row
        with no entity, so a row without one is a group node whatever is_group
        says. The tree dialog (consolidation_group_tree.js) and the JSON's
        mandatory_depends_on use the same rule."""
        return bool(self.is_group or not self.data_area_id)

    def _validate_reporting_currency(self):
        """A group node must name the currency it presents in (konsolidat#93).

        gold_consolidated_trial_balance takes a group's reporting currency from
        its node (the row with no entity) and translates every entity below it
        directly into that currency. A group node with none translated at
        whatever the rate lookup made of '' — the 1.0 parity fallback, a JPY
        ledger landing as if it were USD. The field used to be free text with
        a USD default on every row, so nothing noticed.

        The value itself is a Link to ISO Currency, so Frappe checks that the
        code exists (and corrects its case). An entity row's value is not read.
        """
        if self.reporting_currency or not self._is_group_node():
            return
        frappe.throw(
            f"Consolidation group node '{self.consolidation_group}' needs a Reporting "
            f"Currency: it is the currency the group presents its consolidated "
            f"statements in, and every entity below it is translated into it.",
            frappe.MandatoryError,
        )

    def _warn_if_no_ownership_period(self):
        """Say so when a non-root node has no ownership yet.

        ``ownership_pct`` was ``reqd: 1`` on this doctype, so a link could never
        lack a percentage. Ownership now lives in Ownership Period, which cannot
        be required here — the node has to exist before a period can name it —
        so a subsidiary created without one silently contributes nothing to
        every consolidated statement. assert_ownership_chain_complete fails the
        dbt build over it, but that is hours later and somewhere else.
        """
        if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_import:
            return
        if not self.parent_consolidation_group:
            return  # root: 100% by construction
        blank = ["is", "not set"]
        if frappe.db.exists("Ownership Period", {
            "consolidation_group": self.consolidation_group,
            "data_area_id": self.data_area_id or blank,
            "docstatus": 1,
        }):
            return
        frappe.msgprint(
            f"No Ownership Period for this node yet, so nothing below it will "
            f"consolidate. Add one covering the periods you close.",
            title="Ownership missing", indicator="orange",
        )

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

        Returns the ancestry row count, or None when EITHER write failed or was
        skipped. Both, because reconcile_all records one entry per controller:
        returning only the ancestry's result would report a clean reconcile on a
        migrate where the hierarchy write was refused — the reporting bug
        `_record` exists to prevent.
        """
        docs, by_name = cls._nodes()
        hierarchy, ancestry = cls.build_rows(docs, by_name)
        wrote_hierarchy = sync_table(
            cls.CH_HIERARCHY_TABLE, cls.CH_HIERARCHY_COLUMNS, hierarchy, force=force)
        wrote_ancestry = sync_table(
            cls.CH_STAGING_TABLE, cls.CH_STAGING_COLUMNS, ancestry, force=force)
        if wrote_hierarchy is None:
            return None
        return wrote_ancestry

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
