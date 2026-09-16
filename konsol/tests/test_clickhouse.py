"""TDD tests for konsol.clickhouse — shared ClickHouse write helper."""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")


def test_clickhouse_module_exists():
    """clickhouse.py must exist."""
    assert os.path.exists(CH_PATH)


def test_clickhouse_has_get_connection():
    """Must expose get_connection() that reads EPM Settings."""
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "get_connection" in func_names


def test_clickhouse_has_execute():
    """Must expose execute(sql, params) for raw HTTP queries."""
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "execute" in func_names


def test_clickhouse_has_sync_table():
    """Must expose sync_table(table, columns, rows) — TRUNCATE + INSERT."""
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "sync_table" in func_names


def test_clickhouse_has_sync_doctype():
    """Must expose sync_doctype(doctype, table, field_map) — fetches docs, calls sync_table."""
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "sync_doctype" in func_names


def test_clickhouse_sync_table_truncates():
    """sync_table must INSERT into a temp table and swap it in, never TRUNCATE
    the live table (konsol#194)."""
    with open(CH_PATH) as f:
        content = f.read()
    assert "_sync_tmp" in content
    assert "EXCHANGE TABLES" in content.upper()
    assert "INSERT" in content.upper()


def test_clickhouse_uses_epm_settings():
    """Must read connection from EPM Settings."""
    with open(CH_PATH) as f:
        content = f.read()
    assert "EPM Settings" in content


# --- a forced sync fails loudly (konsol#194) ---

def _load_clickhouse():
    """clickhouse.py against a stub frappe (pattern from test_clickhouse_execute.py).

    ``requests`` is imported here, not at module scope: clickhouse.py needs it,
    but the source-reading tests above do not, and a host without it should
    skip only these."""
    import requests  # noqa: F401 — clickhouse.py imports it at module level

    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(
        in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe._logger = types.SimpleNamespace(
        warning=lambda *a, **k: None, error=lambda *a, **k: None,
        info=lambda *a, **k: None)
    frappe.logger = lambda: frappe._logger
    frappe.publish_realtime = lambda *a, **k: None

    model = types.ModuleType("frappe.model")
    base_document = types.ModuleType("frappe.model.base_document")
    base_document.get_controller = lambda dt: None
    model.base_document = base_document
    frappe.model = model

    sys.modules.update({"frappe": frappe, "frappe.model": model,
                        "frappe.model.base_document": base_document})
    spec = importlib.util.spec_from_file_location(
        "konsol_clickhouse_under_test_force", CH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Resp:
    """Just enough response for the HTTP branch's ``e.response.status_code``."""
    status_code = 500
    text = "Code: 60. DB::Exception: Table epm_staging.zz_rows does not exist"


_TABLE = "epm_staging.zz_rows"


def _errors(module):
    """The exception classes sync_table's three failure branches catch."""
    exc = module.requests.exceptions
    return {
        "connection_refused": exc.ConnectionError("connection refused"),
        "timeout": exc.Timeout("read timed out"),
        "http_error": exc.HTTPError("500 from ClickHouse", response=_Resp()),
    }


def _sync(module, error, force):
    """sync_table with every execute() raising ``error``.

    Returns (raised exception or None, return value or None)."""
    def boom(sql, params=None):
        raise error

    module.execute = boom
    try:
        return None, module.sync_table(_TABLE, ["a"], [("ZZ01",)], force=force)
    except BaseException as exc:  # noqa: BLE001 — which one is the assertion
        return exc, None


def test_a_forced_sync_raises_instead_of_going_quiet():
    """konsol#194: sync_table logged, recorded the failure and returned None even
    with force=True, so a manual ``bench execute`` of reconcile_all reported
    nothing wrong while the table stayed empty. A forced sync must raise, so
    reconcile_all's per-doctype try sees it and a manual run fails loudly.
    The failure is still recorded for check_health()."""
    module = _load_clickhouse()
    for error_type, error in _errors(module).items():
        module._sync_failures.clear()
        raised, returned = _sync(module, error, force=True)
        assert raised is error, (
            f"a forced sync must re-raise the {error_type} failure, not return "
            f"{returned!r}")
        recorded = module._sync_failures.get(_TABLE)
        assert recorded and recorded["error_type"] == error_type, (
            f"the {error_type} failure must still be recorded: {recorded!r}")


def test_an_unforced_sync_stays_best_effort():
    """A document save must not fail because ClickHouse is down: the unforced
    path keeps logging, recording and returning None."""
    module = _load_clickhouse()
    for error_type, error in _errors(module).items():
        module._sync_failures.clear()
        raised, returned = _sync(module, error, force=False)
        assert raised is None, f"an unforced sync must not raise: {raised!r}"
        assert returned is None, error_type
        recorded = module._sync_failures.get(_TABLE)
        assert recorded and recorded["error_type"] == error_type, repr(recorded)


# --- a failed sync leaves the old rows (konsol#194) ---

_TMP = _TABLE + "_sync_tmp"


def _statements(module, fail_on=None):
    """Point module.execute at a recorder and return the list it fills.

    ``fail_on``: the first statement containing this substring raises, as a
    part-way ClickHouse failure would."""
    recorded = []

    def recorder(sql, params=None):
        recorded.append(sql)
        if fail_on and fail_on in sql:
            raise module.requests.exceptions.HTTPError(
                "500 from ClickHouse", response=_Resp())

    module.execute = recorder
    return recorded


def _truncate_target(statement):
    """The table a TRUNCATE statement names (its last token)."""
    return statement.split()[-1]


def test_a_failed_sync_leaves_the_old_rows():
    """konsol#194: _sync_table_inner TRUNCATEd the live table first, so a sync
    that failed part way left it EMPTY. It must fill a sibling temp table and
    EXCHANGE it in, leaving the live table untouched until the swap."""
    module = _load_clickhouse()
    recorded = _statements(module)
    module._sync_table_inner(_TABLE, ["a"], [("ZZ01",), ("ZZ02",)])

    assert recorded[0].startswith("CREATE TABLE IF NOT EXISTS " + _TMP), recorded
    assert recorded[0].endswith(_TABLE), recorded[0]
    assert recorded[1].upper().startswith("TRUNCATE"), recorded
    assert _truncate_target(recorded[1]) == _TMP, recorded[1]

    inserts = [s for s in recorded if s.upper().startswith("INSERT")]
    assert inserts, recorded
    for statement in inserts:
        assert statement.startswith(f"INSERT INTO {_TMP} ("), statement

    exchanges = [i for i, s in enumerate(recorded) if "EXCHANGE TABLES" in s]
    assert len(exchanges) == 1, recorded
    swap = recorded[exchanges[0]]
    assert _TABLE in swap and _TMP in swap, swap

    for statement in recorded[:exchanges[0]]:
        if statement.upper().startswith("TRUNCATE"):
            assert _truncate_target(statement) == _TMP, (
                f"the live table must not be truncated before the swap: "
                f"{statement}")

    after = recorded[exchanges[0] + 1:]
    assert after and after[-1].upper().startswith("TRUNCATE"), recorded
    assert _truncate_target(after[-1]) == _TMP, after[-1]


def test_a_sync_that_fails_part_way_never_swaps():
    """A failing INSERT must raise before the EXCHANGE, so the live table keeps
    yesterday's rows rather than becoming empty."""
    module = _load_clickhouse()
    recorded = _statements(module, fail_on="INSERT INTO")
    try:
        module._sync_table_inner(_TABLE, ["a"], [("ZZ01",)])
    except module.requests.exceptions.HTTPError:
        pass
    else:
        raise AssertionError("_sync_table_inner must raise on a failed INSERT")

    assert not any("EXCHANGE TABLES" in s for s in recorded), recorded
    for statement in recorded:
        if statement.upper().startswith("TRUNCATE"):
            assert _truncate_target(statement) == _TMP, statement


def test_zero_rows_still_empties_the_live_table_through_the_swap():
    """reconcile relies on an empty row list emptying the table — but it must
    go through the same swap, not a bare TRUNCATE."""
    module = _load_clickhouse()
    recorded = _statements(module)
    module._sync_table_inner(_TABLE, ["a"], [])

    assert any("EXCHANGE TABLES" in s for s in recorded), recorded
    assert not any(s.upper().startswith("INSERT") for s in recorded), recorded


def test_inserts_stay_in_batches_of_1000():
    """The temp table is filled by the same 1000-row batches as before."""
    module = _load_clickhouse()
    recorded = _statements(module)
    module._sync_table_inner(_TABLE, ["a"], [(f"ZZ{i:04d}",) for i in range(2500)])

    inserts = [s for s in recorded if s.upper().startswith("INSERT")]
    assert len(inserts) == 3, f"expected 3 batches, got {len(inserts)}"
