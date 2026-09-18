"""Real-DB bench test for EPM Fiscal Year and period close state (konsol#189).

Replaces test_period_status_bench.py, which drove the retired Period Status
doctype. The host tests check the fiscal rules as pure functions
(fiscal_structure_model, fiscal_status_model, fiscal_patterns_model); this
runs them through the EPM Fiscal Year doctype and period_status against a real
MariaDB: generating periods, the declared-never-assumed gate, close / lock /
reopen of a period and of a year, year-over-row precedence, and the freeze on
periods documents use.

Run on a live bench:

    bench --site <site> run-tests --module konsol.tests.test_fiscal_year_bench

or, where tests are disabled for the site, from the bench's sites directory:

    FRAPPE_SITE=<site> ../env/bin/python -m konsol.tests.test_fiscal_year_bench

Uses FY2099 and ZZ codes only, and skips if FY2099 already exists or is used
on the site. Every test rolls back what it made (addCleanup, so it runs even
when setUp or the test fails); nothing is committed.
"""
import os
import sys
import unittest
from contextlib import contextmanager

import frappe
from frappe.utils import getdate

from konsol import fiscal_calendar
from konsol import group_rates
from konsol import period_status as ps

FY = 2099
NAME = str(FY)  # EPM Fiscal Year autoname is format:{fiscal_year}
ENTITY = "ZZ-FY2099-BENCH"
UNDECLARED = 14  # Monthly (12) + Opening (0) + Closing (13) declares 0..13
REASON = "ZZ bench: reopen for test"


# ---- helpers ------------------------------------------------------------------


@contextmanager
def _rate_gate(failing=()):
    """Stand in for group_rates.assert_rates_complete: passes, except that it
    refuses (as a ValidationError, like the real gate) each period in
    `failing`. The real gate asks the warehouse, which a bench test can't
    arrange for FY2099."""
    original = group_rates.assert_rates_complete

    def fake(fiscal_year, fiscal_period):
        if int(fiscal_period) in failing:
            frappe.throw(f"ZZ bench: no approved group rate for period {fiscal_period}",
                         frappe.ValidationError)

    group_rates.assert_rates_complete = fake
    try:
        yield
    finally:
        group_rates.assert_rates_complete = original


@contextmanager
def _roles(roles):
    """frappe.get_roles answers `roles` for whoever asks."""
    original = frappe.get_roles
    frappe.get_roles = lambda *a, **k: list(roles)
    try:
        yield
    finally:
        frappe.get_roles = original


def _year():
    """FY2099 as saved."""
    return frappe.get_doc("EPM Fiscal Year", NAME)


def _row(year, period):
    return next(r for r in year.periods if int(r.fiscal_period) == period)


def _new_year():
    """Create FY2099 (calendar year, Monthly (12), Opening and Closing) by
    running Generate Periods on the unsaved year, which inserts it."""
    doc = frappe.get_doc({
        "doctype": "EPM Fiscal Year",
        "fiscal_year": FY,
        "start_date": "2099-01-01",
        "end_date": "2099-12-31",
        "period_pattern": "Monthly (12)",
        "include_opening_period": 1,
        "include_closing_period": 1,
    })
    doc.generate_periods()
    return _year()


def _use_period(period):
    """Mark a period of FY2099 as used by a document: an Assertion Run row
    (period data, not submittable, so any row counts), written straight to
    the table so no other rule gets in the way."""
    doc = frappe.get_doc({
        "doctype": "Assertion Run",
        "status": "Queued",
        "fiscal_year": FY,
        "fiscal_period": period,
    })
    doc.name = f"ASRT-{FY}-P{period}-ZZ"
    doc.db_insert()


def _epm_admin_user():
    """An enabled user holding EPM Admin but not System Manager, or None."""
    def holders(role):
        return set(frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"},
                                  pluck="parent"))

    for user in sorted(holders("EPM Admin") - holders("System Manager") - {"Administrator", "Guest"}):
        if frappe.db.get_value("User", user, "enabled"):
            return user
    return None


# ---- tests --------------------------------------------------------------------


class FiscalYearBenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        if frappe.db.exists("EPM Fiscal Year", NAME):
            raise unittest.SkipTest(f"FY{FY} already exists on this site; this test needs it free")
        if fiscal_calendar.periods_in_use(FY):
            raise unittest.SkipTest(f"documents on this site use FY{FY}; this test needs it free")

    def setUp(self):
        self.addCleanup(self._cleanup)
        frappe.set_user("Administrator")

    @staticmethod
    def _cleanup():
        try:
            frappe.db.rollback()
        finally:
            frappe.set_user("Administrator")

    # ---- generating the declared periods --------------------------------------

    def test_generate_periods_declares_opening_twelve_months_and_closing(self):
        year = _new_year()
        shape = [(int(r.fiscal_period), r.period_code, r.period_type) for r in year.periods]
        expected = ([(0, "OPN", "Opening")]
                    + [(p, "P%02d" % p, "Regular") for p in range(1, 13)]
                    + [(13, "CLS", "Closing")])
        self.assertEqual(shape, expected)
        self.assertEqual(frappe.db.count("EPM Fiscal Year Period",
                                         {"parent": NAME, "parentfield": "periods"}), 14)
        p01, p12 = _row(year, 1), _row(year, 12)
        self.assertEqual((getdate(p01.start_date), getdate(p01.end_date)),
                         (getdate("2099-01-01"), getdate("2099-01-31")))
        self.assertEqual(getdate(p12.end_date), getdate("2099-12-31"))
        self.assertEqual(year.status, ps.OPEN)
        self.assertEqual({r.status for r in year.periods}, {ps.OPEN})

    def test_overlapping_periods_are_refused(self):
        year = _new_year()
        _row(year, 2).start_date = "2099-01-20"  # P01 runs to 31 Jan
        with self.assertRaisesRegex(frappe.ValidationError, "P02 overlaps P01"):
            year.save()

    def test_gapped_periods_are_refused(self):
        year = _new_year()
        _row(year, 2).start_date = "2099-02-05"  # P01 ends 31 Jan
        with self.assertRaisesRegex(frappe.ValidationError, "a gap of 4 days"):
            year.save()

    # ---- declared, never assumed ----------------------------------------------

    def test_declared_open_period_is_open(self):
        _new_year()
        for period in (0, 3, 13):
            self.assertEqual(ps.get_status(FY, period), ps.OPEN)
            self.assertTrue(ps.is_open(FY, period))
            ps.assert_declared(FY, period)  # must not raise
            ps.assert_open(FY, period)  # must not raise

    def test_undeclared_period_is_refused_never_assumed_open(self):
        _new_year()
        for check in (ps.get_status, ps.is_open, ps.assert_declared, ps.assert_open):
            with self.assertRaisesRegex(ps.PeriodNotDeclared, f"FY{FY} has no period {UNDECLARED}"):
                check(FY, UNDECLARED)

    # ---- one period: close, reopen, lock ---------------------------------------

    def test_close_period_stamps_who_and_when(self):
        year = _new_year()
        with _rate_gate():
            year.close_period(3)
        year = _year()
        p03 = _row(year, 3)
        self.assertEqual(p03.status, ps.CLOSED)
        self.assertEqual(p03.closed_by, "Administrator")
        self.assertIsNotNone(p03.closed_on)
        self.assertEqual(ps.get_status(FY, 3), ps.CLOSED)
        self.assertEqual(ps.get_status(FY, 4), ps.OPEN)  # its neighbour is untouched
        self.assertEqual(year.status, ps.OPEN)

    def test_closed_period_refuses_period_document_writes(self):
        year = _new_year()
        with _rate_gate():
            year.close_period(3)
        with self.assertRaisesRegex(frappe.ValidationError,
                                    f"Cannot start this run: fiscal period 3 of FY{FY} is closed"):
            ps.assert_open(FY, 3, action="start this run")

        # A Trial Balance Submission draft for a ZZ entity in that period: its
        # validate refuses it. Entity access is not what is under test here.
        tbs = frappe.get_doc({"doctype": "Trial Balance Submission", "data_area_id": ENTITY,
                              "fiscal_year": FY, "fiscal_period": 3})
        tbs._check_entity_access = lambda: None
        with self.assertRaisesRegex(frappe.ValidationError,
                                    f"Cannot submit a trial balance: fiscal period 3 of FY{FY} is closed"):
            tbs.validate()

    def test_reopen_period_needs_a_reason(self):
        year = _new_year()
        with _rate_gate():
            year.close_period(3)
        with self.assertRaisesRegex(frappe.ValidationError, "Give a reason for reopening period P03"):
            _year().reopen_period(3, "")
        self.assertEqual(ps.get_status(FY, 3), ps.CLOSED)

        _year().reopen_period(3, REASON)
        year = _year()
        p03 = _row(year, 3)
        self.assertEqual(p03.status, ps.OPEN)
        self.assertIsNone(p03.closed_by)
        self.assertIsNone(p03.closed_on)
        self.assertIn("P03 reopened", year.closing_note or "")
        self.assertIn(REASON, year.closing_note or "")

    def test_locked_period_refuses_reopen_below_system_manager(self):
        year = _new_year()
        with _rate_gate():
            year.lock_period(3)
        self.assertEqual(ps.get_status(FY, 3), ps.LOCKED)

        message = "Reopening a Locked period needs the System Manager role"
        user = _epm_admin_user()
        if user:
            frappe.set_user(user)  # restored by _cleanup
            with self.assertRaisesRegex(frappe.PermissionError, message):
                _year().reopen_period(3, REASON)
        else:  # no such user on this site: an EPM Admin's roles, as the model sees them
            with _roles(["EPM Admin"]), self.assertRaisesRegex(frappe.PermissionError, message):
                _year().reopen_period(3, REASON)
        frappe.set_user("Administrator")
        self.assertEqual(ps.get_status(FY, 3), ps.LOCKED)

    def test_administrator_can_reopen_a_locked_period(self):
        year = _new_year()
        with _rate_gate():
            year.lock_period(3)
        _year().reopen_period(3, REASON)
        self.assertEqual(ps.get_status(FY, 3), ps.OPEN)

    # ---- the whole year -------------------------------------------------------

    def test_close_year_is_all_or_nothing_on_the_rate_gate(self):
        year = _new_year()
        with _rate_gate(failing={5}):
            with self.assertRaisesRegex(frappe.ValidationError, "nothing was changed") as caught:
                year.close_year()
        self.assertIn("P05", str(caught.exception))

        year = _year()
        self.assertEqual(year.status, ps.OPEN)
        self.assertIsNone(year.closed_by)
        for r in year.periods:
            self.assertEqual((r.status, r.closed_by), (ps.OPEN, None), r.period_code)
            self.assertEqual(ps.get_status(FY, int(r.fiscal_period)), ps.OPEN)

    def test_close_year_closes_every_row_and_lock_year_locks_them(self):
        year = _new_year()
        with _rate_gate():
            year.close_year()
            year = _year()
            self.assertEqual(year.status, ps.CLOSED)
            self.assertEqual(year.closed_by, "Administrator")
            for r in year.periods:
                self.assertEqual(r.status, ps.CLOSED, r.period_code)
                self.assertEqual(ps.get_status(FY, int(r.fiscal_period)), ps.CLOSED)

            year.lock_year()
        year = _year()
        self.assertEqual(year.status, ps.LOCKED)
        for r in year.periods:
            self.assertEqual(r.status, ps.LOCKED, r.period_code)
            self.assertEqual(ps.get_status(FY, int(r.fiscal_period)), ps.LOCKED)

    def test_reopen_year_needs_a_reason(self):
        year = _new_year()
        with _rate_gate():
            year.close_year()
        with self.assertRaisesRegex(frappe.ValidationError, f"Give a reason for reopening FY{FY}"):
            _year().reopen_year("")
        self.assertEqual(_year().status, ps.CLOSED)

        _year().reopen_year(REASON)
        year = _year()
        self.assertEqual(year.status, ps.OPEN)
        self.assertIn(f"FY{FY} reopened", year.closing_note or "")
        # The rows stay as they are: each period is reopened on its own.
        self.assertEqual({r.status for r in year.periods}, {ps.CLOSED})
        self.assertEqual(ps.get_status(FY, 3), ps.CLOSED)

    def test_year_status_dominates_its_rows(self):
        _new_year()
        # The year alone, straight to the table: every row stays Open.
        for year_status in (ps.CLOSED, ps.LOCKED):
            frappe.db.set_value("EPM Fiscal Year", NAME, "status", year_status, update_modified=False)
            for period in range(0, 14):
                row = ps.period_row(FY, period)
                self.assertEqual(row["row_status"], ps.OPEN)
                self.assertEqual(row["status"], year_status)
                self.assertFalse(ps.is_open(FY, period))

    # ---- periods documents use are frozen -------------------------------------

    def test_used_period_cannot_be_renumbered(self):
        year = _new_year()
        _use_period(3)
        _row(year, 3).fiscal_period = 20
        with self.assertRaisesRegex(frappe.ValidationError, "P03 can't be renumbered: documents use it"):
            year.save()

    def test_used_period_row_cannot_be_deleted(self):
        year = _new_year()
        _use_period(3)
        year.set("periods", [r for r in year.periods if int(r.fiscal_period) != 3])
        with self.assertRaisesRegex(frappe.ValidationError, "P03 can't be removed: documents use it"):
            year.save()

    def test_year_with_used_periods_cannot_be_deleted(self):
        _new_year()
        _use_period(3)
        with self.assertRaisesRegex(frappe.ValidationError,
                                    f"FY{FY} can't be deleted: documents use 1 of its periods"):
            frappe.delete_doc("EPM Fiscal Year", NAME)
        self.assertTrue(frappe.db.exists("EPM Fiscal Year", NAME))

    def test_generate_periods_is_refused_once_periods_are_used(self):
        year = _new_year()
        _use_period(3)
        with self.assertRaisesRegex(frappe.ValidationError, "generate only on an unused year"):
            year.generate_periods()

    # ---- period_status.set_status goes through the year's actions --------------

    def test_set_status_delegates_to_the_year_actions(self):
        _new_year()
        with _rate_gate():
            doc = ps.set_status(FY, 3, ps.CLOSED)
        self.assertEqual((doc.period_code, doc.status, doc.closed_by), ("P03", ps.CLOSED, "Administrator"))
        self.assertIsNotNone(doc.closed_on)
        self.assertEqual(_row(_year(), 3).status, ps.CLOSED)

        # close_period's group-rate gate applies.
        with _rate_gate(failing={4}), self.assertRaisesRegex(frappe.ValidationError, "ZZ bench"):
            ps.set_status(FY, 4, ps.CLOSED)
        self.assertEqual(ps.get_status(FY, 4), ps.OPEN)

        # So does its role check.
        with _rate_gate(), _roles(["EPM Analyst"]), self.assertRaises(frappe.PermissionError):
            ps.set_status(FY, 4, ps.CLOSED)

        # Reopening needs a reason, which lands on the year's closing note.
        with self.assertRaisesRegex(frappe.ValidationError, f"Give a reason to reopen P03 of FY{FY}"):
            ps.set_status(FY, 3, ps.OPEN)
        doc = ps.set_status(FY, 3, ps.OPEN, reason=REASON)
        self.assertEqual((doc.status, doc.closed_by, doc.closed_on), (ps.OPEN, None, None))
        self.assertIn(REASON, _year().closing_note or "")

        # An undeclared period is refused, not created.
        with self.assertRaises(ps.PeriodNotDeclared):
            ps.set_status(FY, UNDECLARED, ps.CLOSED)


if __name__ == "__main__":
    frappe.init(site=os.environ["FRAPPE_SITE"], sites_path=os.environ.get("SITES_PATH", "."))
    frappe.connect()
    try:
        unittest.main(module=__name__, argv=sys.argv[:1], exit=False, verbosity=2)
    finally:
        frappe.db.rollback()
        frappe.destroy()
