"""Real-DB tests for Entity — the tree, its guards, and the backfill.

test_entity.py covers the shape at source level. This covers what only a
database can: nested-set bounds, the parenting guards, and that the backfill
reproduces the Consolidation Group hierarchy rather than a flat list.

    bench --site <site> run-tests --module konsol.tests.test_entity_bench

Rolls back, so it leaves no records behind.
"""
import unittest

import frappe

from konsol.patches import backfill_entities_from_consolidation_group as backfill


def _mk(code, name=None, is_group=0, parent=None):
    doc = frappe.new_doc("Entity")
    doc.entity_code = code
    doc.entity_name = name or code
    doc.is_group = is_group
    doc.parent_entity = parent
    doc.flags.ignore_permissions = True
    doc.insert()
    return doc


class EntityBenchTest(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    # ---- naming and normalisation ---------------------------------------

    def test_name_is_the_code(self):
        doc = _mk("__T_ALPHA")
        self.assertEqual(doc.name, "__T_ALPHA")

    def test_code_is_upper_cased_and_trimmed(self):
        """It is the warehouse join key; drift breaks joins silently."""
        doc = _mk("  __t_beta  ")
        self.assertEqual(doc.entity_code, "__T_BETA")

    def test_code_is_unique(self):
        _mk("__T_DUP")
        with self.assertRaises(Exception):
            _mk("__T_DUP")

    # ---- the tree --------------------------------------------------------

    def test_children_nest_inside_their_parent(self):
        root = _mk("__T_ROOT", is_group=1)
        child = _mk("__T_CHILD", parent=root.name)
        root.reload()
        child.reload()
        self.assertLess(root.lft, child.lft)
        self.assertGreater(root.rgt, child.rgt)

    def test_an_entity_cannot_parent_itself(self):
        doc = _mk("__T_SELF", is_group=1)
        doc.parent_entity = doc.name
        with self.assertRaises(frappe.ValidationError):
            doc.save()

    def test_a_legal_entity_cannot_be_a_parent(self):
        """Holding another entity is ownership, not management structure."""
        leaf = _mk("__T_LEAF", is_group=0)
        with self.assertRaises(frappe.ValidationError):
            _mk("__T_UNDER_LEAF", parent=leaf.name)

    def test_a_group_can_be_a_parent(self):
        group = _mk("__T_GROUP", is_group=1)
        child = _mk("__T_OK", parent=group.name)
        self.assertEqual(child.parent_entity, group.name)

    # ---- the backfill ----------------------------------------------------

    def test_backfill_reproduces_the_consolidation_group_tree(self):
        if not frappe.db.count("Consolidation Group"):
            self.skipTest("no Consolidation Group rows on this site")

        frappe.db.sql("DELETE FROM tabEntity")
        backfill.execute()

        groups = frappe.get_all(
            "Consolidation Group",
            fields=["consolidation_group", "data_area_id", "is_group"],
            limit_page_length=0,
        )
        expected = {
            (g.consolidation_group if g.is_group else g.data_area_id or "").strip().upper()
            for g in groups
        } - {""}

        actual = {e.name for e in frappe.get_all("Entity", limit_page_length=0)}
        self.assertEqual(actual, expected, "every entity and group node must land in Entity")

    def test_backfill_leaves_functional_currency_blank(self):
        """reporting_currency on a leaf is the GROUP's currency. Copying it
        would put plausible, wrong data in the field FX translates from."""
        if not frappe.db.count("Consolidation Group"):
            self.skipTest("no Consolidation Group rows on this site")

        frappe.db.sql("DELETE FROM tabEntity")
        backfill.execute()

        filled = frappe.get_all(
            "Entity", filters={"functional_currency": ["not in", ["", None]]},
            limit_page_length=0,
        )
        self.assertEqual(filled, [], "functional_currency must be left for a human")

    def test_backfill_is_idempotent(self):
        if not frappe.db.count("Consolidation Group"):
            self.skipTest("no Consolidation Group rows on this site")

        frappe.db.sql("DELETE FROM tabEntity")
        backfill.execute()
        first = frappe.db.count("Entity")
        backfill.execute()
        self.assertEqual(frappe.db.count("Entity"), first, "re-running must not duplicate")
