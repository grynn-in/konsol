"""konsol#305 R02: the old role-home and control-room back ends are gone.

`konsol/close/` replaces `home_api`, `home_model` and `control_api`, and D5
rejected `fiscal_calendar.current_period`'s "today falls in this period"
rule. This test fails if any of them comes back, or if any module still
imports them.
"""

import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_MODULES = ("home_api", "control_api", "home_model")


def test_old_modules_do_not_exist():
    present = [m for m in OLD_MODULES if os.path.exists(os.path.join(APP_DIR, m + ".py"))]
    assert present == [], "delete the old modules: " + ", ".join(present)


def _imports_old_module(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] in OLD_MODULES:
                    return alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[-1] in OLD_MODULES:
                return module
            if module in ("konsol", ""):
                for alias in node.names:
                    if alias.name in OLD_MODULES:
                        return alias.name
    return None


def test_no_module_imports_the_old_modules():
    importers = []
    for root, _dirs, files in os.walk(APP_DIR):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path) as f:
                src = f.read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            hit = _imports_old_module(tree)
            if hit:
                importers.append(os.path.relpath(path, APP_DIR) + " imports " + hit)
    assert importers == [], "remove these imports: " + "; ".join(sorted(importers))


def test_fiscal_calendar_has_no_current_period():
    with open(os.path.join(APP_DIR, "fiscal_calendar.py")) as f:
        tree = ast.parse(f.read())
    names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "current_period" not in names, "delete fiscal_calendar.current_period (D5 rejected its rule)"
