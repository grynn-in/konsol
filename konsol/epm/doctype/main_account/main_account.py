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
        """
        before = self.get_doc_before_save()
        was = before.status if before else None
        if was == _PUBLISHED or self.status == _PUBLISHED:
            check_epm_admin()
        if self.status == _PUBLISHED:
            self._before_publish()
        elif was == _PUBLISHED:
            self._before_unpublish()

    def _before_publish(self):
        """Publish readiness: a leaf declares its name, chart, type, statement,
        normal balance, time balance and translation method; a heading its name
        and chart; and a parent, if any, is already Published."""
        problems = M.publish_problems(M.apply_defaults(self._row()), self._parent_row())
        if problems:
            frappe.throw("\n".join(problems), title="Not ready to publish")

    def _before_unpublish(self):
        if self.is_group:
            live = frappe.get_all(DOCTYPE, filters={"parent_account": self.name, "status": _PUBLISHED},
                                  pluck="name", limit_page_length=0)
            if live:
                frappe.throw(f"{self.name} has Published accounts under it ({', '.join(sorted(live)[:10])}"
                             f"{', …' if len(live) > 10 else ''}): unpublish them first.")
            return
        frappe.msgprint(f"{self.name} is no longer in the group chart: new trial balances posting to it "
                        "are refused.", title="Withdrawn from the chart", indicator="orange")

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

    def on_trash(self):
        NestedSet.on_trash(self)   # refuses a heading that still has accounts under it

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so the
        full-table re-send would put it straight back (#120)."""
        GovernedReferenceDocument.after_delete(self)

    def after_rename(self, olddn, newdn, merge=False):
        """rename_doc never calls on_update, and it rewrites the code and every
        child's parent_account by raw SQL. allow_rename is off, but
        rename_doc(force=True) still arrives here (entity.py)."""
        super().after_rename(olddn, newdn, merge)
        self._resync()
