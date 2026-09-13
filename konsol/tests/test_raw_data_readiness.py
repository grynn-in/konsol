"""A trial-balance-only site builds without Airbyte (konsol#182, decided 13 Sep 2026).

The canonical path is a trial balance uploaded to konsol; on submit its rows land
in epm_raw.trial_balance_submissions. Every raw-dependent build scope
(consolidation among them) ran tasks.check_raw_data_available in preflight, which
knew only connectors and Airbyte and refused such a site with "Airbyte has never
synced". A submitted Trial Balance Submission now counts as raw data.

The two functions run here, lifted out of tasks.py with ast and executed against
a stub frappe (tasks.py imports the Airbyte client at module level).
"""
import ast
import os
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS = os.path.join(APP_DIR, "tasks.py")
TBS = "Trial Balance Submission"


class _Row(dict):
    __getattr__ = dict.get


def check(skip=0, submitted=(), tables=(TBS, "Connector"), connectors=(), sync_at=None, sync_status=None):
    settings = _Row(skip_airbyte_sync=skip, last_airbyte_sync_status=sync_status, last_airbyte_sync_at=sync_at,
                    last_airbyte_sync_rows=7)

    def exists(doctype, filters=None):
        assert doctype == TBS and doctype in tables, f"read {doctype}"
        return any(all(r.get(k) == v for k, v in (filters or {}).items()) for r in submitted)

    def get_all(doctype, filters=None, fields=None, limit_page_length=None):
        assert doctype == "Connector"
        return [_Row(c) for c in connectors if c.get("enabled", 1)]

    frappe = types.SimpleNamespace(get_single=lambda name: settings, get_all=get_all,
                                   db=types.SimpleNamespace(table_exists=lambda name: name in tables, exists=exists))
    with open(TASKS) as f:
        tree = ast.parse(f.read())
    wanted = {"check_raw_data_available", "_trial_balances_submitted"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in nodes} == wanted
    ns = {"frappe": frappe}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), TASKS, "exec"), ns)
    return ns["check_raw_data_available"]()


def test_a_tb_only_site_builds_without_airbyte():
    ok, message = check(submitted=[{"docstatus": 1}])
    assert ok and "trial balances" in message, message


def test_with_nothing_submitted_the_airbyte_gate_is_unchanged():
    assert check() == (False, "Airbyte has never synced — epm_raw may be empty")
    ok, message = check(sync_at="2026-09-01 00:00:00", sync_status="Succeeded")
    assert ok and message.startswith("Airbyte sync OK"), message
    assert check(sync_at="2026-09-01 00:00:00", sync_status="Failed")[0] is False


def test_drafts_and_cancelled_do_not_count():
    ok, message = check(submitted=[{"docstatus": 0}, {"docstatus": 2}])
    assert not ok and "Airbyte has never synced" in message


def test_the_flag_still_works():
    ok, message = check(skip=1, tables=())
    assert ok and "skip_airbyte_sync" in message, message


def test_submitted_trial_balances_pass_even_with_an_unsynced_connector():
    """The canonical path does not wait on an ERP feed (13 Sep 2026)."""
    never = [{"connector_name": "ZZ ERP", "last_sync_at": None, "last_sync_status": None}]
    assert check(connectors=never) == (False, "Connector 'ZZ ERP' has never synced — epm_raw may be empty")
    assert check(connectors=never, submitted=[{"docstatus": 1}])[0] is True


def test_a_site_without_the_doctype_is_not_blocked_by_it():
    assert check(tables=("Connector",)) == (False, "Airbyte has never synced — epm_raw may be empty")
