"""Intercompany Account: the intercompany flag on the group chart (konsol#159).

Decided 13 Sep 2026 (decision 3): an account is intercompany because it is
flagged in the group chart, and that one flag drives upload validation, reports
and elimination. The flag is its own governed list, which predates the group
chart master (Main Account, konsol#182): one row per intercompany account,
Published rows written through to epm_staging.intercompany_accounts like every
governed reference doctype. It stays the pairing table; Main Account.allow_ic
says an account may carry partner rows at all (tied together in #182 PR5).
Chart membership is konsol.group_chart.chart_codes.

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


def _accounts(doc):
    """The row's (main, counterpart) as they are stored: stripped, and a
    counterpart equal to the account blank."""
    main = (doc.main_account or "").strip()
    counterpart = (doc.counterpart_account or "").strip()
    return main, "" if counterpart == main else counterpart


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
        self.main_account, self.counterpart_account = _accounts(self)
        self._guard_publish()
        if self.status != "Inactive":
            self._validate_one_pair()

    def _guard_publish(self):
        """A Published row is the Close Lead's (#173 review A1, A2; re-review K1).

        - Any save of a row that WAS Published needs EPM Admin: editing its
          accounts changes what consolidation eliminates, and moving it out of
          Published (to Draft or Inactive) stops eliminating it.
        - A row that is Published after the save passes the publish checks
          whenever it has just become Published (through publish() or a plain
          save alike) or either account changed. An edit of a live row's
          accounts is a publish of new accounts: without the checks an admin
          could point a live flag at an account outside the chart, or at a
          group's difference account, and it would be written through.
        """
        before = self.get_doc_before_save()
        was = before.status if before else None
        if was == _PUBLISHED or self.status == _PUBLISHED:
            check_epm_admin()
        if self.status != _PUBLISHED:
            return
        if was != _PUBLISHED or _accounts(before) != (self.main_account, self.counterpart_account):
            self._before_publish()

    def _validate_one_pair(self):
        """Refuse an account that another live row pairs differently.

        Two saves at once must not both pass (#173 review A4, re-review K2):
        - First one lock, on a row that always exists: this doctype's own
          tabDocType row (the konsol.build_lock pattern). It serialises every
          Intercompany Account validation, so two new rows naming the same
          account are refused cleanly rather than both inserted.
        - Then a locking read (it reads the latest committed rows under
          REPEATABLE READ) of only the rows that share one of this row's
          accounts, by equality on indexed columns (main_account is unique,
          counterpart_account has search_index). An unindexed scan locked
          every row it read, and Frappe has already locked this document's
          own row by now, so two unrelated saves could deadlock.
        Two concurrent edits of rows that share an account can still meet on
        each other's rows. InnoDB then aborts one (a retryable deadlock),
        which never pairs an account twice.
        """
        frappe.db.sql("SELECT `name` FROM `tabDocType` WHERE `name` = %s FOR UPDATE", (DOCTYPE,))
        others = {}
        for account in sorted({a for a in (self.main_account, self.counterpart_account) if a}):
            for column in ("main_account", "counterpart_account"):
                for o in frappe.db.sql(
                        f"SELECT `name`, `main_account`, `counterpart_account` FROM `tabIntercompany Account` "
                        f"WHERE `{column}` = %s AND `name` != %s AND `status` != 'Inactive' FOR UPDATE",
                        (account, self.name or ""), as_dict=True):
                    others[o.name] = o
        clash = pair_conflicts(self.main_account, self.counterpart_account,
                               [(o.name, o.main_account, o.counterpart_account) for o in others.values()])
        if clash:
            frappe.throw(
                f"{', '.join(sorted(clash))} already pairs one of these accounts differently. "
                "Each account belongs to one intercompany pair, or one side would be "
                "eliminated twice. Make the other row Inactive first.")

    def _before_publish(self):
        """Both accounts must be in the group chart, and neither may be a
        group's intercompany-difference account (its postings would be
        eliminated in turn).

        publish() runs this, then saves, and the save would run it again from
        _guard_publish. The flag records WHICH accounts were checked (re-review
        K3), so the second call is skipped only for the same accounts; a
        document reused for another save with other accounts is checked anew.
        """
        checked = _accounts(self)
        if self.flags.ica_publish_checked == checked:
            return
        # #173 re-review L5: Consolidation Group's difference-account check
        # reads these rows and this reads its rows, so both take the same lock
        # first (this doctype's tabDocType row, as _validate_one_pair does),
        # then read by equality on an indexed column with FOR UPDATE.
        frappe.db.sql("SELECT `name` FROM `tabDocType` WHERE `name` = %s FOR UPDATE", (DOCTYPE,))
        from konsol.group_chart import chart_codes

        accounts = [a for a in checked if a]
        chart = chart_codes()
        missing = [a for a in accounts if a not in chart]
        if missing:
            frappe.throw(f"Not in the group chart: {', '.join(missing)}")
        diff = sorted({r.name for a in accounts for r in frappe.db.sql(
            "SELECT `name` FROM `tabConsolidation Group` WHERE `ic_difference_account` = %s FOR UPDATE",
            (a,), as_dict=True)})
        if diff:
            frappe.throw(
                f"{', '.join(accounts)}: {', '.join(diff)} books intercompany differences "
                "to this account, so it cannot also be an intercompany account.")
        self.flags.ica_publish_checked = checked
