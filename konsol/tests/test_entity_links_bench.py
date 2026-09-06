"""Real-DB tests for data_area_id as a Link to Entity.

The structural suite proves the field types. Only a database proves the thing
that actually matters: that converting a live column to a Link did not strand
existing rows. A Link is validated on every save, so a row holding a code with
no matching Entity becomes unsaveable — and it surfaces later, during an
unrelated edit, as "Could not find Entity: XYZ".

    bench --site <site> run-tests --module konsol.tests.test_entity_links_bench

Rolls back.
"""
import unittest

import frappe

from konsol.patches import ensure_entities_for_data_areas as sweep

DOCTYPES = sweep.REFERRING_DOCTYPES


class EntityLinkBenchTest(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_every_referenced_code_has_an_entity(self):
        """The precondition the conversion depends on. If this fails, some row
        somewhere is already unsaveable."""
        known = {e.name for e in frappe.get_all("Entity", limit_page_length=0)}
        for doctype in DOCTYPES:
            if not frappe.db.table_exists(doctype):
                continue
            codes = {
                r.data_area_id
                for r in frappe.db.sql(
                    f"SELECT DISTINCT data_area_id FROM `tab{doctype}` "
                    "WHERE data_area_id IS NOT NULL AND data_area_id != ''",
                    as_dict=True,
                )
            }
            missing = codes - known
            self.assertEqual(missing, set(), f"{doctype} references entities that do not exist: {missing}")

    def test_existing_rows_still_save(self):
        """Re-saving is what runs Link validation against real stored values."""
        for doctype in DOCTYPES:
            if not frappe.db.table_exists(doctype):
                continue
            for row in frappe.get_all(doctype, limit_page_length=3):
                doc = frappe.get_doc(doctype, row.name)
                doc.flags.ignore_permissions = True
                doc.save()  # must not raise

    def test_an_unknown_entity_is_refused(self):
        name = frappe.db.get_value("Consolidation Group", {"is_group": 0}, "name")
        if not name:
            self.skipTest("no leaf Consolidation Group rows")
        doc = frappe.get_doc("Consolidation Group", name)
        doc.data_area_id = "__NO_SUCH_ENTITY"
        doc.flags.ignore_permissions = True
        with self.assertRaises(frappe.LinkValidationError):
            doc.save()

    def test_roll_up_nodes_may_have_no_entity(self):
        """Consolidation Group's group nodes carry no data area, and a Link
        that is not required must still accept empty."""
        name = frappe.db.get_value("Consolidation Group", {"is_group": 1}, "name")
        if not name:
            self.skipTest("no group nodes")
        doc = frappe.get_doc("Consolidation Group", name)
        self.assertFalse(doc.data_area_id)
        doc.flags.ignore_permissions = True
        doc.save()  # must not raise

    def test_the_stored_value_is_still_the_warehouse_code(self):
        """An Entity is named by its code, so the Link stores exactly what the
        Data field stored — which is why the ClickHouse join is untouched."""
        row = frappe.db.get_value(
            "Consolidation Group", {"is_group": 0}, ["name", "data_area_id"], as_dict=True
        )
        if not row:
            self.skipTest("no leaf rows")
        self.assertTrue(frappe.db.exists("Entity", row.data_area_id))
        code = frappe.db.get_value("Entity", row.data_area_id, "entity_code")
        self.assertEqual(code, row.data_area_id)

    def test_sweep_is_idempotent(self):
        before = frappe.db.count("Entity")
        sweep.execute()
        self.assertEqual(frappe.db.count("Entity"), before, "re-running must create nothing")
