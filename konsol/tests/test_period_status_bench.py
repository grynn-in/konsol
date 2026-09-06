"""Real-DB bench test for period close state.

test_period_status.py covers the shape of the DocType and the presence of the
guards at source level. It cannot exercise the property the whole design exists
for: that closing one period leaves every other period — and the same period in
every other year — untouched.

That property is why status is a ``Period Status`` record keyed by (fiscal
year, fiscal period) rather than a field on ``Fiscal Period``. ``Fiscal
Period`` is a template of fourteen records that every year reuses, so a status
there would make closing September 2024 also close September 2025. This test
proves the isolation against a real database.

Run on a live bench:

    bench --site <site> run-tests --module konsol.tests.test_period_status_bench

Rolls back, so it leaves no records behind.
"""
import unittest

import frappe

from konsol import period_status as ps

YEAR = "2024"
OTHER_YEAR = "2025"


def _any_period_number():
    """A real period number from this site's calendar, so the Int validation
    passes without the test inventing one."""
    row = frappe.db.get_value(
        "Fiscal Period", {"fiscal_period": [">=", 1]}, "fiscal_period", order_by="fiscal_period desc"
    )
    return int(row) if row is not None else None


class PeriodStatusBenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.period = _any_period_number()
        if cls.period is None:
            raise unittest.SkipTest("no Fiscal Period records on this site")

    def tearDown(self):
        frappe.db.rollback()

    # ---- default ---------------------------------------------------------

    def test_period_with_no_record_is_open(self):
        self.assertEqual(ps.get_status(YEAR, self.period), ps.OPEN)
        self.assertTrue(ps.is_open(YEAR, self.period))

    def test_assert_open_passes_while_open(self):
        ps.assert_open(YEAR, self.period)  # must not raise

    # ---- closing ---------------------------------------------------------

    def test_closing_records_who_and_when(self):
        doc = ps.set_status(YEAR, self.period, ps.CLOSED)
        self.assertEqual(doc.status, ps.CLOSED)
        self.assertEqual(doc.closed_by, frappe.session.user)
        self.assertIsNotNone(doc.closed_on)

    def test_closed_period_refuses_new_work(self):
        ps.set_status(YEAR, self.period, ps.CLOSED)
        with self.assertRaises(frappe.ValidationError):
            ps.assert_open(YEAR, self.period, action="start this run")

    def test_reopening_clears_the_closure_stamp(self):
        ps.set_status(YEAR, self.period, ps.CLOSED)
        doc = ps.set_status(YEAR, self.period, ps.OPEN)
        self.assertIsNone(doc.closed_by)
        self.assertIsNone(doc.closed_on)

    # ---- isolation: the reason this is its own doctype -------------------

    def test_closing_one_period_leaves_its_neighbour_open(self):
        neighbour = self.period - 1
        if neighbour < 1:
            self.skipTest("needs two accounting periods")
        ps.set_status(YEAR, self.period, ps.CLOSED)
        self.assertEqual(ps.get_status(YEAR, neighbour), ps.OPEN)

    def test_closing_a_period_leaves_the_same_period_in_another_year_open(self):
        """The bug avoided by not putting status on the shared Fiscal Period."""
        ps.set_status(YEAR, self.period, ps.CLOSED)
        self.assertEqual(ps.get_status(OTHER_YEAR, self.period), ps.OPEN)

    # ---- locking ---------------------------------------------------------

    def test_locked_period_refuses_reopen_below_system_manager(self):
        ps.set_status(YEAR, self.period, ps.LOCKED)
        original = frappe.get_roles
        frappe.get_roles = lambda *a, **k: ["EPM Admin"]
        try:
            with self.assertRaises(frappe.PermissionError):
                ps.set_status(YEAR, self.period, ps.OPEN)
        finally:
            frappe.get_roles = original

    def test_system_manager_can_reopen_a_locked_period(self):
        ps.set_status(YEAR, self.period, ps.LOCKED)
        ps.set_status(YEAR, self.period, ps.OPEN)
        self.assertEqual(ps.get_status(YEAR, self.period), ps.OPEN)

    # ---- validation ------------------------------------------------------

    def test_unknown_period_number_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            ps.set_status(YEAR, 99, ps.CLOSED)
