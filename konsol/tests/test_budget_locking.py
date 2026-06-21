"""Tests for budget_cell_save optimistic locking (spec #51 §1).

Site-free / AST-based per the repo convention: assert the concurrency control
is wired into budget_cell_save — a `for_update` row lock, a `base_modified`
staleness check returning an HTTP 409 conflict, and a `modified` baseline echo
on success.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")


def _cell_save_src():
    with open(API_PATH) as fh:
        src = fh.read()
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "budget_cell_save":
            return ast.get_source_segment(src, n)
    raise AssertionError("budget_cell_save not found")


def test_takes_for_update_row_lock():
    src = _cell_save_src()
    assert "for_update=True" in src


def test_checks_base_modified_and_returns_conflict():
    src = _cell_save_src()
    assert "base_modified" in src
    assert '"status": "conflict"' in src
    assert "http_status_code = 409" in src


def test_lock_taken_before_staleness_check():
    """The row lock must be acquired before the conflict comparison, else two
    requests could both pass the check."""
    src = _cell_save_src()
    assert src.index("for_update=True") < src.index('"status": "conflict"')


def test_conflict_payload_carries_current_value_and_modified():
    src = _cell_save_src()
    for key in ('"current_amount"', '"current_modified"', '"your_amount"'):
        assert key in src


def test_success_echoes_new_modified_baseline():
    src = _cell_save_src()
    # the ok return must include modified so the client can store the baseline
    ok_tail = src[src.rindex('"status": "ok"'):]
    assert '"modified"' in ok_tail


def test_backward_compatible_when_base_modified_omitted():
    """The staleness check is guarded by `if base_modified` — omitting it keeps
    last-write-wins behaviour."""
    src = _cell_save_src()
    assert "if base_modified and" in src


def _api_src():
    with open(API_PATH) as fh:
        return fh.read()


def test_cell_save_handles_create_race():
    """Concurrent create of the same new combo must retry-as-update, not 500."""
    src = _cell_save_src()
    assert "DuplicateEntryError" in src and "continue" in src


def test_upsert_handles_create_race():
    src = _api_src()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_upsert_budget_line")
    body = ast.get_source_segment(src, fn)
    assert "DuplicateEntryError" in body


def test_account_category_load_is_guarded():
    """A ClickHouse blip must not 500 every gated write — load is wrapped and
    the failure is not cached."""
    src = _api_src()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_account_category")
    body = ast.get_source_segment(src, fn)
    assert "try:" in body and "_load_account_category_map" in body
    assert "frappe.throw" in body  # clean, retryable error instead of raw 500
