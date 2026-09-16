"""Main Account: the group chart of accounts, governed in konsol (konsol#182).

Until now konsol had no chart: what an account IS was inferred in the warehouse
from whatever an ERP sent, and a site with no ERP had no chart at all, so every
trial balance was refused. Decided 13 Sep 2026: konsol defines the shape, and
the group chart is these records. Group finance declares each account here, or
uploads the whole chart as one file (konsol.chart_upload): its type, its
statement, how it is translated and whether it accumulates. The rules are in
konsol.group_chart_model, so the form and the file cannot disagree.

A tree (parent_account): headings (is_group) hold accounts and are never posted
to. Draft/Published/Inactive like every governed reference doctype: only a
Published account is in the chart (konsol.group_chart), and Published rows,
headings included, are written through to epm_staging.main_accounts for the
warehouse. Publishing needs the Close Lead (EPM Admin); a Group Accountant
drafts.
"""
import json

import frappe
from frappe.utils.nestedset import NestedSet

from konsol import group_chart_model as M
from konsol.governed_reference import GovernedReferenceDocument
from konsol.schema_lifecycle import check_epm_admin

DOCTYPE = "Main Account"
_PUBLISHED = "Published"
_PARENT_FIELDS = ["main_account", "is_group", "chart_of_accounts", "statement_section", "status"]
#: Filled from the other declarations when left blank (group_chart_model.apply_defaults).
_DEFAULTED = ("normal_balance", "time_balance", "fx_method")


class MainAccount(NestedSet, GovernedReferenceDocument):
    nsm_parent_field = "parent_account"

    CH_TABLE = "epm_staging.main_accounts"
    CH_SYNC_FILTERS = {"status": _PUBLISHED}
    # silver_main_accounts and everything downstream of it: what an account is
    # decides every statement, not only consolidation (tasks.SCOPE_SELECTOR)
    BUILD_SCOPE = "chart"
    # The DDL's columns in the DDL's order (clickhouse._REFERENCE_TABLE_DDL,
    # identical to konsolidat's clickhouse/init-db.sql).
    CH_FIELD_MAP = {
        "main_account": "main_account",
        "account_name": "account_name",
        "chart_of_accounts": "chart_of_accounts",
        "parent_account": "parent_account",
        "is_group": "is_group",
        "account_type": "account_type",
        "statement_section": "statement_section",
        "sub_section": "sub_section",
        "normal_balance": "normal_balance",
        "time_balance": "time_balance",
        "fx_method": "fx_method",
        "is_posting": "is_posting",
        "is_suspended": "is_suspended",
        "allow_ic": "allow_ic",
        "cf_category": "cf_category",
        "cf_line_item": "cf_line_item",
        "is_cash": "is_cash",
        "main_account_category": "main_account_category",
        "status": "status",
        # konsolidat#199: added after the table shipped, so LAST (_ADDED_COLUMNS)
        "is_retained_earnings": "is_retained_earnings",
    }

    # -- naming ----------------------------------------------------------------

    def before_naming(self):
        """Normalise before the name is derived: with autoname field:main_account
        the name is taken from the field before validate runs, and the field is
        then kept in step with the name, so normalising in validate alone would
        be undone (entity.py, the same reason)."""
        self._normalise_code()

    def _normalise_code(self):
        code = M.text(self.main_account)
        problem = M.code_problem(code)
        if problem:
            frappe.throw(problem[0].upper() + problem[1:])
        self.main_account = code
        if self.parent_account:
            self.parent_account = M.text(self.parent_account)

    # -- validation --------------------------------------------------------------

    def _row(self):
        return {"main_account": self.main_account, "status": self.status,
                **{f: self.get(f) for f in M.DECLARED_FIELDS}}

    def _parent_row(self):
        """The parent's declaration, or None when it does not exist."""
        if not self.parent_account:
            return None
        return frappe.db.get_value(DOCTYPE, self.parent_account, _PARENT_FIELDS, as_dict=True)

    def validate(self):
        self._normalise_code()
        filled = M.apply_defaults(self._row())
        for field in _DEFAULTED:
            if not self.get(field) and filled.get(field):
                self.set(field, filled[field])
        problems = M.declaration_problems(self._row(), self._parent_row())
        if problems:
            frappe.throw("\n".join(problems), title="Main Account")
        self._guard_publish()
        self._warn_if_reclassified()

    def _guard_publish(self):
        """A Published row is the Close Lead's (intercompany_account.py's shape).

        - Any save of a row that WAS or IS BECOMING Published needs EPM Admin:
          editing it changes what every statement shows, and moving it out of
          Published withdraws the declaration.
        - A row that is Published after the save must be publishable, whether it
          got there through publish() or a plain save.
        - A row leaving Published: a heading with Published accounts under it is
          refused; a leaf is withdrawn with a warning.
        - A Published leaf made a heading leaves the statements although the row
          stays Published: the warehouse drops headings (silver_main_accounts:
          is_group = 0). Refused while anything depends on it, as a leaf leaving
          the chart is.
        """
        before = self.get_doc_before_save()
        was = before.status if before else None
        if was == _PUBLISHED or self.status == _PUBLISHED:
            check_epm_admin()
        if was == _PUBLISHED and M.flag(self.is_group) and not M.flag(before.get("is_group")):
            self._refuse_if_in_use(as_leaf=True)
        if self.status == _PUBLISHED:
            self._before_publish()
        elif was == _PUBLISHED:
            self._before_unpublish()

    def _before_publish(self):
        """Publish readiness: a leaf declares its name, chart, type, statement,
        normal balance, time balance and translation method; a heading its name
        and chart; and a parent, if any, is already Published. A retained-earnings
        account (konsolidat#199) is the only Published one in its chart."""
        problems = M.publish_problems(M.apply_defaults(self._row()), self._parent_row())
        if M.flag(self.is_retained_earnings):
            problems += M.retained_earnings_problems(self._other_retained_earnings_rows() + [self._row()])
        if problems:
            frappe.throw("\n".join(problems), title="Not ready to publish")

    def _other_retained_earnings_rows(self):
        """The chart's Published accounts flagged is_retained_earnings, this one aside."""
        return frappe.get_all(DOCTYPE, filters={"chart_of_accounts": self.chart_of_accounts, "status": _PUBLISHED,
                                                "is_retained_earnings": 1, "main_account": ["!=", self.main_account]},
                              fields=["main_account", "chart_of_accounts", "status", "is_retained_earnings"],
                              limit_page_length=0)

    def _before_unpublish(self):
        if self.is_group:
            live = frappe.get_all(DOCTYPE, filters={"parent_account": self.name, "status": _PUBLISHED},
                                  pluck="name", limit_page_length=0)
            if live:
                frappe.throw(f"{self.name} has Published accounts under it ({', '.join(sorted(live)[:10])}"
                             f"{', …' if len(live) > 10 else ''}): unpublish them first.")
        self._refuse_if_in_use()
        if self.is_group:
            return
        frappe.msgprint(f"{self.name} is no longer in the group chart: new trial balances posting to it "
                        "are refused.", title="Withdrawn from the chart", indicator="orange")

    def _refuse_if_in_use(self, as_leaf=False):
        """An account leaving the chart (unpublished, Inactive, deleted) while
        something depends on it is refused: submitted trial balances post to it,
        an Intercompany Account names it, or a group books its differences to it.
        A heading answers for the accounts under it; ``as_leaf`` checks the
        account itself (a leaf being made a heading)."""
        heading = bool(M.flag(self.is_group)) and not as_leaf
        codes = self._codes_in_scope() if heading else [self.name]
        problems = M.in_use_problems(self.name, postings=_submitted_postings(codes),
                                     intercompany=_intercompany_rows(codes),
                                     difference_groups=_difference_groups(codes), heading=heading)
        if problems:
            frappe.throw("\n".join(problems), title="Account in use")

    def _codes_in_scope(self):
        """This account, or for a heading every account under it."""
        if not self.is_group:
            return [self.name]
        bounds = frappe.db.get_value(DOCTYPE, self.name, ["lft", "rgt"], as_dict=True)
        if not bounds:
            return []
        return frappe.get_all(DOCTYPE, filters={"lft": [">", bounds.lft], "rgt": ["<", bounds.rgt]},
                              pluck="name", limit_page_length=0)

    def _warn_if_reclassified(self):
        before = self.get_doc_before_save()
        if not before or before.status != _PUBLISHED or self.status != _PUBLISHED:
            return
        changed = M.reclassified(before, self)
        if changed:
            frappe.msgprint(
                f"{', '.join(changed)} changed on a Published account: every period, closed ones included, "
                "is re-translated at the next full rebuild.", title="Reclassified", indicator="orange")

    # -- lifecycle ---------------------------------------------------------------
    # Neither NestedSet nor GovernedReferenceDocument calls super() in these, so
    # the MRO would run only the first. Both are called, the tree first (as
    # entity.py does): if the tree refuses, nothing reaches the warehouse.

    def on_update(self):
        NestedSet.on_update(self)                   # update_nsm + validate_ledger
        GovernedReferenceDocument.on_update(self)   # after-commit resync when Published
        self._mirror_cash_flow_category()

    def _mirror_cash_flow_category(self):
        # The chart is the source of the cash-flow mapping (konsol#196): dbt reads
        # only epm_staging.cash_flow_categories, so without this a site that
        # uploaded a complete chart still had to re-key every mapping as Cash Flow
        # Category rows before the full build could run. A Published mapped leaf
        # keeps a Published CFC-<code> row in step; an account that stops being
        # mapped (unpublished, a cf field cleared, no longer Balance Sheet) makes
        # its row Inactive. A manual Cash Flow Category edit for a chart account is
        # overwritten on the next chart save.
        if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
            return
        mapping = M.cash_flow_mapping(self.as_dict())
        if self.status != _PUBLISHED or not mapping:
            _inactivate_cash_flow_row(self.main_account)
            return
        name = _live_cash_flow_row(self.main_account)
        row = frappe.get_doc("Cash Flow Category", name) if name else frappe.new_doc("Cash Flow Category")
        for field in ("main_account", "cf_category", "cf_line_item", "is_cash"):
            row.set(field, mapping[field])
        row.set("status", _PUBLISHED)
        if name:
            row.save(ignore_permissions=True)
        else:
            row.insert(ignore_permissions=True)   # autoname: CFC-<code>

    def on_trash(self):
        NestedSet.on_trash(self)   # refuses a heading that still has accounts under it
        if self.status == _PUBLISHED:
            self._refuse_if_in_use()   # what is posted to it would drop out of the statements

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so the
        full-table re-send would put it straight back (#120)."""
        GovernedReferenceDocument.after_delete(self)
        if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
            return
        _inactivate_cash_flow_row(self.main_account)   # a deleted account leaves no live mapping behind

    def after_rename(self, olddn, newdn, merge=False):
        """rename_doc never calls on_update, and it rewrites the code and every
        child's parent_account by raw SQL. allow_rename is off, but
        rename_doc(force=True) still arrives here (entity.py).

        The old code's Cash Flow Category row is withdrawn and the new code
        mirrored: rename_doc has already rewritten main_account to newdn
        (update_autoname_field), so the mirror sees the new code."""
        super().after_rename(olddn, newdn, merge)
        self._resync()
        if not (frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch):
            _inactivate_cash_flow_row(olddn)
        self._mirror_cash_flow_category()


def _live_cash_flow_row(code):
    """The name of the account's live Cash Flow Category row, whatever it is
    called; else CFC-<code> when that (Inactive) row exists; else None.

    Looked up by main_account, not by name: a manually keyed row for the
    account may have another name and must be updated, not collided with, as
    CashFlowCategory._validate_unique_account refuses a second live row per
    account and would otherwise fail a plain chart save or a whole chart upload.
    """
    name = frappe.db.get_value("Cash Flow Category", {"main_account": code, "status": ["!=", "Inactive"]}, "name")
    if name:
        return name
    fallback = f"CFC-{code}"
    return fallback if frappe.db.exists("Cash Flow Category", fallback) else None


def _inactivate_cash_flow_row(code):
    """Withdraw the account's cash-flow mapping: its row, if any, is made
    Inactive (the mirror's else-branch, delete and rename share this)."""
    name = _live_cash_flow_row(code)
    if not name:
        return
    row = frappe.get_doc("Cash Flow Category", name)
    if row.status != "Inactive":
        row.set("status", "Inactive")
        row.save(ignore_permissions=True)


def _submitted_postings(codes):
    """(account, entity, fiscal_year, fiscal_period) of the submitted trial
    balances posting to ``codes``: the claimed rows in the warehouse (raw INNER
    JOIN control, what bronze and the statements read). A warehouse with no
    such table has nothing in any statement to unbalance. Any other failure to
    read it refuses: an account in use must not leave the chart unverified."""
    if not codes:
        return []
    from konsol.clickhouse import _sql_value, execute
    from konsol.group_rates import _not_built

    try:
        raw = execute(
            "SELECT DISTINCT main_account, data_area_id, fiscal_year, fiscal_period "
            "FROM epm_raw.trial_balance_submissions "
            f"WHERE main_account IN ({', '.join(_sql_value(c) for c in codes)}) "
            "AND batch_id IN (SELECT batch_id FROM epm_raw.trial_balance_submission_control) "
            "FORMAT JSONEachRow")
    except Exception as e:  # noqa: BLE001
        if _not_built(e):
            return []
        frappe.throw(f"Cannot check whether submitted trial balances post to {', '.join(codes[:5])}: the "
                     f"warehouse could not be read ({type(e).__name__}). Try again once it is up.")
    rows = [json.loads(line) for line in (raw or "").splitlines() if line.strip()]
    return [(r["main_account"], r["data_area_id"], int(r["fiscal_year"]), int(r["fiscal_period"])) for r in rows]


def _intercompany_rows(codes):
    """Published Intercompany Accounts naming any of ``codes``, on either side."""
    if not codes or not frappe.db.table_exists("Intercompany Account"):
        return []
    names = set()
    for column in ("main_account", "counterpart_account"):
        names.update(frappe.get_all("Intercompany Account", filters={"status": _PUBLISHED, column: ["in", codes]},
                                    pluck="name", limit_page_length=0))
    return sorted(names)


def _difference_groups(codes):
    """Consolidation Groups that book intercompany differences to any of ``codes``."""
    if not codes:
        return []
    return sorted(frappe.get_all("Consolidation Group", filters={"ic_difference_account": ["in", codes]},
                                 pluck="name", limit_page_length=0))
