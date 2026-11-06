"""konsol#287 decision 2A: one generic ``Dimension Value`` replaces Budget Cost Center.

Pure tests over ``konsol.dimension_value_model``: naming, the declared set, the
warehouse query, list filtering, the write-gate rule and the retirement plan
for the patch. No frappe, no ClickHouse.
"""
import ast
import json
import os

import pytest

from konsol import dimension_value_model as dvm

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "epm", "doctype")


# --- naming --------------------------------------------------------------------

def test_name_is_dimension_colon_value():
    assert dvm.make_name("dim_a", "V1") == "dim_a:V1"


def test_parse_splits_on_the_first_colon_only():
    assert dvm.parse_name("dim_a:V1") == ("dim_a", "V1")
    # A value may itself carry a colon; a dimension name cannot.
    assert dvm.parse_name("dim_a:X:Y") == ("dim_a", "X:Y")


@pytest.mark.parametrize("name", ["", None, "V1", "dim_a", ":V1", "dim_a:"])
def test_parse_refuses_a_name_without_both_halves(name):
    assert dvm.parse_name(name) is None


def test_resolve_refuses_an_undeclared_dimension():
    declared = {"dim_a"}
    assert dvm.resolve_name("dim_a:V1", declared) == ("dim_a", "V1")
    assert dvm.resolve_name("dim_b:V1", declared) is None
    assert dvm.resolve_name("V1", declared) is None


# --- the declared set ------------------------------------------------------------

def test_declared_is_published_and_budget_or_trial_balance():
    rows = [
        {"name": "dim_a", "status": "Published", "in_budget": 1, "in_trial_balance": 0},
        {"name": "dim_b", "status": "Published", "in_budget": 0, "in_trial_balance": 1},
        {"name": "dim_c", "status": "Published", "in_budget": 0, "in_trial_balance": 0},
        {"name": "dim_d", "status": "Draft", "in_budget": 1, "in_trial_balance": 1},
        {"name": "dim_e", "status": "Inactive", "in_budget": 1, "in_trial_balance": 0},
    ]
    assert dvm.declared_dimensions(rows) == ["dim_a", "dim_b"]


# --- the warehouse query ----------------------------------------------------------

def test_sources_are_the_tables_that_carry_the_column():
    cols = {
        "epm_gold.gold_trial_balance": {"dim_a", "dim_b", "account"},
        "epm_gold.budget_annual_input": {"dim_a"},
        "epm_gold.budget_monthly_input": set(),
    }
    assert dvm.value_sources(["dim_a", "dim_b", "dim_c"], cols) == [
        ("dim_a", "epm_gold.budget_annual_input"),
        ("dim_a", "epm_gold.gold_trial_balance"),
        ("dim_b", "epm_gold.gold_trial_balance"),
    ]


def test_sources_never_include_an_unsafe_identifier():
    cols = {"epm_gold.gold_trial_balance": {"dim_a;drop", "Dim_A"}}
    assert dvm.value_sources(["dim_a;drop", "Dim_A"], cols) == []


def test_values_sql_unions_every_source_and_drops_blanks():
    sql = dvm.values_sql([("dim_a", "epm_gold.gold_trial_balance"),
                          ("dim_b", "epm_gold.budget_annual_input")])
    assert "SELECT 'dim_a' AS dimension, toString(dim_a) AS value FROM epm_gold.gold_trial_balance" in sql
    assert "SELECT 'dim_b' AS dimension, toString(dim_b) AS value FROM epm_gold.budget_annual_input" in sql
    assert "UNION ALL" in sql
    assert "value != ''" in sql
    assert sql.rstrip().endswith("FORMAT JSONCompactEachRow")


def test_values_sql_is_none_without_sources():
    assert dvm.values_sql([]) is None


def test_value_tables_are_the_warehouse_not_the_d365_silver_table():
    assert "epm_silver.silver_financial_dimensions" not in dvm.VALUE_TABLES
    assert "epm_gold.gold_trial_balance" in dvm.VALUE_TABLES
    assert "epm_gold.budget_annual_input" in dvm.VALUE_TABLES
    assert "epm_gold.budget_monthly_input" in dvm.VALUE_TABLES


def test_parse_rows_builds_named_rows_sorted_and_distinct():
    raw = '["dim_b","X"]\n["dim_a","V2"]\n["dim_a","V1"]\n["dim_a","V1"]\n'
    assert dvm.parse_rows(raw) == [
        {"name": "dim_a:V1", "dimension": "dim_a", "value": "V1"},
        {"name": "dim_a:V2", "dimension": "dim_a", "value": "V2"},
        {"name": "dim_b:X", "dimension": "dim_b", "value": "X"},
    ]


# --- list filtering -----------------------------------------------------------------

ROWS = [
    {"name": "dim_a:V1", "dimension": "dim_a", "value": "V1"},
    {"name": "dim_a:V2", "dimension": "dim_a", "value": "V2"},
    {"name": "dim_b:V1", "dimension": "dim_b", "value": "V1"},
]


def test_filter_by_dimension_equality_list_and_dict_forms():
    names = lambda rs: [r["name"] for r in rs]  # noqa: E731
    assert names(dvm.filter_rows(ROWS, filters=[["Dimension Value", "dimension", "=", "dim_a"]])) == ["dim_a:V1", "dim_a:V2"]
    assert names(dvm.filter_rows(ROWS, filters=[["dimension", "=", "dim_b"]])) == ["dim_b:V1"]
    assert names(dvm.filter_rows(ROWS, filters={"dimension": "dim_b"})) == ["dim_b:V1"]
    assert names(dvm.filter_rows(ROWS, filters={"value": ["like", "%2%"]})) == ["dim_a:V2"]


def test_or_filters_match_any_like_the_link_search_sends():
    out = dvm.filter_rows(ROWS, or_filters=[["Dimension Value", "name", "like", "%b:%"],
                                            ["Dimension Value", "value", "like", "%v2%"]])
    assert [r["name"] for r in out] == ["dim_a:V2", "dim_b:V1"]


def test_txt_searches_the_name():
    assert [r["name"] for r in dvm.filter_rows(ROWS, txt="b:v")] == ["dim_b:V1"]


def test_in_and_not_equal():
    assert [r["name"] for r in dvm.filter_rows(ROWS, filters=[["name", "in", ["dim_a:V1", "dim_b:V1"]]])] == ["dim_a:V1", "dim_b:V1"]
    assert [r["name"] for r in dvm.filter_rows(ROWS, filters=[["dimension", "!=", "dim_a"]])] == ["dim_b:V1"]


def test_an_unknown_operator_or_field_is_refused_not_ignored():
    with pytest.raises(ValueError):
        dvm.filter_rows(ROWS, filters=[["dimension", "between", ["a", "b"]]])
    with pytest.raises(ValueError):
        dvm.filter_rows(ROWS, filters=[["owner", "=", "x"]])


def test_page_and_shape():
    assert [r["name"] for r in dvm.page(ROWS, start=1, page_length=1)] == ["dim_a:V2"]
    assert dvm.page(ROWS, start=0, page_length=0) == ROWS
    fields = ["`tabDimension Value`.`name`", "value as label", {"IFNULL": [1, 2], "as": "_relevance"}]
    assert dvm.as_list(ROWS[:1], fields) == [["dim_a:V1", "V1", None]]


# --- the budget write gate ------------------------------------------------------------

def test_dimension_value_grant_allows_only_its_own_dimension():
    granted = {"dim_a:V1", "dim_b:V2", "V3", "dim_a:X:Y"}
    assert dvm.allowed_values("dim_a", "Dimension Value", granted) == {"V1", "X:Y"}
    assert dvm.allowed_values("dim_b", "Dimension Value", granted) == {"V2"}
    # A permission for another dimension never allows: dim_b's V2 is not dim_a's.
    assert "V2" not in dvm.allowed_values("dim_a", "Dimension Value", granted)


def test_other_permission_doctypes_are_unchanged():
    granted = {"V1", "dim_a:V2"}
    assert dvm.allowed_values("dim_a", "Some Other DocType", granted) == granted


def test_unrestricted_stays_unrestricted():
    assert dvm.allowed_values("dim_a", "Dimension Value", None) is None


def test_api_dimension_gate_uses_the_model():
    with open(os.path.join(APP_DIR, "api.py")) as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_assert_dimension_access")
    called = {n.func.attr for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "allowed_values" in called


# --- retiring Budget Cost Center ----------------------------------------------------------

def _dim(name, permission_doctype=None):
    return {"name": name, "permission_doctype": permission_doctype}


def _up(name, user, value, applicable_for=None):
    return {"name": name, "user": user, "for_value": value,
            "applicable_for": applicable_for}


def test_target_is_the_dimension_that_pointed_at_the_retired_doctype():
    dims = [_dim("dim_a", "Budget Cost Center"), _dim("dim_b")]
    plan = dvm.plan_retirement(dims, [_up("UP1", "u@x", "V1")], [])
    assert plan["targets"] == ["dim_a"]
    assert plan["repoint"] == ["dim_a"]
    assert plan["rewrite"] == [("UP1", "dim_a:V1")]
    assert plan["add"] == []
    assert plan["remove"] == []
    assert plan["unmapped"] == []


def test_every_pointing_dimension_gets_the_grant():
    """The old gate compared a grant with every Dimension that pointed at
    Budget Cost Center, so V was allowed on all of them. One row becomes one
    row per pointing dimension: the old row is rewritten to the first, copies
    are added for the rest."""
    dims = [_dim("dim_b", "Budget Cost Center"), _dim("dim_a", "Budget Cost Center"), _dim("dim_c")]
    plan = dvm.plan_retirement(dims, [_up("UP1", "u@x", "V1"), _up("UP2", "v@x", "V2")], [])
    assert plan["targets"] == ["dim_a", "dim_b"]
    assert plan["repoint"] == ["dim_a", "dim_b"]
    assert plan["rewrite"] == [("UP1", "dim_a:V1"), ("UP2", "dim_a:V2")]
    assert plan["add"] == [("UP1", "dim_b:V1"), ("UP2", "dim_b:V2")]
    assert plan["unmapped"] == []
    assert plan["remove"] == []


@pytest.mark.parametrize("dims", [[], [_dim("dim_a"), _dim("dim_b")]])
def test_no_pointing_dimension_reports_every_row_and_guesses_nothing(dims):
    plan = dvm.plan_retirement(dims, [_up("UP1", "u@x", "V1"), _up("UP2", "v@x", "V2")], [])
    assert plan["targets"] == []
    assert plan["rewrite"] == [] and plan["add"] == []
    assert [u for u, _ in plan["unmapped"]] == ["UP1", "UP2"]
    assert all(reason for _, reason in plan["unmapped"])
    assert plan["repoint"] == []


def test_unmapped_rows_are_removed():
    """A row left on a deleted DocType is debris; the patch records it in full
    and deletes it, so the plan lists it for removal."""
    plan = dvm.plan_retirement([], [_up("UP1", "u@x", "V1")], [])
    assert plan["remove"] == ["UP1"]


def test_blank_value_is_reported_and_removed():
    plan = dvm.plan_retirement([_dim("dim_a", "Budget Cost Center")], [_up("UP1", "u@x", "")], [])
    assert plan["rewrite"] == []
    assert [u for u, _ in plan["unmapped"]] == ["UP1"]
    assert plan["remove"] == ["UP1"]


def test_a_grant_that_already_exists_on_dimension_value_is_a_duplicate():
    existing = [{"user": "u@x", "for_value": "dim_a:V1", "applicable_for": None}]
    plan = dvm.plan_retirement([_dim("dim_a", "Budget Cost Center")],
                               [_up("UP1", "u@x", "V1"), _up("UP2", "u@x", "V2")], existing)
    assert plan["duplicate"] == [("UP1", "dim_a:V1")]
    assert plan["rewrite"] == [("UP2", "dim_a:V2")]
    # UP1 maps to nothing new, so the old row goes.
    assert plan["remove"] == ["UP1"]


def test_duplicate_on_one_of_several_dimensions_rewrites_to_the_next():
    existing = [{"user": "u@x", "for_value": "dim_a:V1", "applicable_for": None}]
    dims = [_dim("dim_a", "Budget Cost Center"), _dim("dim_b", "Budget Cost Center"),
            _dim("dim_c", "Budget Cost Center")]
    plan = dvm.plan_retirement(dims, [_up("UP1", "u@x", "V1")], existing)
    assert plan["duplicate"] == [("UP1", "dim_a:V1")]
    assert plan["rewrite"] == [("UP1", "dim_b:V1")]
    assert plan["add"] == [("UP1", "dim_c:V1")]
    assert plan["remove"] == []


def test_patch_deletes_unmapped_rows_after_recording_them_in_full():
    src = open(os.path.join(APP_DIR, "patches", "adopt_dimension_value.py")).read()
    for field in ("user", "allow", "for_value", "applicable_for",
                  "apply_to_all_doctypes", "is_default"):
        assert f'"{field}"' in src, f"unmapped record must carry {field}"
    assert "log_error" in src
    assert 'plan["remove"]' in src
    assert 'plan["add"]' in src


# --- the doctype -----------------------------------------------------------------------------

def test_dimension_value_doctype_is_virtual_and_read_only():
    meta = json.load(open(os.path.join(DOCTYPE_DIR, "dimension_value", "dimension_value.json")))
    assert meta["name"] == "Dimension Value"
    assert meta["is_virtual"] == 1
    assert meta["module"] == "EPM"
    fields = {f["fieldname"]: f for f in meta["fields"]}
    assert fields["dimension"]["fieldtype"] == "Link"
    assert fields["dimension"]["options"] == "Dimension"
    assert all(f.get("read_only") == 1 for f in fields.values()
               if f["fieldtype"] not in ("Section Break", "Column Break"))
    assert all(not p.get(k) for p in meta["permissions"] for k in ("write", "create", "delete"))


def test_controller_refuses_a_name_it_cannot_resolve():
    src = open(os.path.join(DOCTYPE_DIR, "dimension_value", "dimension_value.py")).read()
    assert "DoesNotExistError" in src
    assert "resolve_name" in src
    assert "readonly_guard" in src


def _controller_method(name):
    with open(os.path.join(DOCTYPE_DIR, "dimension_value", "dimension_value.py")) as fh:
        tree = ast.parse(fh.read())
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "DimensionValue")
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


@pytest.mark.parametrize("method", ["get_list", "get_count"])
def test_list_and_count_check_read_permission(method):
    """reportview calls a virtual controller's get_list/get_count with no read
    check of its own, so the controller must make it."""
    with open(os.path.join(DOCTYPE_DIR, "dimension_value", "dimension_value.py")) as fh:
        src = fh.read()
    assert 'frappe.has_permission(dvm.DOCTYPE, "read", throw=True)' in src
    called = {n.func.id for n in ast.walk(_controller_method(method))
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_assert_read" in called or "_listed" in called
    listed = open(os.path.join(DOCTYPE_DIR, "dimension_value", "dimension_value.py")).read()
    body = listed[listed.index("def _listed"):listed.index("class DimensionValue")]
    assert "_assert_read()" in body


def test_pluck_returns_one_field():
    assert dvm.pluck(ROWS[:2], "value") == ["V1", "V2"]
    assert dvm.pluck(ROWS[:1], "`tabDimension Value`.`name`") == ["dim_a:V1"]
    called = {n.func.attr for n in ast.walk(_controller_method("get_list"))
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "pluck" in called


def test_not_found_is_raised_with_only_the_keywords_frappe_accepts():
    """frappe.DoesNotExistError(*args, doctype=None): a ``name=`` keyword is a
    TypeError, which the live A/B hit before this test existed."""
    with open(os.path.join(DOCTYPE_DIR, "dimension_value", "dimension_value.py")) as fh:
        tree = ast.parse(fh.read())
    raises = [n.exc for n in ast.walk(tree) if isinstance(n, ast.Raise)
              and isinstance(n.exc, ast.Call)
              and getattr(n.exc.func, "attr", None) == "DoesNotExistError"]
    assert raises
    for call in raises:
        assert {k.arg for k in call.keywords} <= {"doctype"}


def test_budget_cost_center_is_gone():
    assert not os.path.exists(os.path.join(DOCTYPE_DIR, "budget_cost_center"))


def test_retirement_patch_is_registered():
    with open(os.path.join(APP_DIR, "patches.txt")) as fh:
        assert "konsol.patches.adopt_dimension_value" in fh.read().split()
