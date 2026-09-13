"""Intercompany Account: the intercompany flag on the group chart (konsol#159).

Decided 13 Sep 2026 (decision 3): an account is intercompany because it is
flagged in the group chart, and that one flag drives upload validation, reports
and elimination. konsol has no chart-of-accounts master (the chart comes from
the ERP, through epm_silver.silver_main_accounts), so the flag is its own
governed list: one row per intercompany account, Published rows written through
to epm_staging.intercompany_accounts like every governed reference doctype.

``counterpart_account`` is the account the PARTNER books the other side on:
an intercompany receivable's counterpart is the intercompany payable, revenue's
is cost. The elimination pairs (entity, partner, account) with (partner, entity,
counterpart). Blank means both sides use this same account. Each account
belongs to exactly one pair; otherwise one side could be eliminated twice.

IC Elimination Rule stays only for the special cases (``unrealized_profit``).
"""
import frappe

from konsol.governed_reference import GovernedReferenceDocument
from konsol.schema_lifecycle import check_epm_admin

DOCTYPE = "Intercompany Account"
_PUBLISHED = "Published"


def pair_of(main_account, counterpart_account):
    """The unordered account pair a row declares: (a,) when both sides use
    one account, else (a, b) sorted. Pure."""
    a = (main_account or "").strip()
    b = (counterpart_account or "").strip() or a
    return tuple(sorted({a, b}))


def pair_conflicts(main_account, counterpart_account, others):
    """Names of other rows that put one of this row's accounts in a DIFFERENT
    pair. ``others`` is [(name, main_account, counterpart_account)]. Pure.

    X paired with Y on one row and Y with X on another is the same pair, not a
    conflict. X with Y and Y on its own (or with Z) is: the engine would match
    Y against both.
    """
    mine = pair_of(main_account, counterpart_account)
    return [name for name, m, c in others
            if pair_of(m, c) != mine and set(pair_of(m, c)) & set(mine)]


def intercompany_accounts():
    """Every account flagged intercompany: each Published row's account and
    its counterpart. An empty set on a site the doctype has not reached yet
    (a hot-copied release before migrate), so a trial balance still loads."""
    if not frappe.db.table_exists(DOCTYPE):
        return set()
    rows = frappe.get_all(DOCTYPE, filters={"status": _PUBLISHED},
                          fields=["main_account", "counterpart_account"], limit_page_length=0)
    return {a for r in rows for a in pair_of(r.main_account, r.counterpart_account)}


class IntercompanyAccount(GovernedReferenceDocument):
    CH_TABLE = "epm_staging.intercompany_accounts"
    CH_SYNC_FILTERS = {"status": _PUBLISHED}
    CH_FIELD_MAP = {
        "main_account": "main_account",
        "counterpart_account": "counterpart_account",
        "description": "description",
        "status": "status",
    }
    # the flag changes what consolidation eliminates
    BUILD_SCOPE = "consolidation"

    def validate(self):
        self.main_account = (self.main_account or "").strip()
        self.counterpart_account = (self.counterpart_account or "").strip()
        if self.counterpart_account == self.main_account:
            self.counterpart_account = ""   # the same account: blank says so
        self._guard_publish()
        if self.status != "Inactive":
            self._validate_one_pair()

    def _guard_publish(self):
        """A Published row is the Close Lead's (#173 review, A1 and A2).

        - Any save of a row that WAS Published needs EPM Admin: editing its
          accounts changes what consolidation eliminates, and moving it out of
          Published (to Draft or Inactive) stops eliminating it. Checking only
          the move into Published let an EPM Analyst do both.
        - A move INTO Published needs EPM Admin and passes the publish checks,
          through publish() or a plain save alike: on_update writes a
          Published row through either way.
        """
        before = self.get_doc_before_save()
        was = before.status if before else None
        if was == _PUBLISHED or self.status == _PUBLISHED:
            check_epm_admin()
        if self.status == _PUBLISHED and was != _PUBLISHED:
            self._before_publish()

    def _validate_one_pair(self):
        # A locking read (CLAUDE.md, MariaDB REPEATABLE READ). A plain read can
        # miss a row another transaction committed after this one's first
        # read, so two saves at once could each pass and pair one account
        # twice. FOR UPDATE reads the latest committed rows and holds what it
        # scanned until the commit.
        others = frappe.db.sql(
            "SELECT `name`, `main_account`, `counterpart_account` FROM `tabIntercompany Account` "
            "WHERE `name` != %s AND `status` != 'Inactive' FOR UPDATE",
            (self.name or "",), as_dict=True)
        clash = pair_conflicts(self.main_account, self.counterpart_account,
                               [(o.name, o.main_account, o.counterpart_account) for o in others])
        if clash:
            frappe.throw(
                f"{', '.join(clash)} already pairs one of these accounts differently. "
                "Each account belongs to one intercompany pair, or one side would be "
                "eliminated twice. Make the other row Inactive first.")

    def _before_publish(self):
        """Both accounts must be in the group chart, and neither may be a
        group's intercompany-difference account (its postings would be
        eliminated in turn). Once per save: publish() runs it, and the save
        it makes would run it again from _guard_publish."""
        if self.flags.ica_publish_checked:
            return
        from konsol.tb_bulk import _chart_accounts

        accounts = [a for a in ((self.main_account or "").strip(), (self.counterpart_account or "").strip()) if a]
        chart = _chart_accounts()
        missing = [a for a in accounts if a not in chart]
        if missing:
            frappe.throw(f"Not in the group chart: {', '.join(missing)}")
        diff = frappe.get_all("Consolidation Group",
                              filters={"ic_difference_account": ["in", accounts]}, pluck="name")
        if diff:
            frappe.throw(
                f"{', '.join(accounts)}: {', '.join(diff)} books intercompany differences "
                "to this account, so it cannot also be an intercompany account.")
        self.flags.ica_publish_checked = True
