"""Budget S6 KA5 (konsol#348 S6-Q1 b, S6-Q2 a): the forecast Datasets read one round.

The shipped ``forecast_input`` Dataset (the Excel ``K.EPM(..., "forecast")``
flat read) binds ``scenario_id`` and ``layer`` but never ``cycle``; over
``gold_spread_budget`` it would add every approved round of a forecast year.
It now reads ``gold_forecast_current``, the fact narrowed to the declared
current round (konsolidat S6 WA6). A new ``latest_estimate`` Dataset reads
``gold_latest_estimate_current`` (WA8): actuals to the as-of, forecast after.

Datasets are seeded create-if-missing (konsol#230), so an existing site keeps
the old table until the ``repoint_forecast_dataset`` patch moves it. The patch
moves only the unchanged seed: a site that pointed ``forecast_input`` elsewhere
is left alone and named in the migrate log. Host-run under a stub frappe.
"""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASETS = os.path.join(APP_DIR, "defaults", "dataset.json")
PATCH_PY = os.path.join(APP_DIR, "patches", "repoint_forecast_dataset.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")

OLD_TABLE, OLD_MODEL = "epm_gold.gold_spread_budget", "gold_spread_budget"
NEW_TABLE, NEW_MODEL = "epm_gold.gold_forecast_current", "gold_forecast_current"


def _dataset(scenario_key):
    with open(DATASETS, encoding="utf-8") as fh:
        rows = [r for r in json.load(fh) if r.get("scenario_key") == scenario_key]
    assert len(rows) == 1, f"{len(rows)} seeded Datasets carry scenario_key {scenario_key!r}"
    return rows[0]


def test_forecast_dataset_reads_the_current_round_view():
    forecast = _dataset("forecast")
    assert forecast["name"] == "forecast_input"
    assert forecast["clickhouse_table"] == NEW_TABLE
    assert forecast["dbt_model"] == NEW_MODEL
    assert forecast["has_scenario_id"] == 1 and forecast["has_layer"] == 1
    assert forecast["default_measure"] == "period_amount"


def test_budget_dataset_still_reads_the_fact():
    budget = _dataset("budget")
    assert budget["clickhouse_table"] == OLD_TABLE
    assert budget["dbt_model"] == OLD_MODEL


def test_latest_estimate_dataset_is_seeded():
    le = _dataset("latest_estimate")
    assert le["doctype"] == "Dataset"
    assert le["name"] == le["fact_name"] == "latest_estimate"
    assert le["clickhouse_table"] == "epm_gold.gold_latest_estimate_current"
    assert le["dbt_model"] == "gold_latest_estimate_current"
    assert le["has_scenario_id"] == 1
    assert le["has_layer"] == 0
    assert le["default_measure"] == "period_amount"
    assert le["status"] == "Published"
    assert not le.get("generates_source")
    assert [m["measure"] for m in le["fact_measures"]] == ["period_amount"]


# --- the patch --------------------------------------------------------------

class _Site:
    def __init__(self, row=None):
        self.row = dict(row) if row else None
        self.writes = []
        self.printed = []

    def stub(self):
        site = self
        frappe = types.ModuleType("frappe")

        class DB:
            def get_value(self, doctype, name, fields, as_dict=False):
                assert doctype == "Dataset" and name == "forecast_input", (doctype, name)
                if site.row is None:
                    return None
                return types.SimpleNamespace(**{f: site.row.get(f) for f in fields}) if as_dict \
                    else tuple(site.row.get(f) for f in fields)

            def set_value(self, doctype, name, values, update_modified=True):
                site.writes.append((doctype, name, dict(values)))
                site.row.update(values)

        frappe.db = DB()
        return frappe

    def run(self):
        frappe = self.stub()
        saved = sys.modules.get("frappe")
        sys.modules["frappe"] = frappe
        import builtins
        real_print = builtins.print
        builtins.print = lambda *a, **k: self.printed.append(" ".join(str(x) for x in a))
        try:
            spec = importlib.util.spec_from_file_location("repoint_forecast_dataset_t", PATCH_PY)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            m.execute()
        finally:
            builtins.print = real_print
            if saved is None:
                sys.modules.pop("frappe", None)
            else:
                sys.modules["frappe"] = saved
        return self


def test_patch_repoints_the_unchanged_seed_once():
    site = _Site({"clickhouse_table": OLD_TABLE, "dbt_model": OLD_MODEL}).run()
    assert site.writes == [("Dataset", "forecast_input",
                            {"clickhouse_table": NEW_TABLE, "dbt_model": NEW_MODEL})]
    assert any("forecast_input" in p and NEW_TABLE in p for p in site.printed), site.printed
    site.writes.clear()
    site.run()
    assert site.writes == []


def test_patch_leaves_a_site_change_alone_and_names_it():
    site = _Site({"clickhouse_table": "epm_gold.zz_site_forecast", "dbt_model": "zz_site_forecast"}).run()
    assert site.writes == []
    assert site.row["clickhouse_table"] == "epm_gold.zz_site_forecast"
    msg = " ".join(site.printed)
    assert "forecast_input" in msg and "epm_gold.zz_site_forecast" in msg and NEW_TABLE in msg, msg


def test_patch_does_nothing_when_the_dataset_is_absent():
    site = _Site(None).run()
    assert site.writes == []


def test_patch_is_registered():
    with open(PATCHES_TXT, encoding="utf-8") as fh:
        lines = [l.strip() for l in fh]
    assert "konsol.patches.repoint_forecast_dataset" in lines
