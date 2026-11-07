"""Historical Equity Rate — IAS 21 historical FX rates for equity accounts.

PRD-10: Equity accounts translated at the rate on the date the equity was
acquired/established, rather than closing rate.

konsol#374 (option b; Deepak Pai, 7 Nov 2026): a rate converts the entity's
functional currency into ONE presentation currency, stored in ``to_currency``
and part of the key. A sub-group that presents in EUR and its parent that
presents in USD each need their own rate for the same entity and account; the
warehouse matches a rate on (owning group, entity, account, to_currency)
against each consolidating group's reporting currency. Rejected: one
presentation currency per tree (refuses a common structure) and a warning on
the form only (leaves a wrong rate at one level).
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype_after_commit
from konsol.period_status import assert_open_between, first_period_affected
from konsol.epm.budget_grain import digest_name

_BLANK = ["is", "not set"]
_NODE_FIELDS = ["name", "consolidation_group", "parent_consolidation_group", "reporting_currency"]


@frappe.whitelist()
def presentation_currencies(consolidation_group):
    """The currencies a rate recorded under ``consolidation_group`` may convert
    into: [(group code, reporting currency)] for the group's own node and every
    ancestor, nearest first.

    Those are exactly the groups that consolidate an entity owned by this
    group, so they are the only readers of its rates
    (gold_consolidated_trial_balance joins eo.reporting_currency =
    hr.to_currency). The root node of a group is its Consolidation Group row
    with no entity (consolidation_journal._root); parent_consolidation_group
    is the Frappe name of the parent node. Returns [] when the group has no
    root row. A cycle or a dangling parent ends the walk.
    """
    node = frappe.db.get_value(
        "Consolidation Group",
        {"consolidation_group": consolidation_group, "data_area_id": _BLANK},
        _NODE_FIELDS, as_dict=True)
    out, seen = [], set()
    while node and node.name not in seen:
        seen.add(node.name)
        out.append((node.consolidation_group, node.reporting_currency))
        if not node.parent_consolidation_group:
            break
        node = frappe.db.get_value(
            "Consolidation Group", node.parent_consolidation_group, _NODE_FIELDS, as_dict=True)
    return out


class HistoricalEquityRate(Document):
    CH_TABLE = "epm_staging.historical_equity_rates"
    CH_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "data_area_id": "data_area_id",
        "main_account": "main_account",
        "rate_date": "rate_date",
        "historical_rate": "historical_rate",
        "to_currency": "to_currency",
    }

    # The From currency, for the form (Deepak Pai, 5 Nov 2026: the form
    # "doesnt show rate what to what"). Virtual: computed on read, never
    # stored, so there is no second copy of the entity's currency to drift.
    # The To currency is STORED (konsol#374): it is part of the key, a tree
    # may present in several currencies, and the user chooses which this rate
    # converts into. The rate reads 1 From = rate To; the warehouse multiplies
    # the local amount by it (gold_consolidated_trial_balance,
    # translation_rate).
    @property
    def from_currency(self):
        if not self.data_area_id:
            return None
        return frappe.db.get_value("Entity", self.data_area_id, "functional_currency")

    def autoname(self):
        """Collision-safe name for the (group, entity, account, date, to_currency) grain.

        The keys are free text and were packed into a `format:` name that
        could blow Frappe's 140-char cap and silently truncate/collide
        (grynn-in/konsolidat#92, finding #6). digest_name keeps a readable head
        and appends a hash of the exact key tuple so distinct grains never
        collide. to_currency is in the tuple (konsol#374): the EUR and the USD
        rate for one tranche are two documents.
        """
        self.name = digest_name(
            "HER",
            [self.consolidation_group, self.data_area_id,
             self.main_account, self.rate_date, self.to_currency],
        )

    def validate(self):
        self._validate_positive_rate()
        self._validate_references()
        self._validate_to_currency()

    def _validate_positive_rate(self):
        """Reject a non-positive historical rate (#92, finding #4).

        A 0/negative FX rate is never valid and would silently zero-out or
        sign-flip translated equity. Duplicate tranches need no separate guard:
        autoname() derives the document name deterministically from the
        (group, entity, account, rate_date, to_currency) tuple, so a second
        rate for the same tranche and currency collides on the primary key and
        is rejected at insert.
        """
        if self.historical_rate is None or float(self.historical_rate) <= 0:
            frappe.throw(
                "Historical Rate must be a positive number.",
                frappe.ValidationError,
            )

    def _validate_references(self):
        """Referential integrity on the free-text keys (#92, finding #3).

        `consolidation_group` is a Data field, not a Link — a clean Link target
        does not exist (Consolidation Group is named `CG-{group}-{entity}`, and
        the dbt join keys on the bare code, so the stored value must stay the
        bare code). A typo therefore silently misses the exact-string dbt join
        and drops the equity account back to the closing rate with no error.
        Enforce existence here instead, against the Consolidation Group
        registry (the app's source of truth for membership).

        `data_area_id` IS a Link (to Entity, reqd), and is still checked here
        anyway, because existing as an Entity is not the same as being in the
        consolidation tree. The Link proves only that the code names an Entity
        row; what the gold join needs is a code some Consolidation Group row
        carries. The two sets differ by construction:
        `patches/backfill_entities_from_consolidation_group.py` creates one
        Entity per group node named after the *group* code (`code =
        data_area_id if not is_group else consolidation_group`), so a group
        code passes the Link while being an entity on no node. That is the
        second reachable bad input, and why the refusal below says "on no node"
        rather than "not a node" — a group code IS a node.

        Membership, not leafness, is the entity test (konsol#240). The check
        matches any Consolidation Group row carrying this `data_area_id`, with
        no is_group clause: `_is_group_node()` (consolidation_group.py) is
        `is_group or not data_area_id`, so carrying an entity code is what
        makes a row an entity row, whatever the flag says. A regional holding
        company with its own share capital, its own trial balance and
        subsidiaries beneath it is a parent AND an entity, and on the live
        site 27 of the 28 group nodes are exactly that. Rollup nodes carry no
        `data_area_id`, so dropping the clause still excludes them.

        `main_account` IS a Link, to Main Account, and the difference is the
        naming: Main Account is named `field:main_account`, so its name is the
        bare account code itself. The stored value is byte for byte what it
        always was and the dbt join is untouched — exactly what a Link to
        Consolidation Group could not promise. The check below stays anyway: it
        names the offending account and says what an unmatched key costs, and it
        still fires on the write paths that skip Frappe's own link validation
        (a patch, an import, `ignore_links`).

        Existence is checked *independently* (the group is a known group; the
        entity is a known entity). The (group, entity) *pair* is intentionally
        NOT enforced yet: the Consolidation Group doctype currently diverges from
        the dbt `consolidation_groups` seed that gold actually joins on
        (grynn-in/konsolidat#130), so a pair check would reject seed-correct
        keys. Pair enforcement lands once that divergence is resolved.
        """
        if self.consolidation_group and not frappe.db.exists(
            "Consolidation Group", {"consolidation_group": self.consolidation_group}
        ):
            frappe.throw(
                f"Unknown consolidation group '{self.consolidation_group}'. "
                "It must exist in Consolidation Group — an unmatched key silently "
                "drops the equity rate to the closing rate in gold.",
                frappe.ValidationError,
            )
        if self.data_area_id and not frappe.db.exists(
            "Consolidation Group", {"data_area_id": self.data_area_id}
        ):
            frappe.throw(
                f"Entity '{self.data_area_id}' is on no node of the consolidation "
                "tree. It must be an entity assigned to a Consolidation Group "
                "node — a group code names a node but is not an entity on one; "
                "an unmatched key silently drops the account to the closing rate "
                "in gold, which is the wrong number, not an error.",
                frappe.ValidationError,
            )
        if self.main_account and not frappe.db.exists("Main Account", self.main_account):
            frappe.throw(
                f"Unknown account '{self.main_account}'. It must exist in Main "
                "Account — an unmatched key silently drops the account to the "
                "closing rate in gold, which is the wrong number, not an error.",
                frappe.ValidationError,
            )

    def _validate_to_currency(self):
        """The To currency is one some consolidating group presents in (konsol#374).

        gold_consolidated_trial_balance applies this rate where the
        consolidating group's reporting_currency equals to_currency, and the
        groups that consolidate an entity owned by `consolidation_group` are
        that group and its ancestors. A rate into any other currency is never
        read, and the account silently translates at the closing rate at every
        level, which is the wrong number, not an error. So: blank is refused
        (the field is reqd, but a patch, an import or ignore_mandatory skips
        that), and a currency no group in the chain presents in is refused
        naming each group and its currency, so the user can pick one.
        """
        if not self.to_currency:
            frappe.throw(
                "To Currency is required: the presentation currency this rate "
                "converts into (the reporting currency of the owning group, or "
                "of a group above it that consolidates the entity).",
                frappe.MandatoryError,
            )
        if not self.consolidation_group:
            return
        chain = presentation_currencies(self.consolidation_group)
        if not chain:
            frappe.throw(
                f"Consolidation group '{self.consolidation_group}' has no root node "
                "(a Consolidation Group row with no entity), so its reporting "
                "currency is unknown. Declare the group before recording a rate "
                "under it.",
                frappe.ValidationError,
            )
        if self.to_currency not in {ccy for _g, ccy in chain}:
            presents = ", ".join(f"{g} presents in {ccy or '(no currency)'}" for g, ccy in chain)
            frappe.throw(
                f"No group that consolidates an entity of '{self.consolidation_group}' "
                f"presents in {self.to_currency}: {presents}. A rate into any other "
                "currency is never applied, and the account translates at the "
                "closing rate instead.",
                frappe.ValidationError,
            )

    def before_cancel(self):
        """Cancel only while every period the rate applies to is open (#136).
        A rate applies from the first period its date affects until the next
        rate for the same group, entity, account and To currency (one series
        per presentation currency, konsol#374), or indefinitely."""
        next_rate = frappe.db.get_value(
            "Historical Equity Rate",
            {"consolidation_group": self.consolidation_group, "data_area_id": self.data_area_id,
             "main_account": self.main_account, "to_currency": self.to_currency, "docstatus": 1,
             "rate_date": [">", self.rate_date], "name": ["!=", self.name]},
            "rate_date", order_by="rate_date asc")
        assert_open_between(self.rate_date, first_period_affected(next_rate) if next_rate else None,
                            action="cancel a historical equity rate", end_exclusive=True)

    def on_submit(self):
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_cancel(self):
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so
        the full-table re-send put it straight back (#120)."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
