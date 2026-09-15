"""build_lock.build_writer() saves as Administrator (konsol#215 row W2).

The Build Approval Workflow gives the build job's transitions (Start, Fail to
Start, Complete, Fail) to the Administrator role only, so every save of the
build job that moves a row's state must run as Administrator: inside
build_writer(), which switches the session user and restores the caller's
session whole afterwards, error or not.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_LOCK = os.path.join(APP_DIR, "build_lock.py")
TASKS = os.path.join(APP_DIR, "tasks.py")


class _D(dict):
    """frappe._dict: attribute reads and writes are item reads and writes."""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


def _frappe():
    """A frappe whose set_user behaves as v15's (__init__.py:641): it rewrites
    the session in place (user, sid, data), clears form_dict and resets the
    permission caches."""
    frappe = types.ModuleType("frappe")
    session = _D(user="zz.analyst@example.com", sid="zz-sid", data=_D(csrf_token="zz"))
    frappe.local = types.SimpleNamespace(session=session, form_dict=_D(cmd="zz.method"),
                                         user_perms="perms:zz", cache={"k": "zz"})
    frappe.session = session
    frappe.flags = _D()
    frappe.db = types.SimpleNamespace(sql=lambda *a, **k: None)

    def set_user(user):
        session.user = user
        session.sid = user
        session.data = _D()
        frappe.local.form_dict = _D()
        frappe.local.user_perms = None
        frappe.local.cache = {}

    frappe.set_user = set_user
    return frappe


def _build_lock(frappe):
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("build_lock_w2", BUILD_LOCK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _assert_caller_restored(frappe):
    assert frappe.session.user == "zz.analyst@example.com"
    assert frappe.session.sid == "zz-sid"
    assert frappe.session.data == {"csrf_token": "zz"}
    assert frappe.local.form_dict == {"cmd": "zz.method"}
    assert not frappe.flags.get("konsol_build_writer")


def test_inside_the_writer_the_user_is_administrator_and_the_flag_is_set():
    frappe = _frappe()
    build_lock = _build_lock(frappe)
    with build_lock.build_writer():
        assert frappe.session.user == "Administrator"
        assert frappe.flags.konsol_build_writer is True


def test_the_caller_is_restored_after_a_normal_exit():
    frappe = _frappe()
    build_lock = _build_lock(frappe)
    with build_lock.build_writer():
        pass
    _assert_caller_restored(frappe)


def test_the_caller_is_restored_after_an_exception():
    frappe = _frappe()
    build_lock = _build_lock(frappe)
    try:
        with build_lock.build_writer():
            assert frappe.session.user == "Administrator"
            raise RuntimeError("the save failed")
    except RuntimeError:
        pass
    else:
        raise AssertionError("the error must propagate")
    _assert_caller_restored(frappe)


def test_a_nested_writer_keeps_administrator_until_the_outer_exit():
    frappe = _frappe()
    build_lock = _build_lock(frappe)
    with build_lock.build_writer():
        with build_lock.build_writer():
            assert frappe.session.user == "Administrator"
        assert frappe.session.user == "Administrator", "an inner exit keeps the outer switch"
        assert frappe.flags.konsol_build_writer is True
    _assert_caller_restored(frappe)


def _function(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _is_build_writer(with_node):
    return any(isinstance(item.context_expr, ast.Call)
               and getattr(item.context_expr.func, "id", None) == "build_writer"
               for item in with_node.items)


def _doc_saves(node, inside=False):
    """(lineno, inside a `with build_writer():`) for every doc.save(...) under node."""
    found = []
    for child in ast.iter_child_nodes(node):
        child_inside = inside or (isinstance(child, ast.With) and _is_build_writer(child))
        if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                and child.func.attr == "save" and getattr(child.func.value, "id", None) == "doc"):
            found.append((child.lineno, inside))
        found.extend(_doc_saves(child, child_inside))
    return found


def test_every_build_approval_save_of_the_build_job_is_inside_the_writer():
    with open(TASKS) as f:
        tree = ast.parse(f.read())
    for name in ("run_governed_build", "_finish_governed_build"):
        saves = _doc_saves(_function(tree, name))
        assert saves, f"{name}: no doc.save found"
        outside = [line for line, inside in saves if not inside]
        assert not outside, f"{name}: doc.save outside `with build_writer():` at line(s) {outside}"


def test_the_running_save_sits_inside_the_writer():
    with open(TASKS) as f:
        source = f.read()
    tree = ast.parse(source)
    run = _function(tree, "run_governed_build")
    running = next(n for n in ast.walk(run) if isinstance(n, ast.Assign)
                   and isinstance(n.value, ast.Constant) and n.value.value == "Running"
                   and getattr(n.targets[0], "attr", None) == "workflow_state")
    after = sorted((line, inside) for line, inside in _doc_saves(run) if line > running.lineno)
    assert after and after[0][1], "the Approved -> Running save must be inside `with build_writer():`"
