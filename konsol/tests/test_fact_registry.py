"""Structural tests for the Fact Registry (Phase 2.3).

These parse the doctype JSON / controller / fixtures / schema_apply source
without a live Frappe site, mirroring test_config_doctypes.py.
"""
import ast
import contextlib
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET_CONTROLLER = os.path.join(APP_DIR, "epm", "doctype", "dataset", "dataset.py")


def _doctype_json(name):
    path = os.path.join(APP_DIR, "epm", "doctype", name, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def _read(path):
    with open(path) as f:
        return f.read()


def _fixture(name):
    with open(os.path.join(APP_DIR, "fixtures", name)) as f:
        return json.load(f)


def _field_names(meta):
    return [f["fieldname"] for f in meta["fields"]]


# --- Dataset doctype ---

def test_dataset_has_new_fields():
    fields = _field_names(_doctype_json("dataset"))
    for f in ["grain", "refresh_frequency", "generates_source", "extra_columns",
              "status", "fact_measures", "fact_dimensions"]:
        assert f in fields, f"Missing field: {f}"


def test_dataset_measures_json_is_hidden_synced():
    """The legacy `measures` JSON stays for backward compat but is hidden/read-only."""
    meta = _doctype_json("dataset")
    measures = next(f for f in meta["fields"] if f["fieldname"] == "measures")
    assert measures.get("read_only") == 1
    assert measures.get("hidden") == 1


def test_dataset_status_options():
    meta = _doctype_json("dataset")
    status = next(f for f in meta["fields"] if f["fieldname"] == "status")
    assert status["options"] == "Draft\nPublished\nInactive"


def test_dataset_has_epm_admin_permission():
    meta = _doctype_json("dataset")
    roles = {p["role"] for p in meta["permissions"]}
    assert "EPM Admin" in roles


def test_fact_measures_child_doctype():
    meta = _doctype_json("dataset_measure")
    assert meta["istable"] == 1
    fields = _field_names(meta)
    assert "measure" in fields and "required" in fields


def test_fact_dimension_child_has_required():
    fields = _field_names(_doctype_json("dataset_dimension"))
    assert "required" in fields


# --- Controller ---

def test_controller_publish_unpublish_and_lifecycle():
    content = _read(os.path.join(APP_DIR, "epm", "doctype", "dataset", "dataset.py"))
    assert "schema_lifecycle" in content
    tree = ast.parse(content)
    methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    for m in ["publish", "unpublish", "_validate_measures", "_validate_dimensions",
              "_validate_extra_columns", "_sync_json_fields"]:
        assert m in methods, f"Missing method: {m}"


def test_controller_no_on_update():
    content = _read(os.path.join(APP_DIR, "epm", "doctype", "dataset", "dataset.py"))
    tree = ast.parse(content)
    methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "on_update" not in methods


# --- schema_apply generation ---

def test_schema_apply_has_fact_generation():
    content = _read(os.path.join(APP_DIR, "schema_apply.py"))
    assert "_apply_fact_tables" in content
    assert "_upsert_dbt_source" in content
    assert "facts_created" in content
    assert "sources_written" in content
    assert "CREATE TABLE IF NOT EXISTS" in content


# --- Fixture integrity (guards validation-vs-fixtures consistency) ---

def test_every_fact_measure_is_registered_published():
    published = {
        m["measure_name"] for m in _fixture("measure.json")
        if m.get("status") == "Published"
    }
    for fact in _fixture("dataset.json"):
        for row in fact.get("fact_measures", []):
            assert row["measure"] in published, (
                f"{fact['fact_name']} references unregistered measure {row['measure']}"
            )


def test_every_fact_dimension_is_registered_published():
    published = {
        d["dimension_name"] for d in _fixture("dimension.json")
        if d.get("status") == "Published"
    }
    for fact in _fixture("dataset.json"):
        for row in fact.get("fact_dimensions", []):
            assert row["dimension"] in published, (
                f"{fact['fact_name']} references unregistered dimension {row['dimension']}"
            )


def test_fact_fixtures_use_child_table_and_published():
    for fact in _fixture("dataset.json"):
        assert fact.get("status") == "Published", f"{fact['fact_name']} not Published"
        assert "fact_measures" in fact, f"{fact['fact_name']} missing fact_measures child"


def test_generates_source_facts_target_staging_schema():
    for fact in _fixture("dataset.json"):
        if fact.get("generates_source"):
            assert fact["clickhouse_table"].startswith("epm_staging."), (
                f"{fact['fact_name']} write-back fact must live in epm_staging"
            )


def test_extra_columns_is_valid_json_when_present():
    for fact in _fixture("dataset.json"):
        ec = fact.get("extra_columns")
        if ec:
            parsed = json.loads(ec)
            assert isinstance(parsed, list)
            for col in parsed:
                assert "name" in col


# --- Default measure lives on the Dataset (konsol#105 Decision 1) ---

def _dataset_field(fieldname):
    meta = _doctype_json("dataset")
    return next(f for f in meta["fields"] if f["fieldname"] == fieldname)


def test_dataset_declares_a_default_measure_link():
    """The Dataset — not api.py, not a per-scenario dict — declares the measure
    a read uses when it names none."""
    fields = _field_names(_doctype_json("dataset"))
    assert "default_measure" in fields, "Dataset declares no default_measure field"
    field = _dataset_field("default_measure")
    assert field["fieldtype"] == "Link"
    assert field["options"] == "Measure"
    # Not mandatory: a Dataset may decline to declare one, and a read that
    # names no measure against it is then an error, not a guess.
    assert not field.get("reqd"), "default_measure must not be reqd"
    assert field.get("description"), "default_measure needs a description"


def test_default_measure_sits_in_the_measures_section():
    """It belongs beside the measures it must be one of, not in some other tab."""
    fields = _field_names(_doctype_json("dataset"))
    idx = fields.index("default_measure")
    assert idx == fields.index("measures") + 1
    assert idx < fields.index("col_break_2")
    assert fields.index("section_measures_dims") < idx


def _dataset_validate_calls():
    """The methods validate() calls, in order."""
    tree = ast.parse(_read(DATASET_CONTROLLER))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "Dataset")
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == "validate")
    return [n.func.attr for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]


def test_validate_checks_the_default_measure():
    calls = _dataset_validate_calls()
    assert "_validate_default_measure" in calls, (
        "validate() does not call _validate_default_measure"
    )
    assert calls.index("_validate_default_measure") > calls.index("_validate_measures")


class DatasetThrown(Exception):
    """frappe.throw was called by the Dataset controller."""


@contextlib.contextmanager
def _dataset_module():
    """The Dataset controller loaded against a stub frappe, as
    test_fiscal_year_controller.py does, so validate() can be run on a host."""
    class Document:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def __getattr__(self, name):   # an unset field reads as None, as in Frappe
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, name, default=None):
            return self.__dict__.get(name, default)

    def throw(msg, exc=None, *args, **kwargs):
        raise (exc or DatasetThrown)(msg)

    mods = {name: types.ModuleType(name) for name in (
        "frappe", "frappe.model", "frappe.model.document",
        "konsol", "konsol.schema_lifecycle")}
    frappe = mods["frappe"]
    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.flags = types.SimpleNamespace(
        in_install=False, in_migrate=False, in_import=False)
    frappe.db = types.SimpleNamespace(
        get_value=lambda *a, **k: "Published")
    frappe.whitelist = lambda *a, **k: (
        a[0] if a and callable(a[0]) and not k else (lambda fn: fn))
    mods["frappe.model.document"].Document = Document
    mods["konsol"].__path__ = []
    lifecycle = mods["konsol.schema_lifecycle"]
    lifecycle.apply_and_rebuild = lambda *a, **k: None
    lifecycle.check_epm_admin = lambda *a, **k: None
    mods["konsol"].schema_lifecycle = lifecycle

    saved = {name: sys.modules.get(name) for name in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location(
            "dataset_under_test", DATASET_CONTROLLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.frappe_stub = frappe
        yield module
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def _dataset_doc(module, default_measure, measures=("driver_value", "headcount_fte")):
    return module.Dataset(
        doctype="Dataset",
        name="zz_driver_fact",
        fact_name="zz_driver_fact",
        clickhouse_table="epm_staging.zz_driver_fact",
        default_measure=default_measure,
        fact_measures=[types.SimpleNamespace(measure=m, required=0) for m in measures],
        fact_dimensions=[],
    )


def _thrown(fn):
    """The message of the one throw, or None when it passes."""
    try:
        fn()
    except DatasetThrown as exc:
        return str(exc)
    return None


def test_default_measure_may_be_one_of_this_dataset_s_measures():
    with _dataset_module() as module:
        doc = _dataset_doc(module, "driver_value")
        assert _thrown(doc._validate_default_measure) is None


def test_default_measure_may_be_blank():
    """Declining to declare one is allowed here; the read is what errors."""
    with _dataset_module() as module:
        for blank in ("", None):
            doc = _dataset_doc(module, blank)
            assert _thrown(doc._validate_default_measure) is None


def test_default_measure_must_be_a_measure_this_dataset_has():
    with _dataset_module() as module:
        doc = _dataset_doc(module, "period_net_amount")
        msg = _thrown(doc._validate_default_measure)
        assert msg, "a foreign default measure was accepted"
        assert "zz_driver_fact" in msg, msg          # names the dataset
        assert "period_net_amount" in msg, msg       # names the offending measure
        assert "driver_value" in msg, msg            # lists what it does have
        assert "headcount_fte" in msg, msg


def test_default_measure_is_checked_during_migrate_too():
    """Unlike _validate_measures, this check reads only this doc's own child
    rows, so there is no load-order reason to skip it under the flags — and a
    fixture re-import must not be able to install an impossible default."""
    with _dataset_module() as module:
        for flag in ("in_install", "in_migrate", "in_import"):
            doc = _dataset_doc(module, "period_net_amount")
            setattr(module.frappe.flags, flag, True)
            try:
                msg = _thrown(doc.validate)
            finally:
                setattr(module.frappe.flags, flag, False)
            assert msg and "period_net_amount" in msg, (
                f"default measure not checked under {flag}: {msg}"
            )


# --- The shipped datasets declare theirs (konsol#105 Decision 1, row M2) ---

SHIPPED_DEFAULT_MEASURES = {
    "gl_journal_entries": "period_net_amount",
    "budget_input": "period_amount",
    "variance_analysis": "variance_abs",
    "cashflow": "cash_flow_amount",
    "consolidated": "consolidated_amount",
}


def test_every_shipped_dataset_declares_a_default_measure():
    """Holds for datasets added later, not only today's five: a shipped Dataset
    that declares none turns every read naming no measure into an error, so the
    fixture must never ship one blank."""
    missing = [
        fact["fact_name"] for fact in _fixture("dataset.json")
        if not fact.get("default_measure")
    ]
    assert not missing, f"shipped datasets declare no default_measure: {missing}"


def test_every_shipped_default_measure_is_one_the_dataset_has():
    """The rule _validate_default_measure enforces at save time, checked here on
    the fixture so an impossible default cannot reach a migrate."""
    for fact in _fixture("dataset.json"):
        own = [row["measure"] for row in fact.get("fact_measures", [])]
        assert fact.get("default_measure") in own, (
            f"{fact['fact_name']} defaults to {fact.get('default_measure')!r}, "
            f"which is not one of its own measures {own}"
        )


def test_the_shipped_defaults_are_the_agreed_values():
    """Pinned per dataset — a tenth dataset may be added freely, but silently
    re-tagging one of these nine is caught."""
    actual = {
        fact["fact_name"]: fact.get("default_measure")
        for fact in _fixture("dataset.json")
    }
    for name, measure in SHIPPED_DEFAULT_MEASURES.items():
        assert name in actual, f"shipped dataset {name} is gone from the fixture"
        assert actual[name] == measure, (
            f"{name} defaults to {actual[name]!r}, the agreed value is {measure!r}"
        )
