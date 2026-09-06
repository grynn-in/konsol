"""Real-DB tests for entity-scoped access (#91).

Structural tests prove the wiring; only a database proves the thing that
matters — that a real non-admin user, with a real User Permission, sees only
their sub-tree in a real permission-aware query.

    bench --site <site> run-tests --module konsol.tests.test_entity_permissions_bench

Note: assertions use frappe.get_list, not get_all. get_all ignores permissions
by design, so it is the wrong probe — a filtering test written with it passes
whether or not the filter works.
"""
import unittest

import frappe

from konsol import entity_permissions as ep

EMAIL = "__konsol_perm_bench@example.com"


class EntityPermissionBenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not frappe.db.count("Entity"):
            raise unittest.SkipTest("no Entity records on this site")
        cls.group = frappe.db.get_value("Entity", {"is_group": 1}, "name")
        if not cls.group:
            raise unittest.SkipTest("no roll-up Entity to assign")

    def setUp(self):
        if frappe.db.exists("User", EMAIL):
            frappe.delete_doc("User", EMAIL, force=True, ignore_permissions=True)
        u = frappe.new_doc("User")
        u.email = EMAIL
        u.first_name = "Perm Bench"
        u.send_welcome_email = 0
        u.flags.ignore_permissions = True
        u.insert()
        u.add_roles("EPM User")

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback()

    # -- helpers ----------------------------------------------------------

    def _grant(self, entity):
        up = frappe.new_doc("User Permission")
        up.user = EMAIL
        up.allow = "Entity"
        up.for_value = entity
        up.flags.ignore_permissions = True
        up.insert()
        frappe.clear_cache(user=EMAIL)

    def _as_user(self, fn):
        frappe.set_user(EMAIL)
        try:
            return fn()
        finally:
            frappe.set_user("Administrator")

    def _descendants(self, code):
        row = frappe.db.get_value("Entity", code, ["lft", "rgt"], as_dict=True)
        return {
            r.name for r in frappe.get_all(
                "Entity", filters={"lft": [">=", row.lft], "rgt": ["<=", row.rgt]},
                fields=["name"], limit_page_length=0)
        }

    # -- the default ------------------------------------------------------

    def test_no_assignment_is_unrestricted(self):
        """Frappe's convention: a User Permission is an opt-in restriction."""
        self.assertIsNone(ep.allowed_entity_codes(EMAIL))

    def test_administrator_bypasses(self):
        self.assertIsNone(ep.allowed_entity_codes("Administrator"))

    # -- sub-tree expansion ----------------------------------------------

    def test_assignment_to_a_group_grants_its_subtree(self):
        self._grant(self.group)
        self.assertEqual(ep.allowed_entity_codes(EMAIL), self._descendants(self.group))

    def test_assignment_to_a_leaf_grants_only_itself(self):
        leaf = frappe.db.get_value("Entity", {"is_group": 0}, "name")
        if not leaf:
            self.skipTest("no leaf entities")
        self._grant(leaf)
        self.assertEqual(ep.allowed_entity_codes(EMAIL), {leaf})

    def test_assignment_to_a_deleted_entity_grants_nothing(self):
        """Must not fail open."""
        self._grant(self.group)
        frappe.db.sql("DELETE FROM `tabEntity` WHERE name = %s", self.group)
        self.assertEqual(ep.allowed_entity_codes(EMAIL), set())

    # -- the filter actually applies --------------------------------------

    def test_entity_list_is_filtered(self):
        self._grant(self.group)
        visible = set(self._as_user(
            lambda: frappe.get_list("Entity", pluck="name", limit_page_length=0)))
        self.assertEqual(visible, self._descendants(self.group))

    def test_entities_outside_the_subtree_are_hidden(self):
        self._grant(self.group)
        allowed = self._descendants(self.group)
        everything = {e.name for e in frappe.get_all("Entity", limit_page_length=0)}
        outside = everything - allowed
        if not outside:
            self.skipTest("single-tree site; nothing outside to hide")
        visible = set(self._as_user(
            lambda: frappe.get_list("Entity", pluck="name", limit_page_length=0)))
        self.assertEqual(visible & outside, set())

    def test_opening_a_document_outside_scope_is_refused(self):
        """has_permission, not query conditions — a direct fetch bypasses those."""
        self._grant(self.group)
        outside = ({e.name for e in frappe.get_all("Entity", limit_page_length=0)}
                   - self._descendants(self.group))
        if not outside:
            self.skipTest("single-tree site")
        target = sorted(outside)[0]

        def attempt():
            with self.assertRaises(frappe.PermissionError):
                frappe.get_doc("Entity", target).check_permission("read")

        self._as_user(attempt)

    def test_opening_a_document_inside_scope_is_allowed(self):
        self._grant(self.group)
        target = sorted(self._descendants(self.group))[0]
        self._as_user(lambda: frappe.get_doc("Entity", target).check_permission("read"))

    # -- the warehouse path ----------------------------------------------

    def test_assert_entity_access_guards_clickhouse_reads(self):
        """permission_query_conditions never run for ClickHouse queries."""
        self._grant(self.group)
        allowed = self._descendants(self.group)
        outside = ({e.name for e in frappe.get_all("Entity", limit_page_length=0)} - allowed)
        if outside:
            with self.assertRaises(frappe.PermissionError):
                ep.assert_entity_access(sorted(outside)[0], EMAIL)
        ep.assert_entity_access(sorted(allowed)[0], EMAIL)
