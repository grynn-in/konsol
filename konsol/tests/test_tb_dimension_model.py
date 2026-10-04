"""Declared trial-balance dimensions (konsol/tb_dimension_model.py, konsol#255):
a file may carry a dim_* column only for a Dimension whose name is lower snake
case and which is Published and ticked in_trial_balance; any other dim_* header
is refused BY NAME so the reader knows whether to rename the Dimension, declare
it, publish it, or tick the flag."""
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


def test_a_mixed_case_dimension_name_is_refused_by_name_not_silently_matched():
    """The reproduction from konsol#255 row 9.

    Both parsers lowercase a header before validating it, so a Dimension named
    ``dim_Cost_Center`` could never match one — the admin was told to "create
    the Dimension dim_cost_center" while looking at the Published, ticked
    Dimension on screen. schema_apply refuses the same name for the same
    reason (it cannot make the column), so the rule now says so up front and
    names the Dimension whose name is wrong.
    """
    rows = [declared("dim_Cost_Center")]

    accepted = M.accepted_dimension_columns(rows)
    assert isinstance(accepted, frozenset)
    assert accepted == frozenset()

    problems = M.dimension_problems(["dim_cost_center"], rows)
    assert len(problems) == 1
    # It names the offender as stored, not the lower-cased header, because the
    # thing to go and fix is the Dimension record called dim_Cost_Center.
    assert "dim_Cost_Center" in problems[0]
    assert "rename" in problems[0].lower()


def test_a_lower_snake_dimension_name_is_still_accepted():
    rows = [
        declared("dim_cost_center"),
        declared("dim_product_line_2"),
        declared("dim_x"),
    ]
    assert M.accepted_dimension_columns(rows) == frozenset(
        {"dim_cost_center", "dim_product_line_2", "dim_x"}
    )
    assert M.dimension_problems(
        ["dim_cost_center", "dim_product_line_2", "dim_x"], rows
    ) == []


def test_a_padded_dimension_name_is_refused():
    """schema_apply fullmatches the raw value, so padding is illegal there too.

    Stripping it here would accept a name the column creation refuses, and the
    value would have nowhere to land.
    """
    for padded in (" dim_cost_center", "dim_cost_center ", "\tdim_cost_center\n"):
        rows = [declared(padded)]
        assert M.accepted_dimension_columns(rows) == frozenset()

        problems = M.dimension_problems(["dim_cost_center"], rows)
        assert len(problems) == 1
        assert "rename" in problems[0].lower()


def test_a_dimension_name_without_the_dim_prefix_is_accepted_by_nobody():
    for bad in ("cost_center", "dim", "dim_", "Dim_cost_center", "dim_cost-center"):
        assert M.accepted_dimension_columns([declared(bad)]) == frozenset()


def test_the_name_refusal_is_distinguishable_from_the_other_three():
    """Four reasons, four different fixes: rename / declare / publish / tick."""
    rows = [
        declared("dim_Cost_Center"),
        declared("dim_project", in_trial_balance=0),
        declared("dim_product", status="Draft"),
    ]
    headers = ["dim_cost_center", "dim_project", "dim_product", "dim_widget"]
    problems = M.dimension_problems(headers, rows)
    assert len(problems) == 4
    said = dict(zip(headers, problems))

    rename = said["dim_cost_center"]
    assert "rename" in rename.lower()
    assert "not declared" not in rename.lower()
    assert "not published" not in rename.lower()
    assert "in_trial_balance" not in rename

    assert "not declared" in said["dim_widget"].lower()
    assert "rename" not in said["dim_widget"].lower()
    assert "not published" in said["dim_product"].lower()
    assert "rename" not in said["dim_product"].lower()
    assert "in_trial_balance" in said["dim_project"]
    assert "rename" not in said["dim_project"].lower()


def test_a_legal_dimension_is_not_collapsed_onto_an_illegal_twin():
    """Two Dimensions must never share one column.

    Lowercasing dim_Cost_Center would have merged it into dim_cost_center and
    put two dimensions' values in one place — a silent-data bug worse than the
    one being fixed. The legal one is accepted; the illegal one stays refused.
    """
    rows = [declared("dim_cost_center"), declared("dim_Cost_Center")]
    assert M.accepted_dimension_columns(rows) == frozenset({"dim_cost_center"})
    assert M.dimension_problems(["dim_cost_center"], rows) == []


def test_an_illegal_name_is_refused_for_its_name_before_its_status():
    """A Draft dimension with an illegal name cannot be published out of it."""
    rows = [declared("dim_Cost_Center", status="Draft", in_trial_balance=0)]
    problems = M.dimension_problems(["dim_cost_center"], rows)
    assert len(problems) == 1
    assert "rename" in problems[0].lower()
    assert "not published" not in problems[0].lower()


def test_a_falsy_in_trial_balance_of_any_shape_is_off():
    for off in (0, "0", "", None, False):
        rows = [declared("dim_project", in_trial_balance=off)]
        assert M.accepted_dimension_columns(rows) == frozenset()
        assert len(M.dimension_problems(["dim_project"], rows)) == 1


def test_a_duplicated_bad_header_is_reported_once_per_column():
    rows = [declared("dim_project", in_trial_balance=0)]
    problems = M.dimension_problems(["dim_project", "dim_project"], rows)
    assert len(problems) == 1


# -- konsolidat#245: the flag reader is shared, so make it public ----------------------

def test_is_flag_on_is_public_and_handles_the_text_off_values():
    """konsolidat#245 option D adds a second flag (Dimension.in_journal) read by
    konsol#305's close app. Both readers need the same "0" handling — a Check
    arriving as the string "0" through JSON, CSV or REST is truthy in Python —
    so the helper is public rather than imported as _is_on across modules."""
    assert M.is_flag_on(1) is True
    assert M.is_flag_on(True) is True
    assert M.is_flag_on("1") is True
    assert M.is_flag_on("yes") is True
    for off in (0, False, None, "", "0", "false", "False", "no", " 0 ", "NO"):
        assert M.is_flag_on(off) is False, off


def test_the_private_alias_still_resolves_to_the_public_helper():
    """Nothing should import _is_on now, but anything that still does must get
    the same function, not a divergent copy."""
    assert M._is_on is M.is_flag_on


def test_the_three_copies_of_the_column_name_rule_are_identical():
    """PR #324 re-review, finding 7. The pattern is hand-copied into three
    modules — here, schema_apply._SAFE_TB_DIM_COLUMN and
    journal_model._LEGAL_DIM_COLUMN — because the other two import frappe or
    would make a cycle. Each docstring says it MUST stay identical and nothing
    enforced it. A drift is silent in the worst direction: one layer accepts a
    name the next refuses, and values land nowhere."""
    import os
    import re as _re
    app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = {}
    for rel, const in (("schema_apply.py", "_SAFE_TB_DIM_COLUMN"),
                       (os.path.join("close", "journal_model.py"), "_LEGAL_DIM_COLUMN")):
        with open(os.path.join(app, rel)) as fh:
            src = fh.read()
        m = _re.search(const + r' = re\.compile\(r"([^"]+)"\)', src)
        assert m, f"{const} not found in {rel} in the expected shape"
        found[rel] = m.group(1)
    assert set(found.values()) == {M._LEGAL_DIMENSION_NAME.pattern}, found
