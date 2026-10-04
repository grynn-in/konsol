import frappe
from frappe.model.document import Document

from konsol.close import close_policy_model

REGULAR = "Regular"
# Effective period statuses that fix the first close (fiscal_status_model).
_CLOSED_STATUSES = ("Closed", "Locked")


def _first_close_key(year, period):
    """``(year, period)``, or None when undeclared (blank or 0 in either part)."""
    year, period = int(year or 0), int(period or 0)
    if not year or not period:
        return None
    return (year, period)


def _label(key):
    return "FY%d P%02d" % key


class CloseSettings(Document):
    # konsol#305 A36a (decision P13): close policy the Close Lead (EPM Admin)
    # owns. It is its own single doctype because EPM Settings is System
    # Manager only, and Frappe's base write check reads permlevel-0 rows only,
    # so field-level write could not open just these fields (A36, proved live).

    def validate(self):
        self.validate_first_close_period()
        self.validate_first_close_locked()
        self.validate_policies()
        self.validate_intercompany_declaration()
        self.validate_statement_accounts()

    def validate_first_close_period(self):
        """konsol#303: the first period konsol closes. No default — a blank
        pair is fine (nothing declared yet), but a half-set pair or a
        non-Regular / undeclared period is refused outright."""
        year = self.first_close_fiscal_year
        period = self.first_close_fiscal_period
        if not year and not period:
            return
        if not (year and period):
            frappe.throw(frappe._("Give both the first close year and period, or neither."))
        # Imported lazily: konsol.period_status needs a live site, and this
        # keeps the pure-ish controller test free to stub it.
        import konsol.period_status as period_status

        row = period_status.period_row(year, period)
        if row["type"] != "Regular":
            frappe.throw(frappe._(
                "The first close period must be a Regular period; {0} is {1}."
            ).format(row["code"], row["type"]))

    def validate_first_close_locked(self):
        """konsol#305 A64 (#305-R5a, Deepak 26 Sep): once the first close
        period has been used, it cannot move. A change of year or period (or
        clearing it) is refused when any Regular period from min(old, new)
        onward is Closed or Locked, or has a signed latest close run. A first
        declaration (nothing stored) and a save with no change are allowed."""
        old = _first_close_key(
            frappe.db.get_single_value("Close Settings", "first_close_fiscal_year"),
            frappe.db.get_single_value("Close Settings", "first_close_fiscal_period"),
        )
        new = _first_close_key(self.first_close_fiscal_year, self.first_close_fiscal_period)
        if old is None or old == new:
            return
        start = old if new is None else min(old, new)

        # Imported lazily: both need a live site; the controller test stubs them.
        from konsol import fiscal_calendar
        from konsol.close import signoff_gate
        from konsol.consolidation.doctype.assertion_run.assertion_run import SIGNED_STATES

        runs = signoff_gate._latest_runs()
        rows = sorted(
            (r for r in fiscal_calendar.fiscal_period_rows()
             if r.get("period_type") == REGULAR),
            key=lambda r: (int(r["fiscal_year"]), int(r["fiscal_period"])),
        )
        for row in rows:
            key = (int(row["fiscal_year"]), int(row["fiscal_period"]))
            if key < start:
                continue
            if row["status"] in _CLOSED_STATUSES:
                used = row["status"].lower()
            elif (runs.get(key) or {}).get("signoff_status") in SIGNED_STATES:
                used = "signed"
            else:
                continue
            frappe.throw(frappe._(
                "{0} is already {1} under the current first close ({2}); "
                "the first close period can no longer move."
            ).format(_label(key), used, _label(old)))

    def validate_policies(self):
        """konsol#305-D2-3, D2-9: self_approval and rate_move_threshold have
        no default. A save is refused only for an unknown self-approval
        value or a negative threshold; blank / 0 is undeclared and saves
        fine — it is reported elsewhere as a setup gap
        (close_policy_model.policy_gaps), never guessed."""
        problems = close_policy_model.settings_problems(
            self.self_approval, self.rate_move_threshold)
        if problems:
            frappe.throw("<br>".join(problems))

    def validate_intercompany_declaration(self):
        """konsol#305-W3-7 (C16): "None in this group" is refused while any
        Intercompany Account is Published. Blank (undeclared) reads nothing.
        An unknown value is refused before any read.

        The count runs under the lock an Intercompany Account publish takes
        first (its tabDocType row, intercompany_account.py), then as a locking
        read: MariaDB is REPEATABLE READ, so without both a publish and this
        save in two requests could each pass (the #293 review finding 1
        pattern)."""
        declaration = self.intercompany_declaration
        if not declaration:
            return
        if declaration not in close_policy_model.INTERCOMPANY_DECLARATIONS:
            frappe.throw("<br>".join(
                close_policy_model.intercompany_declaration_problems(declaration, 0)))
        frappe.db.sql(
            "SELECT `name` FROM `tabDocType` WHERE `name` = %s FOR UPDATE",
            ("Intercompany Account",))
        published = frappe.db.sql(
            "SELECT COUNT(*) FROM `tabIntercompany Account` "
            "WHERE `status` = 'Published' FOR UPDATE")[0][0]
        problems = close_policy_model.intercompany_declaration_problems(
            declaration, int(published))
        if problems:
            frappe.throw("<br>".join(problems))

    def validate_statement_accounts(self):
        """konsol#305-W4-1 option 1c: no default. Blank stays undeclared
        (reported elsewhere as close_policy_model.statement_accounts' one
        setup gap, never defaulted). A save naming an account that cannot
        hold the role, or the same account for both roles, is refused with
        close_policy_model's sentence. Not locked after a signed period
        (unlike validate_first_close_locked): these fields change
        presentation, not numbers, and the Single's track_changes is the
        record."""
        cta = self.statement_cta_account
        result = self.statement_result_account
        if not cta and not result:
            return
        codes = [c for c in (cta, result) if c]
        rows = {
            r["name"]: r
            for r in frappe.get_all(
                "Main Account",
                filters={"name": ["in", codes]},
                fields=["name", "is_group", "status", "statement_section", "account_name"],
            )
        }
        problems = close_policy_model.statement_account_problems(cta, result, rows)
        if problems:
            frappe.throw("<br>".join(problems))
