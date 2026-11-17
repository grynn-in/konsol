"""Retire the variance_favorable Measure (konsol#348 S6 KB1; S6-Q4 a, taken
under the standing rule; konsolidat#214), host-run.

The warehouse column ``gold_variance_analysis.variance_favorable`` (a Bool)
becomes ``variance_favourability`` (a String with three states). A String
cannot be summed, and the Bool never should have been: konsol shipped it as a
``sum`` Measure and as a row of the ``variance_analysis`` Dataset. Both leave
the defaults, and the patch ``retire_variance_favorable_measure`` takes them
off a site that already seeded them (defaults are create-if-missing,
konsol#230, so a site keeps what it was given until a patch says otherwise):

1. the ``variance_analysis`` Dataset's child row naming the Measure goes;
2. where no other Dataset names the Measure, it is unpublished and deleted;
   where one does (a site-made Dataset), the Measure stays and the patch
   logs the Datasets by name: a site's own semantic model is never edited
   silently (konsol#230);
3. every step is logged by name; a rerun, or a site that never seeded the
   Measure, is a no-op.

A stub frappe records every call over an in-memory site.
"""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "retire_variance_favorable_measure.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
MODULE = "konsol.patches.retire_variance_favorable_measure"
MEASURE = "variance_favorable"
DATASET = "variance_analysis"


def _json(name):
    with open(os.path.join(APP_DIR, "defaults", name), encoding="utf-8") as fh:
        return json.load(fh)


# --- the defaults no longer ship it ------------------------------------------

def test_measure_json_no_longer_ships_the_flag_and_keeps_the_other_variance_measures():
    names = [row["name"] for row in _json("measure.json")]
    assert MEASURE not in names
    for kept in ("actual_amount", "budget_amount", "variance_abs", "variance_pct"):
        assert kept in names, kept


def test_variance_dataset_no_longer_lists_the_flag():
    dataset = next(row for row in _json("dataset.json") if row["name"] == DATASET)
    measures = [m["measure"] for m in dataset["fact_measures"]]
    assert MEASURE not in measures
    assert measures == ["actual_amount", "budget_amount", "variance_abs", "variance_pct"]
    assert dataset["clickhouse_table"] == "epm_gold.gold_variance_analysis"


def test_nothing_in_the_app_names_the_flag_outside_tests_and_patches():
    """The retired name survives only where history is recorded (the patch and
    the tests). hierarchy_query's variance measure set never listed it."""
    found = []
    for root, dirs, files in os.walk(APP_DIR):
        rel = os.path.relpath(root, APP_DIR)
        if rel.split(os.sep)[0] in ("tests", "patches", "node_modules") or "__pycache__" in rel:
            dirs[:] = []
            continue
        for name in files:
            if not name.endswith((".py", ".js", ".json", ".md", ".html", ".txt")) or name == "patches.txt":
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            if MEASURE in text:
                found.append(os.path.relpath(path, APP_DIR))
    assert found == [], found


def test_registered_in_patches_txt():
    with open(PATCHES_TXT) as fh:
        assert MODULE in [line.strip() for line in fh]


# --- the patch over a stub site ----------------------------------------------

class _Doc:
    def __init__(self, site, doctype, name, **fields):
        self._site, self.doctype, self.name = site, doctype, name
        self.__dict__.update(fields)
        self.saves = []

    def save(self, *a, **k):
        self._site.calls.append(("save", self.doctype, self.name, self.status))
        self._site.measures[self.name]["status"] = self.status


class _Site:
    def __init__(self, measure=True, dataset_row=True, other_datasets=()):
        self.calls = []
        self.tables = {"Measure", "Dataset", "Dataset Measure"}
        self.measures = {MEASURE: {"name": MEASURE, "status": "Published", "cube_type": "sum"}} if measure else {}
        self.measures["variance_abs"] = {"name": "variance_abs", "status": "Published", "cube_type": "sum"}
        # Dataset Measure child rows: (name, parent, measure).
        self.dataset_measures = [{"name": "dm-1", "parent": DATASET, "parenttype": "Dataset", "measure": "variance_abs"}]
        if dataset_row:
            self.dataset_measures.append({"name": "dm-2", "parent": DATASET, "parenttype": "Dataset", "measure": MEASURE})
        for parent in other_datasets:
            self.dataset_measures.append({"name": "dm-" + parent, "parent": parent, "parenttype": "Dataset",
                                          "measure": MEASURE})
        self.logs = []

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")

        def table_exists(doctype, *a, **k):
            site.calls.append(("table_exists", doctype))
            return doctype in site.tables

        def exists(doctype, name, *a, **k):
            site.calls.append(("exists", doctype, name))
            assert doctype == "Measure", doctype
            return name if name in site.measures else None

        def get_all(doctype, filters=None, fields=None, **k):
            site.calls.append(("get_all", doctype, dict(filters or {})))
            assert doctype == "Dataset Measure", doctype
            rows = [r for r in site.dataset_measures
                    if all(r.get(key) == value for key, value in (filters or {}).items())]
            return [types.SimpleNamespace(**{f: r.get(f) for f in fields}) for r in rows]

        def delete(doctype, filters=None, *a, **k):
            site.calls.append(("db.delete", doctype, dict(filters or {})))
            assert doctype == "Dataset Measure", doctype
            site.dataset_measures = [r for r in site.dataset_measures
                                     if not all(r.get(key) == value for key, value in filters.items())]

        def get_doc(doctype, name, *a, **k):
            site.calls.append(("get_doc", doctype, name))
            assert doctype == "Measure" and name in site.measures, (doctype, name)
            return _Doc(site, doctype, name, **{k: v for k, v in site.measures[name].items() if k != "name"})

        def delete_doc(doctype, name, *a, **k):
            site.calls.append(("delete_doc", doctype, name, k))
            assert doctype == "Measure"
            if name not in site.measures:
                assert k.get("ignore_missing"), "a missing Measure raises without ignore_missing"
                return
            del site.measures[name]

        def logger(*a, **k):
            return types.SimpleNamespace(info=lambda message, *ia, **ik: site.logs.append(("info", message)),
                                         warning=lambda message, *wa, **wk: site.logs.append(("warning", message)))

        frappe.db = types.SimpleNamespace(table_exists=table_exists, exists=exists, delete=delete)
        frappe.get_all = get_all
        frappe.get_doc = get_doc
        frappe.delete_doc = delete_doc
        frappe.logger = logger
        return frappe


def _run(site):
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = site.module()
    try:
        assert os.path.exists(PATCH_PY), "no retire_variance_favorable_measure.py"
        spec = importlib.util.spec_from_file_location("_retire_vf_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        patch.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _measures_of(site, parent):
    return [r["measure"] for r in site.dataset_measures if r["parent"] == parent]


def test_removes_the_dataset_row_unpublishes_and_deletes_the_measure_and_logs_both():
    site = _Site()
    _run(site)
    assert _measures_of(site, DATASET) == ["variance_abs"]
    assert MEASURE not in site.measures and "variance_abs" in site.measures
    assert ("save", "Measure", MEASURE, "Inactive") in site.calls, "unpublished before the delete"
    deleted = [c for c in site.calls if c[0] == "delete_doc"]
    assert len(deleted) == 1 and deleted[0][2] == MEASURE and deleted[0][3].get("force") is True
    text = " ".join(message for _level, message in site.logs)
    assert MEASURE in text and DATASET in text
    assert not [m for level, m in site.logs if level == "warning"]


def test_a_site_dataset_that_names_the_measure_keeps_it_and_is_named():
    """konsol#230: the semantic model is site-owned. A site-made Dataset that
    reads the Measure is not edited; the Measure stays (it would break that
    Dataset's reads), and the log names the Dataset so the site decides."""
    site = _Site(other_datasets=("zz_site_dataset",))
    _run(site)
    assert _measures_of(site, DATASET) == ["variance_abs"], "the shipped Dataset's row still goes"
    assert _measures_of(site, "zz_site_dataset") == [MEASURE]
    assert MEASURE in site.measures and site.measures[MEASURE]["status"] == "Published"
    assert not [c for c in site.calls if c[0] in ("delete_doc", "save")]
    warnings = [m for level, m in site.logs if level == "warning"]
    assert warnings and "zz_site_dataset" in warnings[0] and MEASURE in warnings[0]


def test_a_second_run_and_a_site_without_the_measure_are_no_ops():
    site = _Site()
    _run(site)
    site.calls.clear()
    site.logs.clear()
    _run(site)
    assert not [c for c in site.calls if c[0] in ("delete_doc", "save", "db.delete")]
    assert MEASURE not in site.measures
    fresh = _Site(measure=False, dataset_row=False)
    _run(fresh)
    assert not [c for c in fresh.calls if c[0] in ("delete_doc", "save", "db.delete")]
    assert _measures_of(fresh, DATASET) == ["variance_abs"]


def test_a_dataset_row_without_the_measure_is_still_removed():
    """The row can outlive the Measure (a site deleted the Measure by hand):
    the row goes and nothing raises."""
    site = _Site(measure=False, dataset_row=True)
    _run(site)
    assert _measures_of(site, DATASET) == ["variance_abs"]
    assert not [c for c in site.calls if c[0] in ("delete_doc", "save")]


def test_without_the_measure_table_nothing_runs():
    site = _Site()
    site.tables = set()
    _run(site)
    assert [c for c in site.calls if c[0] != "table_exists"] == []
