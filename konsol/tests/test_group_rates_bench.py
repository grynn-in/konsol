"""Real-DB and real-warehouse bench tests for the group exchange rates (konsol#103,
joint review of #174).

The host tests run the rules against a stub frappe. These run what the stubs
cannot: the upgrade patch against a real ISO Currency table (without its
usd_log10 column, and with the column but no values), the gate's SQL against
ClickHouse, and concurrent publishes.

Run on a live bench (ZZ data only, FY2099; rolled back or deleted):

    bench --site <site> run-tests --module konsol.tests.test_group_rates_bench

or, where tests are disabled for the site, from a plain script that calls
``frappe.init(site); frappe.connect()`` and then ``unittest.main(module=...)``.
"""
import os
import subprocess
import sys
import time
import unittest

import frappe

from konsol import group_rates
from konsol.clickhouse import execute

FY, P = 2099, 12
PATCH = "konsol.patches.adopt_erp_rates_as_group_exchange_rates"
#: What the stubbed warehouse says FY2099 P12 was translated at.
USED = [("JPY", "USD", FY, P, [0.006607], [0.0066]), ("EUR", "CHF", FY, P, [0.9478], [0.9422])]


def _count(sql):
    return int(execute(sql + " FORMAT TSV") or 0)


class _StubbedWarehouse:
    """group_rates.translated_rates / erp_quote_rows answer with ZZ FY2099 data."""

    def __enter__(self):
        self.saved = group_rates.translated_rates, group_rates.erp_quote_rows
        group_rates.translated_rates = lambda: USED
        group_rates.erp_quote_rows = lambda as_of: []
        return self

    def __exit__(self, *exc):
        group_rates.translated_rates, group_rates.erp_quote_rows = self.saved


def _run_patch():
    return frappe.get_attr(f"{PATCH}.execute")()


class AdoptionPatchBenchTest(unittest.TestCase):
    """The patch runs before model sync and before fixtures, so it must bring
    its own ISO Currency column and references."""

    def setUp(self):
        frappe.set_user("Administrator")
        if frappe.db.count("Group Exchange Rate", {"fiscal_year": FY}):
            self.skipTest("FY2099 Group Exchange Rates exist on this site")
        self.values = dict(frappe.db.sql("SELECT name, usd_log10 FROM `tabISO Currency`"))

    def tearDown(self):
        frappe.db.rollback()
        # put back what the site had (the column, then its values), committed
        if "usd_log10" not in frappe.db.get_table_columns("ISO Currency"):
            frappe.reload_doc("epm", "doctype", "iso_currency", force=True)
        for name, value in self.values.items():
            frappe.db.set_value("ISO Currency", name, "usd_log10", value, update_modified=False)
        frappe.db.commit()

    def _adopted(self, summary):
        rows = frappe.get_all("Group Exchange Rate", filters={"fiscal_year": FY, "docstatus": 1},
                              fields=["from_currency", "to_currency", "rate_type", "source"])
        return summary, rows

    def test_a_site_without_the_usd_log10_column(self):
        """Upgrading from main: the ISO Currency table has no usd_log10 and its
        DocType is the old one. MariaDB 1054 used to fail every migrate."""
        frappe.db.sql_ddl("ALTER TABLE `tabISO Currency` DROP COLUMN usd_log10")
        frappe.db.sql("UPDATE `tabDocType` SET migration_hash = 'zz-stale' WHERE name = 'ISO Currency'")
        frappe.db.commit()
        frappe.clear_cache(doctype="ISO Currency")
        with _StubbedWarehouse():
            summary, rows = self._adopted(_run_patch())
        self.assertEqual(len(summary["adopted"]), 4, summary)
        self.assertEqual((summary["refused"], summary["skipped"]), ([], []))
        self.assertEqual({r.source for r in rows}, {"Adoption"})
        self.assertIn("usd_log10", frappe.db.get_table_columns("ISO Currency"))

    def test_a_site_with_the_column_but_no_values(self):
        """Every non-USD value 0: every row used to be refused, and the patch
        still looked done."""
        frappe.db.sql("UPDATE `tabISO Currency` SET usd_log10 = 0")
        with _StubbedWarehouse():
            summary, rows = self._adopted(_run_patch())
        self.assertEqual(len(summary["adopted"]), 4, summary)
        self.assertEqual(summary["refused"], [])
        self.assertEqual(float(frappe.db.get_value("ISO Currency", "JPY", "usd_log10")), 2.17)

    def test_a_missing_reference_stops_the_adoption_before_it_writes(self):
        frappe.db.sql("UPDATE `tabISO Currency` SET usd_log10 = 0 WHERE name = 'JPY'")
        with _StubbedWarehouse(), self.assertRaises(RuntimeError) as caught:
            group_rates.adopt_erp_rates()
        self.assertIn("no magnitude reference (ISO Currency usd_log10) for JPY", str(caught.exception))
        self.assertEqual(frappe.db.count("Group Exchange Rate", {"fiscal_year": FY}), 0)

    def test_a_site_edit_survives_the_seed(self):
        from konsol.currency_references import seed_iso_currencies

        frappe.db.set_value("ISO Currency", "EUR", "usd_log10", -0.04)
        seed_iso_currencies()
        self.assertEqual(float(frappe.db.get_value("ISO Currency", "EUR", "usd_log10")), -0.04)


ZZ_ROWS = (("ZZFU", "VND", "full", P), ("ZZEQ", "KRW", "equity", P), ("ZZNC", "IDR", "full", P),
           ("ZZT1", "VND", "full", 11), ("ZZT2", "KRW", "equity", 11))


class GateBenchTest(unittest.TestCase):
    """The gate's pairs, read from ClickHouse: what translation translates."""

    def setUp(self):
        groups = {"ZZNC": "ZZ_NOCURRENCY"}
        execute("INSERT INTO epm_gold.gold_trial_balance (data_area_id, fiscal_year, fiscal_period, main_account, "
                "period_debit) VALUES " + ", ".join(f"('{e}', {FY}, {p}, '1100', 100)" for e, _, _, p in ZZ_ROWS))
        execute("INSERT INTO epm_silver.silver_entity_currencies (data_area_id, accounting_currency) VALUES "
                + ", ".join(f"('{e}', '{c}')" for e, c, _, _ in ZZ_ROWS))
        execute("INSERT INTO epm_gold.gold_entity_ownership (consolidation_group, data_area_id, fiscal_year, "
                "fiscal_period, has_complete_chain, consolidation_method) VALUES "
                + ", ".join(f"('{groups.get(e, 'GROUP_CORP')}', '{e}', {FY}, {p}, 1, '{m}')"
                            for e, _, m, p in ZZ_ROWS))
        execute("INSERT INTO epm_gold.consolidation_groups (consolidation_group, data_area_id, reporting_currency) "
                "VALUES ('ZZ_NOCURRENCY', '', '')")

    def tearDown(self):
        for table in ("epm_gold.gold_trial_balance", "epm_silver.silver_entity_currencies",
                      "epm_gold.gold_entity_ownership"):
            execute(f"ALTER TABLE {table} DELETE WHERE data_area_id LIKE 'ZZ%' SETTINGS mutations_sync = 1")
        execute("ALTER TABLE epm_gold.consolidation_groups DELETE WHERE consolidation_group LIKE 'ZZ%' "
                "SETTINGS mutations_sync = 1")

    def test_equity_is_excluded_and_a_group_without_a_currency_blocks(self):
        pairs, groups = group_rates.translation_needs(FY, P)
        self.assertIn(("VND", "USD"), pairs)
        self.assertNotIn(("KRW", "USD"), pairs, "ZZEQ is held at equity")
        self.assertEqual(groups, ["ZZ_NOCURRENCY"])
        missing, error, blockers = group_rates.rate_gate(FY, P)
        self.assertIsNone(error)
        self.assertEqual(blockers, ["Consolidation Group ZZ_NOCURRENCY has no reporting currency"])
        self.assertEqual({(f, t) for f, t, _ in missing}, {("VND", "USD")})

    def test_the_pre_fill_fallback_uses_the_latest_ownership_and_the_same_filter(self):
        """FY2099 P13 has no ledgers: each entity takes its latest built period."""
        pairs = group_rates.tree_pairs(FY, 13)
        self.assertIn(("VND", "USD"), pairs)
        self.assertNotIn(("KRW", "USD"), pairs, "an equity-accounted currency owes no rate")


PUBLISHER = ("import frappe; frappe.init(site={site!r}); frappe.connect(); "
             "from konsol.group_rates import publish_rates; print(publish_rates(force=True)); frappe.destroy()")
#: A reader in its own process (frappe.local is per thread): counts the
#: published rows until the stop file appears, one count per line.
READER = ("import os, frappe; frappe.init(site={site!r}); frappe.connect(); "
          "from konsol.clickhouse import execute\n"
          "while not os.path.exists({stop!r}):\n"
          "    print(execute('SELECT count() FROM epm_staging.group_exchange_rates'), flush=True)\n"
          "frappe.destroy()")


class PublishBenchTest(unittest.TestCase):
    """Two publishes at once used to duplicate every key; a reader mid-way
    saw part of the set."""

    def test_concurrent_publishes_leave_one_row_per_rate_and_never_a_partial_set(self):
        approved = frappe.db.count("Group Exchange Rate", {"docstatus": 1})
        if not approved:
            self.skipTest("no approved Group Exchange Rate to publish")
        site = frappe.local.site
        stop = os.path.abspath("zz103_publish_reader_stop")
        if os.path.exists(stop):
            os.remove(stop)
        reader = subprocess.Popen([sys.executable, "-c", READER.format(site=site, stop=stop)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(2)   # the reader is reading before the first publish starts
        procs = [subprocess.Popen([sys.executable, "-c", PUBLISHER.format(site=site)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
        results = [p.communicate(timeout=300) for p in procs]
        time.sleep(1)
        open(stop, "w").close()
        out, err = reader.communicate(timeout=60)
        os.remove(stop)
        counts = [int(line) for line in out.split() if line.strip().isdigit()]
        published = [out.strip().splitlines()[-1] if out.strip() else err[-200:] for out, err in results]
        self.assertEqual(published, [str(approved)] * 6, published)
        self.assertEqual(_count("SELECT count() FROM epm_staging.group_exchange_rates"), approved)
        self.assertEqual(_count("SELECT count() FROM (SELECT DISTINCT to_currency, from_currency, fiscal_year, "
                                "fiscal_period, rate_type FROM epm_staging.group_exchange_rates)"), approved)
        self.assertTrue(counts, "the reader read")
        self.assertEqual(set(counts), {approved}, f"a reader saw {sorted(set(counts))}")
        print(f"\n  6 concurrent publishes of {approved} rates; a reader read {len(counts)} times, "
              f"always {sorted(set(counts))}")
