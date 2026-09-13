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
        """Publishing is the Close Lead's. publish() checks it, but a plain
        save with status=Published would write through too (on_update syncs
        Published rows), so the save checks it as well."""
        before = self.get_doc_before_save()
        if self.status == _PUBLISHED and (not before or before.status != _PUBLISHED):
            check_epm_admin()

    def _validate_one_pair(self):
        others = frappe.get_all(
            DOCTYPE,
            filters={"name": ["!=", self.name or ""], "status": ["!=", "Inactive"]},
            fields=["name", "main_account", "counterpart_account"], limit_page_length=0)
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
        eliminated in turn)."""
        from konsol.tb_bulk import _chart_accounts

        accounts = [a for a in (self.main_account, self.counterpart_account) if a]
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
