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
  (ancestor group, entity, link on one PATH between them), so dbt can resolve a
  multi-level ownership chain at a given date without a recursive CTE. This is
  what makes consolidation multi-level: before F2 the tree was stored and never
  traversed, so GROUP_CORP's consolidated result contained only its own direct
  entities and none of GROUP_EMEA's, at any percentage.

konsol#117 pair 1 (Deepak Pai, 7 Nov 2026, option A): ownership is a GRAPH. The
links are Ownership Period rows (owner node -> owned node); the tree stays the
reporting structure (which group's chart, policy and currency present a node),
and ``build_rows`` enumerates every path over the links from every node that
defines a group code (the root, or a node whose tree parent carries another
code; a node sharing its parent's code is a stop on paths, not a start). An
entity reached by two paths gets two sets of rows with distinct ``path_id``, so
dbt multiplies within a path and sums across paths. A link row with no owner
(every row until task G's patch fills ``owner_node``) is the tree link, so on a
tree the closure is exactly what it was.
"""
import frappe
from frappe.utils.nestedset import NestedSet

from konsol.clickhouse import after_commit_once, sync_doctype_after_commit, sync_table
from konsol.ownership_graph_model import ownership_edges


class OwnershipCycle(ValueError):
    """The ownership links form a cycle, so the closure has no finite path set.
    OwnershipPeriod.validate refuses the link that would close one (konsol#117
    design, section 3; task B); this is the build-time backstop for a cycle
    that reached the data another way, named so the reconcile log says which."""

    def __init__(self, labels):
        self.labels = list(labels)
        super().__init__(
            "Ownership links form a cycle: " + " → ".join(self.labels) + ". "
            "Cross-holdings are refused until a treasury-share rule is decided "
            "(konsol#117); the closure was not rebuilt."
        )


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
        # konsolidat#198 (design 1a): the group root's Consolidation Policy.
        # The warehouse column nci_measurement is read from the doctype's
        # goodwill_method, which keeps its fieldname and is the NCI
        # measurement (konsol#226: column first, field second).
        "nci_measurement": "goodwill_method",
        "accounting_framework": "accounting_framework",
        "framework_note": "framework_note",
        "goodwill_treatment": "goodwill_treatment",
        "goodwill_amortisation_years": "goodwill_amortisation_years",
        "acquisition_costs_treatment": "acquisition_costs_treatment",
        "measurement_period": "measurement_period",
        "bargain_purchase": "bargain_purchase",
        # konsolidat#198 (design 1): the declared accounts the consolidation
        # journals post to. dbt never assumes a code.
        "goodwill_account": "goodwill_account",
        "fair_value_adjustment_account": "fair_value_adjustment_account",
        "investment_account": "investment_account",
        "nci_account": "nci_account",
        "bargain_purchase_gain_account": "bargain_purchase_gain_account",
        "disposal_gain_loss_account": "disposal_gain_loss_account",
        "disposal_proceeds_account": "disposal_proceeds_account",
        "goodwill_amortisation_expense_account": "goodwill_amortisation_expense_account",
        "acquisition_costs_account": "acquisition_costs_account",
        # konsolidat#171: the last warehouse column (an existing table gets it
        # appended by _ADDED_COLUMNS).
        "partial_period_treatment": "partial_period_treatment",
    }

    # PRD-8 / F2: the link closure, computed by a path walk over the links.
    # konsol#117 (task C): path_id names the path, link_owner_* the node each
    # link leaves. The column order is init-db.sql's (test_ownership_graph_ddl.py).
    CH_STAGING_TABLE = "epm_staging.consolidation_ancestry"
    CH_STAGING_COLUMNS = [
        "consolidation_group", "data_area_id",
        "link_group", "link_data_area_id", "link_depth", "depth", "path",
        "path_id", "link_owner_group", "link_owner_data_area_id",
    ]

    # The flat hierarchy view, kept for structure reporting. It no longer
    # carries an ownership column — see the module note.
    CH_HIERARCHY_TABLE = "epm_staging.consolidation_hierarchy"
    CH_HIERARCHY_COLUMNS = [
        "consolidation_group", "data_area_id", "parent_group",
        "hierarchy_level", "path",
    ]

    def validate(self):
        self._validate_not_rekeyed_under_ownership()
        self._validate_entity_in_one_node()
        self._validate_reporting_currency()
        self._validate_ic_difference()
        self._validate_policy()
        self._validate_method_declarations()

    def _validate_method_declarations(self):
        """The group's Method Declarations are on a node without an entity,
        name a method the warehouse computes, and give each entity one method
        on any date.

        konsol#117 pair 1 (task D): the method a group declares for an entity
        its links reach by more than one path, read by dbt only where the
        links alone do not decide it (design comment section 2). The rules and
        their sentences are konsol.method_declaration_model; every problem is
        thrown at once, like the policy check. A declaration for an entity
        reached by one path is not refused here: the warehouse reports it (it
        may be made ahead of a link that arrives later).
        """
        from konsol.method_declaration_model import declaration_problems

        problems = declaration_problems(
            self.consolidation_group, self.data_area_id,
            getattr(self, "method_declarations", None))
        if problems:
            frappe.throw("<br>".join(problems))

    def _validate_policy(self):
        """The Consolidation Policy is complete once the group has a deal,
        consistent with its framework, and its accounts are Published leaves.

        konsolidat#198 (design 1a): policy is configured here with no
        defaults, and only the mechanics are programmed — the rules live in
        konsol.consolidation_policy_model, which returns the sentences thrown
        here. Group nodes only: the fields are shown on them alone.
        """
        if not self.is_group:
            return
        # Imported here, like group_chart below: the rule module is pure and
        # host-tested, and a module-level import would tie every loader of
        # this controller to it.
        from konsol.consolidation_policy_model import (
            ACCOUNT_FIELDS, account_problems, policy_problems,
        )

        # The deal doctypes shipped after this did, and a migrate runs
        # validate before every table exists: read each only when its table
        # is there. A Business Disposal is a deal too — it posts to the
        # declared accounts (PR #202 review, finding 8). A cancelled deal
        # (docstatus 2) is not a deal.
        live = {"consolidation_group": self.consolidation_group, "docstatus": ["<", 2]}
        has_deals = bool(
            (frappe.db.table_exists("Business Combination")
             and frappe.db.exists("Business Combination", live))
            or (frappe.db.table_exists("Business Disposal")
                and frappe.db.exists("Business Disposal", live))
        )
        problems = policy_problems(self, has_deals)
        # Every account that IS set must be a Published leaf of the chart;
        # which ones are required is a per-deal question the deal answers.
        chosen = [f for f in ACCOUNT_FIELDS if (getattr(self, f, None) or "").strip()]
        problems += account_problems(self, chosen, self._is_published_leaf)
        if problems:
            frappe.throw("<br>".join(problems))

    @staticmethod
    def _is_published_leaf(account):
        row = frappe.db.get_value("Main Account", account, ["status", "is_group"])
        return bool(row) and row[0] == "Published" and not row[1]

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
        from konsol.group_chart import chart_codes

        if account not in chart_codes():
            frappe.throw(f"{account} is not in the group chart.")

    def on_update(self):
        self._warn_if_no_ownership_period()
        super().on_update()
        self._queue_sync()

    def on_trash(self):
        # F8 (#290): before the tree check, so the sentence names the periods.
        periods = self._ownership_periods_naming(self.consolidation_group, self.data_area_id)
        if periods:
            frappe.throw(
                f"Consolidation Group node {self.name} is named by Ownership Period "
                f"{', '.join(periods)}. Cancel or delete those periods first: deleting "
                f"the node would leave ownership that covers nothing.")
        super().on_trash()   # the tree check; it may refuse the delete

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so
        the full-table re-send put it straight back (#120)."""
        self._queue_sync()

    def _queue_sync(self):
        """After the commit, once per transaction (konsol#124).

        The node's row, the closure, and the node's Method Declarations
        (konsol#117 task D): a child table has no hooks of its own, so the
        parent queues its sync, on save and on delete alike (a deleted node's
        rows die with it and must leave the warehouse). Imported here, like
        the rule modules: the child controller imports frappe's Document, and
        the host tests load this module against a stub frappe.
        """
        from konsol.consolidation.doctype.consolidation_method_declaration.consolidation_method_declaration import (
            ConsolidationMethodDeclaration,
        )

        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        after_commit_once(("consolidation_hierarchy",), type(self).resync_staging)
        after_commit_once(("consolidation_method_declarations",),
                          ConsolidationMethodDeclaration.resync_staging)

    # -- validation ---------------------------------------------------------

    @staticmethod
    def _ownership_periods_naming(group, entity):
        """The draft and submitted Ownership Periods that name the node
        ``(group, entity)``, by name.

        F8 (#290): Ownership Period.consolidation_group is Data, not a Link (a
        node's name is CG-{group}-{entity}), so Frappe's link check never sees
        it. A cancelled period is not counted: it covers nothing already. A
        draft is counted (judgement): it would fail its own node check later.
        A blank Link is stored as NULL, so {"data_area_id": ""} would match no
        group node; ["is", "not set"] matches both spellings
        (ownership_period._BLANK).
        """
        blank = ["is", "not set"]
        return frappe.get_all(
            "Ownership Period",
            filters={"consolidation_group": group,
                     "data_area_id": entity or blank,
                     "docstatus": ["<", 2]},
            pluck="name",
            order_by="name asc",
        )

    def _validate_not_rekeyed_under_ownership(self):
        """A saved node keeps its (group, entity) while a period names it.

        F8 (#290): the pair is how an Ownership Period finds its node, and
        neither field is set_only_once, so an edit would orphan the period
        exactly as a delete would. A blank entity read back as NULL and posted
        as "" is the same entity.
        """
        before = self.get_doc_before_save()
        if before is None:
            return
        old = (before.consolidation_group or "", before.data_area_id or "")
        if old == (self.consolidation_group or "", self.data_area_id or ""):
            return
        periods = self._ownership_periods_naming(*old)
        if periods:
            frappe.throw(
                f"Consolidation Group node {self.name} is named by Ownership Period "
                f"{', '.join(periods)} as {old[0]} / {old[1] or '(group node)'}. Cancel or "
                f"delete those periods first: changing the node's group or entity would "
                f"leave ownership that covers nothing.")

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

    @classmethod
    def _links(cls):
        """Every submitted ownership link, as ``build_rows`` reads it: rows with
        the owned node (``consolidation_group``, ``data_area_id``) and the
        owner's node name (``owner_node``).

        Submitted only: a draft is not yet a link and a cancelled one is gone,
        which is also what the ``ownership_periods`` sync sends, so the closure
        and the percentages dbt joins to it describe the same graph.

        konsol#117 task A adds ``owner_node`` in parallel with this (task C):
        on a stack where the column does not exist yet nothing is read, every
        node falls back to its tree parent in ``build_rows``, and the closure
        is today's. The same fallback covers a row whose owner is blank until
        task G's patch fills it.
        """
        if not frappe.db.has_column("Ownership Period", "owner_node"):
            return []
        return frappe.get_all(
            "Ownership Period",
            filters={"docstatus": 1},
            fields=["consolidation_group", "data_area_id", "owner_node"],
            limit_page_length=0,
        )

    @staticmethod
    def _label(node):
        """How a node reads in a path: its entity code, else its group code.

        A leaf's ``consolidation_group`` is its PARENT's code, so using it for
        both produced paths like `GROUP_CORP/GROUP_EMEA/GROUP_EMEA/DEMF` —
        the middle segment repeated.
        """
        return node.data_area_id or node.consolidation_group

    @classmethod
    def build_rows(cls, docs, by_name, links=()):
        """Return (hierarchy_rows, ancestry_rows). Pure — no ClickHouse, no I/O.

        Split out from resync_staging so the graph arithmetic is testable
        without a site: the ancestry is the part consolidation correctness now
        rests on.

        ``links`` are Ownership Period rows (``consolidation_group``,
        ``data_area_id`` = the owned node; ``owner_node`` = the owner's node
        name), as ``_links`` reads them. A node named by at least one link with
        an owner is owned by exactly those owners; a node named by none (every
        node before task A's field exists and task G's patch fills it, konsol#117
        task C) is owned by its tree parent, so a tree without links yields
        today's rows. Two periods on one (owner, owned) edge are one link: the
        closure is undated, dbt resolves each link's dates. A link naming a node
        that does not exist is skipped (validate refuses it; a stale row must
        not stop every other node's closure).

        The ancestry is a depth-first enumeration of every path along the
        links from every node that DEFINES a group code: the root, or a node
        whose tree parent carries a different ``consolidation_group``. A row is
        keyed by the ancestor's code, not its node, so a node sharing its
        parent's code (a leaf, or an entity-carrying node with children, as 27
        live nodes are) is a stop on paths, never a start: starting there too
        put a second path_id under the same (code, entity) key for the one
        holding, and dbt's path sum counted it twice (task H, 7 Nov 2026:
        the live share came out as 2..5 and the in-range dbt test failed).
        At each entity on a path, one row per link on the path from the
        starting node: the ancestor's group, the entity, the link (the node it
        reaches), its depth on the path, the path's length, the entity's TREE
        path (``path``, unchanged in meaning), ``path_id`` (the node labels
        from the ancestor to the entity, unique per path) and the node the
        link leaves. A node met twice on one path is a cycle, refused by name
        (OwnershipCycle): task B refuses it at validate, this is the backstop.
        """
        hierarchy = []
        tree_path = {}
        for d in docs:
            ancestors = cls._get_ancestors(d, by_name)  # nearest-first
            chain = [by_name[a] for a in ancestors]
            path = "/".join([cls._label(n) for n in reversed(chain)]
                            + [cls._label(d)])
            tree_path[d.name] = path
            hierarchy.append([
                d.consolidation_group,
                d.data_area_id or "",
                by_name[d.parent_consolidation_group].consolidation_group
                if d.parent_consolidation_group and d.parent_consolidation_group in by_name
                else "",
                len(ancestors) + 1,
                path,
            ])

        # owner node name -> owned node names: the links, and the tree parent
        # for a node no link names. ONE rule, shared with the Ownership
        # Period's cycle check (PR #459 re-review, finding A).
        children = ownership_edges(docs, links)

        ancestry = []
        for start in docs:
            parent = by_name.get(start.parent_consolidation_group)
            if parent is not None and parent.consolidation_group == start.consolidation_group:
                continue  # shares its parent's code: a stop on paths, not a start
            # Each stack entry is a path from ``start``: the nodes on it (start
            # first) and the labels that name it.
            stack = [[start.name]]
            while stack:
                path_nodes = stack.pop()
                for child in children[path_nodes[-1]]:
                    if child in path_nodes:
                        cycle = path_nodes[path_nodes.index(child):] + [child]
                        raise OwnershipCycle(cls._label(by_name[n]) for n in cycle)
                    nodes = path_nodes + [child]
                    d = by_name[child]
                    # Ancestry is about where an ENTITY's numbers roll to. A
                    # group node is a destination, never a contributor; the walk
                    # continues below it.
                    if d.data_area_id:
                        path_id = "/".join(cls._label(by_name[n]) for n in nodes)
                        hops = nodes[1:]
                        for link_depth, name in enumerate(hops, start=1):
                            link = by_name[name]
                            owner = by_name[nodes[link_depth - 1]]
                            ancestry.append([
                                start.consolidation_group,
                                d.data_area_id,
                                link.consolidation_group,
                                link.data_area_id or "",
                                link_depth,
                                len(hops),
                                tree_path[child],
                                path_id,
                                owner.consolidation_group,
                                owner.data_area_id or "",
                            ])
                    stack.append(nodes)
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
        links = cls._links()
        hierarchy, ancestry = cls.build_rows(docs, by_name, links)
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
