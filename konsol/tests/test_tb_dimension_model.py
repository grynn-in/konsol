"""Declared trial-balance dimensions (konsol/tb_dimension_model.py, konsol#255):
a file may carry a dim_* column only for a Dimension that is Published and
ticked in_trial_balance, and any other dim_* header is refused BY NAME so the
reader knows whether to declare it, publish it, or tick the flag."""
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "tb_dimension_model", os.path.join(APP_DIR, "tb_dimension_model.py")
)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def declared(name, status="Published", in_trial_balance=1):
    return {
        "dimension_name": name,
        "status": status,
        "in_trial_balance": in_trial_balance,
    }


def test_a_published_dimension_in_the_trial_balance_is_accepted():
    rows = [declared("dim_cost_center"), declared("dim_department")]
    assert M.accepted_dimension_columns(rows) == frozenset(
        {"dim_cost_center", "dim_department"}
    )
    assert M.dimension_problems(["account", "dim_cost_center"], rows) == []


def test_a_published_dimension_off_the_trial_balance_is_not_accepted():
    rows = [declared("dim_project", in_trial_balance=0)]
    assert M.accepted_dimension_columns(rows) == frozenset()

    problems = M.dimension_problems(["dim_project"], rows)
    assert len(problems) == 1
    assert "dim_project" in problems[0]
    joined = problems[0].lower()
    assert "in_trial_balance" in joined
    assert "not published" not in joined
    assert "not declared" not in joined


def test_a_dimension_that_is_not_published_is_refused_as_not_published():
    for status in ("Draft", "Inactive"):
        rows = [declared("dim_cost_center", status=status)]
        assert M.accepted_dimension_columns(rows) == frozenset()

        problems = M.dimension_problems(["dim_cost_center"], rows)
        assert len(problems) == 1
        assert "dim_cost_center" in problems[0]
        assert status in problems[0]
        assert "not published" in problems[0].lower()
        assert "in_trial_balance" not in problems[0]


def test_an_undeclared_dim_header_is_refused_as_undeclared():
    rows = [declared("dim_cost_center")]
    problems = M.dimension_problems(["dim_widget"], rows)
    assert len(problems) == 1
    assert "dim_widget" in problems[0]
    assert "not declared" in problems[0].lower()


def test_every_bad_header_is_reported_not_just_the_first():
    rows = [
        declared("dim_cost_center"),
        declared("dim_project", in_trial_balance=0),
        declared("dim_product", status="Draft"),
    ]
    headers = [
        "account",
        "dim_cost_center",
        "dim_project",
        "dim_product",
        "dim_widget",
    ]
    problems = M.dimension_problems(headers, rows)
    assert len(problems) == 3
    named = " | ".join(problems)
    assert "dim_project" in named
    assert "dim_product" in named
    assert "dim_widget" in named
    assert "dim_cost_center" not in named


def test_a_non_dim_header_is_none_of_this_functions_business():
    rows = [declared("dim_cost_center")]
    headers = ["account", "amount", "cost_center", "dimension", "dim", "dimx"]
    assert M.dimension_problems(headers, rows) == []


def test_a_blank_header_is_not_a_column():
    rows = [declared("dim_cost_center")]
    assert M.dimension_problems(["", "   ", None, "dim_cost_center"], rows) == []


def test_no_declared_dimensions_accepts_nothing_and_refuses_every_dim_header():
    assert M.accepted_dimension_columns([]) == frozenset()

    problems = M.dimension_problems(["account", "dim_cost_center"], [])
    assert len(problems) == 1
    assert "dim_cost_center" in problems[0]
    assert "not declared" in problems[0].lower()


def test_the_accepted_set_is_a_frozenset_of_verbatim_names():
    rows = [declared("dim_Cost_Center")]
    accepted = M.accepted_dimension_columns(rows)
    assert isinstance(accepted, frozenset)
    assert accepted == frozenset({"dim_Cost_Center"})


def test_a_falsy_in_trial_balance_of_any_shape_is_off():
    for off in (0, "0", "", None, False):
        rows = [declared("dim_project", in_trial_balance=off)]
        assert M.accepted_dimension_columns(rows) == frozenset()
        assert len(M.dimension_problems(["dim_project"], rows)) == 1


def test_a_duplicated_bad_header_is_reported_once_per_column():
    rows = [declared("dim_project", in_trial_balance=0)]
    problems = M.dimension_problems(["dim_project", "dim_project"], rows)
    assert len(problems) == 1
