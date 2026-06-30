"""Real-DB bench smoke test for the orchestrator single-flight guard (#64 / #67).

The unit/host suites (test_orchestrator_api, _wedge_guard, _67_review) cover the
guard at source/logic level with frappe mocked. They CANNOT exercise the part the
guard actually depends on: MariaDB named-lock semantics and InnoDB REPEATABLE READ
snapshot visibility across two connections. That is exactly where the subtle bug
lived (the GET_LOCK serialised execution but the second caller still read a stale
snapshot until a read-view-refreshing ``commit()``). This test proves the real DB
behaviour end to end.

Run on a live bench (it COMMITS marker rows and cleans them up — it does not use
the rollback harness, since cross-connection visibility requires real commits):

    bench --site <site> run-tests --module konsol.tests.test_single_flight_bench

Requires MariaDB (the guard is MariaDB-specific); skips on other backends.
"""
import unittest

import frappe

from konsol.orchestrator.api import _SINGLE_FLIGHT_LOCK, ACTIVE_RUN_STATES, _assert_no_active_run

MARKER = "__single_flight_bench__"


def _active_count(db):
    placeholders = ", ".join(["%s"] * len(ACTIVE_RUN_STATES))
    row = db.sql(
        f"SELECT COUNT(*) FROM `tabPipeline Run` WHERE status IN ({placeholders})",
        tuple(ACTIVE_RUN_STATES),
    )
    return int(row[0][0])


class SingleFlightBenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if frappe.conf.db_type not in (None, "mariadb"):
            raise unittest.SkipTest("single-flight guard is MariaDB-specific")

    def setUp(self):
        self._conn2 = None
        self._cleanup()

    def tearDown(self):
        # Best-effort release of the named lock on both connections, then purge
        # any marker rows this test committed.
        try:
            frappe.db.sql("SELECT RELEASE_LOCK(%s)", (_SINGLE_FLIGHT_LOCK,))
        except Exception:
            pass
        if self._conn2 is not None:
            try:
                self._conn2.sql("SELECT RELEASE_LOCK(%s)", (_SINGLE_FLIGHT_LOCK,))
                self._conn2.close()
            except Exception:
                pass
        self._cleanup()

    def _cleanup(self):
        frappe.db.delete("Pipeline Run", {"triggered_by": MARKER})
        frappe.db.commit()

    def _second_connection(self):
        conn = frappe.database.get_db(
            host=frappe.conf.db_host,
            user=frappe.conf.db_name,
            password=frappe.conf.db_password,
            port=frappe.conf.db_port,
            cur_db_name=frappe.conf.db_name,
        )
        conn.connect()
        self._conn2 = conn
        return conn

    def _make_active_run(self):
        doc = frappe.get_doc(
            {
                "doctype": "Pipeline Run",
                "status": "Running",
                "triggered_by": MARKER,
                "started_at": frappe.utils.now_datetime(),
            }
        )
        # triggered_by is a Link→User; the marker isn't a real user, so skip link
        # validation (these are throwaway rows purged in tearDown).
        doc.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True)
        frappe.db.commit()
        return doc.name

    # ------------------------------------------------------------------ proofs

    def test_named_lock_serialises_across_connections(self):
        """GET_LOCK is a global, cross-connection lock on this MariaDB."""
        conn2 = self._second_connection()

        got1 = frappe.db.sql("SELECT GET_LOCK(%s, 0)", (_SINGLE_FLIGHT_LOCK,))[0][0]
        self.assertEqual(got1, 1, "conn1 should acquire the lock")

        blocked = conn2.sql("SELECT GET_LOCK(%s, 0)", (_SINGLE_FLIGHT_LOCK,))[0][0]
        self.assertEqual(blocked, 0, "conn2 must be REFUSED while conn1 holds the lock")

        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (_SINGLE_FLIGHT_LOCK,))
        got2 = conn2.sql("SELECT GET_LOCK(%s, 0)", (_SINGLE_FLIGHT_LOCK,))[0][0]
        self.assertEqual(got2, 1, "conn2 should acquire once conn1 releases")
        conn2.sql("SELECT RELEASE_LOCK(%s)", (_SINGLE_FLIGHT_LOCK,))

    def test_read_view_refresh_exposes_committed_run(self):
        """The crux: under REPEATABLE READ a second caller pins its snapshot at
        its first read (before it acquires the lock). Without a refreshing
        commit() it would MISS a run another caller committed in the meantime and
        wrongly pass the guard. A commit() after acquiring the lock refreshes the
        read view so the just-committed run becomes visible. Prove both halves."""
        conn2 = self._second_connection()

        # conn2 pins its RR snapshot with a first read (mirrors a Frappe request
        # reading — session/roles — before it reaches GET_LOCK).
        before = _active_count(conn2)

        # conn1 commits a new active run (a competitor that won the lock first).
        self._make_active_run()

        # conn2, still on its pinned snapshot, must NOT see it yet — this is the
        # staleness the bug relied on.
        stale = _active_count(conn2)
        self.assertEqual(stale, before, "RR snapshot should hide the committed run pre-refresh")

        # The fix: commit() refreshes conn2's read view (a named lock survives it).
        conn2.commit()
        fresh = _active_count(conn2)
        self.assertEqual(fresh, before + 1, "post-commit read view must expose the committed run")

    def test_assert_no_active_run_guard_against_real_db(self):
        """The actual guard function: passes with no active run, throws with one."""
        # Guard should pass when nothing is active. Only assert this if the live
        # site genuinely has no active run (don't fail on a real in-flight run).
        if _active_count(frappe.db) == 0:
            try:
                _assert_no_active_run()
            except frappe.ValidationError:
                self.fail("_assert_no_active_run raised with no active run present")

        self._make_active_run()
        with self.assertRaises(frappe.ValidationError):
            _assert_no_active_run()
