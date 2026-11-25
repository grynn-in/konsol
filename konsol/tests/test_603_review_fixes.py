"""PR #603 review fixes.

F1 (#535): with a `PREFIX-.#####` autoname Frappe's delete_doc reverts the
series when the deleted name is the last one (update_naming_series runs
after on_trash, before after_delete), so the next upload would reuse the
deleted name, and Trial Balance Submissions that name the old upload by
string would look as if they came from the new one. Each upload's
after_delete puts the counter back above the deleted number.

F2 (#536): dbt prints "does not match any enabled nodes" as a warning on a
successful build too (an --exclude of an isolated scope that matches
nothing), so the text fallback matches "Nothing to do" only.
"""
import ast
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME_SERIES = os.path.join(APP_DIR, "name_series.py")
CONTROLLERS = {
    os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_upload",
                 "trial_balance_upload.py"): "TBU-",
    os.path.join(APP_DIR, "epm", "doctype", "budget_upload", "budget_upload.py"): "BUU-",
}
TASKS = os.path.join(APP_DIR, "tasks.py")


def _keep(prefix, name, current):
    calls = []

    class DB:
        def sql(self, q, params=None, **k):
            calls.append((" ".join(q.split()), params))
            if q.strip().lower().startswith("update") and current[0] < params[0]:
                current[0] = params[0]
            return []

    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = types.SimpleNamespace(db=DB())
    try:
        ns = {}
        exec(compile(open(NAME_SERIES).read(), NAME_SERIES, "exec"), ns)
        ns["keep_series_above"](prefix, name)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return calls


def test_the_counter_goes_back_above_a_deleted_last_name():
    current = [12]                      # Frappe reverted 13 -> 12 on delete
    _keep("TBU-", "TBU-00013", current)
    assert current == [13]


def test_the_counter_is_never_lowered():
    current = [20]
    calls = _keep("TBU-", "TBU-00013", current)
    assert current == [20]
    assert all("`current` <" in q or "`current`<" in q for q, _p in calls)


def test_a_name_outside_the_series_is_left_alone():
    assert _keep("TBU-", "ZZ-legacy", [5]) == []


def _after_delete_calls(path):
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "after_delete":
            return [ast.unparse(c) for c in ast.walk(node) if isinstance(c, ast.Call)]
    return None


def test_each_upload_keeps_its_counter_after_delete():
    for path, prefix in CONTROLLERS.items():
        calls = _after_delete_calls(path)
        assert calls is not None, f"{path}: no after_delete"
        assert any("keep_series_above" in c and repr(prefix) in c and "self.name" in c
                   for c in calls), (path, calls)


def test_the_text_fallback_matches_nothing_to_do_only():
    tree = ast.parse(open(TASKS).read())
    markers = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name) and t.id == "_NO_MATCH_MARKERS"]
    assert markers == [("Nothing to do",)]
