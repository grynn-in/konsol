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


# --- pair C (KC1): consolidated budget and forecast accuracy ---------------
#
# konsol#348 S6-Q7 (a), taken under the standing rule: a `consolidated_budget`
# Dataset over konsolidat's `gold_consolidated_budget_fact` (WC1: the fully
# consolidated budget rolled to the group slot, the current round only for a
# forecast scenario), with scenario_id and layer arguments. FC-4-2 (13 Nov
# 2026): the accuracy model in entity currency (WC2) and in group currency
# (WC3), each a Dataset with the error measures. All seeded create-if-missing
# (konsol#230): `install_defaults` runs on every migrate and inserts a row a
# site lacks, so existing sites receive them with no patch.

MEASURES = os.path.join(APP_DIR, "defaults", "measure.json")
#: No forecast_error_pct (konsol#561 review M2): a read averaged the rows'
#: percentages, unweighted, with a period that had none read as 0, so it was
#: not error / |actual|. Excel computes the % in the sheet; the Forecast
#: Accuracy report shows it per row.
ACCURACY_MEASURES = ["forecast_amount", "actual_amount", "forecast_error",
                     "forecast_abs_error"]


def _measures():
    with open(MEASURES, encoding="utf-8") as fh:
        return {r["name"]: r for r in json.load(fh)}


def test_consolidated_budget_dataset_is_seeded():
    cb = _dataset("consolidated_budget")
    assert cb["doctype"] == "Dataset"
    assert cb["name"] == cb["fact_name"] == "consolidated_budget"
    assert cb["clickhouse_table"] == "epm_gold.gold_consolidated_budget_fact"
    assert cb["dbt_model"] == "gold_consolidated_budget_fact"
    assert cb["has_scenario_id"] == 1
    assert cb["has_layer"] == 1
    assert cb["default_measure"] == "consolidated_amount"
    assert cb["status"] == "Published"
    assert not cb.get("generates_source")
    assert [m["measure"] for m in cb["fact_measures"]] == ["consolidated_amount"]


def test_forecast_accuracy_datasets_are_seeded():
    for key, table in (("forecast_accuracy", "gold_forecast_accuracy"),
                       ("forecast_accuracy_group", "gold_forecast_accuracy_group")):
        ds = _dataset(key)
        assert ds["name"] == ds["fact_name"] == key
        assert ds["clickhouse_table"] == f"epm_gold.{table}", key
        assert ds["dbt_model"] == table, key
        assert ds["has_scenario_id"] == 1, key
        assert ds["has_layer"] == 0, key  # layers summed in the model
        assert ds["default_measure"] == "forecast_error", key
        assert ds["status"] == "Published", key
        assert not ds.get("generates_source"), key
        assert [m["measure"] for m in ds["fact_measures"]] == ACCURACY_MEASURES, key


def test_accuracy_measures_are_seeded_with_their_aggregation():
    measures = _measures()
    for name in ("forecast_amount", "forecast_error", "forecast_abs_error"):
        m = measures[name]
        assert m["doctype"] == "Measure" and m["measure_name"] == name
        assert m["expression"] == f"sum({name})" and m["cube_type"] == "sum", m
        assert m["status"] == "Published"
    assert "forecast_error_pct" not in measures, "nothing reads it (konsol#561 review M2)"


def test_every_seeded_dataset_measure_is_a_seeded_measure():
    """A Dataset row naming a Measure the seed lacks inserts a link to nothing
    on a fresh site (defaults.ORDER seeds Measures first for this reason)."""
    measures = _measures()
    with open(DATASETS, encoding="utf-8") as fh:
        for row in json.load(fh):
            for m in row.get("fact_measures", []):
                assert m["measure"] in measures, (row["name"], m["measure"])


# --- konsol#561 review M2: retire forecast_error_pct on an existing site ------
#
# install_defaults is create-if-missing, so a site seeded with the accuracy
# Datasets keeps their forecast_error_pct row and the Measure. The patch
# removes the row from the two shipped Datasets, then the Measure if nothing
# else names it and it still holds the shipped expression.

RETIRE_PY = os.path.join(APP_DIR, "patches", "retire_forecast_error_pct_measure.py")
PCT = "forecast_error_pct"
_KEPT = ["actual_amount", "forecast_abs_error", "forecast_amount", "forecast_error"]


class _PctSite:
    """Datasets {name: {"default_measure", "rows": [measure, ...], "measures": json}}
    and Measures {name: expression}."""

    def __init__(self, datasets, measures):
        self.datasets = {k: dict(v, rows=list(v["rows"])) for k, v in datasets.items()}
        self.measures = dict(measures)
        self.writes = []
        self.printed = []

    def stub(self):
        site = self
        frappe = types.ModuleType("frappe")

        def _match(ds, row, filters):
            return all({"parent": ds, "parenttype": "Dataset", "measure": row}[k] == v
                       for k, v in filters.items())

        class DB:
            def exists(self, doctype, name):
                return name in {"Dataset": site.datasets, "Measure": site.measures}[doctype]

            def get_value(self, doctype, name, fields, as_dict=False):
                if doctype == "Measure":
                    assert fields == "expression", fields
                    return site.measures.get(name)
                assert doctype == "Dataset", doctype
                d = site.datasets[name]
                return types.SimpleNamespace(**{f: d[f] for f in fields})

            def count(self, doctype, filters):
                assert doctype == "Dataset Measure", doctype
                return sum(1 for ds, d in site.datasets.items() for r in d["rows"]
                           if _match(ds, r, filters))

            def delete(self, doctype, filters):
                site.writes.append(("delete", doctype, dict(filters)))
                if doctype == "Measure":
                    site.measures.pop(filters["name"], None)
                    return
                assert doctype == "Dataset Measure", doctype
                for ds, d in site.datasets.items():
                    d["rows"] = [r for r in d["rows"] if not _match(ds, r, filters)]

            def set_value(self, doctype, name, field, value, update_modified=True):
                assert doctype == "Dataset" and update_modified is False
                site.writes.append(("set", name, field, value))
                site.datasets[name][field] = value

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
            spec = importlib.util.spec_from_file_location("retire_forecast_error_pct_t", RETIRE_PY)
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


def _seeded(default="forecast_error", extra=None):
    rows = _KEPT + [PCT]
    datasets = {name: {"default_measure": default, "rows": list(rows),
                       "measures": json.dumps(sorted(rows))}
                for name in ("forecast_accuracy", "forecast_accuracy_group")}
    datasets.update(extra or {})
    return datasets


def test_retire_pct_patch_removes_the_row_and_the_measure_once():
    site = _PctSite(_seeded(), {PCT: "avg(forecast_error_pct)"}).run()
    for name in ("forecast_accuracy", "forecast_accuracy_group"):
        d = site.datasets[name]
        assert d["rows"] == _KEPT, name
        assert json.loads(d["measures"]) == _KEPT, name
    assert PCT not in site.measures
    assert any(PCT in p for p in site.printed), site.printed
    site.writes.clear()
    site.run()
    assert site.writes == []


def test_retire_pct_patch_leaves_a_dataset_whose_default_is_the_pct():
    datasets = _seeded()
    datasets["forecast_accuracy"]["default_measure"] = PCT
    site = _PctSite(datasets, {PCT: "avg(forecast_error_pct)"}).run()
    assert PCT in site.datasets["forecast_accuracy"]["rows"]
    assert PCT not in site.datasets["forecast_accuracy_group"]["rows"]
    assert PCT in site.measures  # still named by forecast_accuracy
    msg = " ".join(site.printed)
    assert "forecast_accuracy" in msg and "default" in msg.lower(), msg


def test_retire_pct_patch_keeps_a_measure_another_dataset_names():
    extra = {"zz_site_acc": {"default_measure": "forecast_error", "rows": [PCT],
                             "measures": json.dumps([PCT])}}
    site = _PctSite(_seeded(extra=extra), {PCT: "avg(forecast_error_pct)"}).run()
    assert site.datasets["zz_site_acc"]["rows"] == [PCT]
    assert PCT in site.measures
    assert "zz_site_acc" not in [w[1] for w in site.writes if w[0] == "set"]


def test_retire_pct_patch_keeps_a_measure_the_site_redefined():
    site = _PctSite(_seeded(), {PCT: "sum(forecast_error) / sum(abs(actual_amount))"}).run()
    assert PCT in site.measures
    assert site.datasets["forecast_accuracy"]["rows"] == _KEPT


def test_retire_pct_patch_does_nothing_on_a_site_without_them():
    site = _PctSite({}, {}).run()
    assert site.writes == []


def test_retire_pct_patch_is_registered():
    with open(PATCHES_TXT, encoding="utf-8") as fh:
        lines = [l.strip() for l in fh]
    assert "konsol.patches.retire_forecast_error_pct_measure" in lines
