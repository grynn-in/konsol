"""konsol#305 X01 — one ClickHouse read helper for the close app.

``konsol/close/ch_read.py`` is pure like fiscal_status_model.py: loaded by
path (test_fiscal_status_model.py:1-12), no frappe or konsol import at
module level. ``rows`` lazily imports ``konsol.clickhouse.execute`` inside
the call, so each test installs a fake ``konsol`` / ``konsol.clickhouse``
module in ``sys.modules`` around the call and restores whatever was there
before.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_READ_PY = os.path.join(APP_DIR, "close", "ch_read.py")

_spec = importlib.util.spec_from_file_location("ch_read_under_test", CH_READ_PY)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _with_fake_clickhouse(fake_execute, fn):
    """Install a fake konsol.clickhouse.execute, run fn(), restore sys.modules."""
    konsol_mod = types.ModuleType("konsol")
    clickhouse_mod = types.ModuleType("konsol.clickhouse")
    clickhouse_mod.execute = fake_execute
    saved = {n: sys.modules.get(n) for n in ("konsol", "konsol.clickhouse")}
    sys.modules["konsol"] = konsol_mod
    sys.modules["konsol.clickhouse"] = clickhouse_mod
    try:
        return fn()
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def test_rows_binds_params_and_formats_json():
    calls = []

    def fake_execute(sql, params=None):
        calls.append((sql, params))
        return '{"meta":[],"data":[{"x":1}],"rows":1}'

    result = _with_fake_clickhouse(
        fake_execute,
        lambda: M.rows("SELECT 1 AS x WHERE y = {fy:UInt16}", {"fy": 2025}))

    assert result == [{"x": 1}]
    assert len(calls) == 1
    sql, params = calls[0]
    assert sql.endswith(" FORMAT JSON")
    assert params == {"param_fy": 2025}


def test_rows_with_no_params_passes_empty_dict():
    calls = []

    def fake_execute(sql, params=None):
        calls.append((sql, params))
        return '{"meta":[],"data":[],"rows":0}'

    _with_fake_clickhouse(fake_execute, lambda: M.rows("SELECT 1"))

    assert calls[0][1] == {}


def test_empty_body_raises_naming_the_model():
    def fake_execute(sql, params=None):
        return ""

    caught = None
    try:
        _with_fake_clickhouse(
            fake_execute,
            lambda: M.rows("SELECT 1 FROM epm_gold.gold_ic_reconciliation WHERE x = 1"))
    except Exception as e:  # noqa: BLE001 — asserting on the raise itself
        caught = e

    assert caught is not None
    assert "epm_gold.gold_ic_reconciliation" in str(caught)


def test_json_without_data_key_raises_naming_the_model():
    def fake_execute(sql, params=None):
        return '{"meta":[],"rows":0}'

    caught = None
    try:
        _with_fake_clickhouse(
            fake_execute,
            lambda: M.rows("SELECT 1 FROM epm_gold.gold_ic_unmatched WHERE x = 1"))
    except Exception as e:  # noqa: BLE001 — asserting on the raise itself
        caught = e

    assert caught is not None
    assert "epm_gold.gold_ic_unmatched" in str(caught)


def test_real_empty_result_stays_empty_list():
    def fake_execute(sql, params=None):
        return '{"meta":[],"data":[],"rows":0}'

    result = _with_fake_clickhouse(
        fake_execute,
        lambda: M.rows("SELECT 1 FROM epm_gold.gold_ic_reconciliation WHERE x = 1"))
    assert result == []


def test_error_propagates_unwrapped_and_is_not_built():
    class FakeResponse:
        text = ("Code: 60. DB::Exception: Table epm_gold.gold_ic_reconciliation "
                 "does not exist. (UNKNOWN_TABLE)")

    class FakeError(Exception):
        def __init__(self):
            super().__init__("500 from ClickHouse")
            self.response = FakeResponse()

    raised = FakeError()

    def fake_execute(sql, params=None):
        raise raised

    caught = None
    try:
        _with_fake_clickhouse(fake_execute, lambda: M.rows("SELECT 1"))
    except FakeError as e:
        caught = e

    assert caught is raised
    assert M.not_built(raised) is True


def test_code_60_alone_is_not_not_built():
    class FakeError(Exception):
        def __init__(self, text):
            super().__init__(text)

    e = FakeError("Code: 60 something")
    assert M.not_built(e) is False
    assert M.error_names(e) == set()


def test_error_name_cases():
    class FakeResponse:
        def __init__(self, text):
            self.text = text

    class FakeError(Exception):
        def __init__(self, text, with_response=True):
            super().__init__(text if not with_response else "str(e) text, unused")
            if with_response:
                self.response = FakeResponse(text)

    cases = [
        ("Code: 81. DB::Exception: Database epm_gold does not exist. (UNKNOWN_DATABASE)",
         True, {"UNKNOWN_DATABASE"}),
        ("Code: 159. DB::Exception: Timeout exceeded. (TIMEOUT_EXCEEDED)",
         False, {"TIMEOUT_EXCEEDED"}),
    ]
    for text, want_not_built, want_names in cases:
        e = FakeError(text)
        assert M.not_built(e) == want_not_built, text
        assert M.error_names(e) == want_names, text


def test_response_text_wins_over_str_e():
    class FakeResponse:
        text = "(UNKNOWN_TABLE)"

    class FakeError(Exception):
        def __init__(self):
            super().__init__("(TIMEOUT_EXCEEDED)")
            self.response = FakeResponse()

    e = FakeError()
    assert M.error_names(e) == {"UNKNOWN_TABLE"}


def test_module_imports_no_frappe_or_konsol_at_top_level():
    with open(CH_READ_PY) as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("frappe") or a.name.startswith("konsol")
                           for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("frappe")
            assert not (node.module or "").startswith("konsol")


def test_konsol_clickhouse_import_is_inside_rows_only():
    with open(CH_READ_PY) as fh:
        tree = ast.parse(fh.read())

    def _imports_konsol_clickhouse(node):
        return isinstance(node, ast.ImportFrom) and node.module == "konsol.clickhouse"

    # No such import outside any function (top-level body).
    for node in tree.body:
        assert not _imports_konsol_clickhouse(node)

    # Every such import in the whole tree lives inside a function named "rows".
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for inner in ast.walk(node):
                if inner is not node and _imports_konsol_clickhouse(inner):
                    assert node.name == "rows", (
                        f"konsol.clickhouse import found inside {node.name}, not rows")

    # And it is actually present inside rows.
    rows_fn = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "rows")
    assert any(_imports_konsol_clickhouse(n) for n in ast.walk(rows_fn))
