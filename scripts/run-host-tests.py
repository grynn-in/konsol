#!/usr/bin/env python3
"""Run konsol's host tests without pytest.

Most of konsol/tests/ is written pytest-style — plain `test_*` functions, no
class — but the bench virtualenv has no pytest and this repo has no CI, so
those files were failing silently for anyone who did not happen to have pytest
on their host. This runs them with nothing but the standard library.

    python3 scripts/run-host-tests.py                 # every host test
    python3 scripts/run-host-tests.py konsol/tests/test_period_status.py

Only covers tests that read source files. Anything importing `frappe` needs a
live site:

    bench --site <site> run-tests --module konsol.tests.test_period_status_bench
"""
import importlib.util
import inspect
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "konsol", "tests")
# Tests that `import konsol...` need the app root importable. Without this
# sys.path[0] is scripts/, and those files were skipped as "not host tests"
# while the run still reported green.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#: Module trees a test file may stub or bind to a stub; reset after each file.
_ISOLATED = ("frappe", "konsol")


def _discover():
    for name in sorted(os.listdir(TESTS)):
        if name.startswith("test_") and name.endswith(".py"):
            yield os.path.join(TESTS, name)


def _load(path):
    spec = importlib.util.spec_from_file_location(
        os.path.basename(path)[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skip_reason(exc):
    """Why a file cannot run on a host, or None when its load error is a real
    failure. Only a missing third-party module, pytest or frappe is a reason:
    a broken konsol import (a renamed name, a syntax error) must fail the run,
    not vanish into the skipped count."""
    if isinstance(exc, ImportError):
        root = (exc.name or "").split(".")[0]
        if root == "frappe":
            return "needs frappe"
        if isinstance(exc, ModuleNotFoundError) and root and root != "konsol":
            return f"needs {root}"
        if exc.name is None and str(exc).startswith("needs "):
            return str(exc)  # a test's own guard, e.g. ImportError("needs yaml")
    return None


def _isolate(before):
    """Put frappe and konsol modules back as they were before a file ran, so a
    stub frappe that one file installs cannot bind the konsol modules a later
    file imports."""
    for key in [k for k in sys.modules if k.split(".")[0] in _ISOLATED]:
        if key not in before:
            del sys.modules[key]
    for key, mod in before.items():
        if key.split(".")[0] in _ISOLATED and sys.modules.get(key) is not mod:
            sys.modules[key] = mod


def main(argv):
    paths = argv[1:] or list(_discover())
    total = passed = 0
    failures = []
    skipped = []
    needs_pytest = []
    missing_deps = set()

    for path in paths:
        rel = os.path.relpath(path, ROOT)
        before = dict(sys.modules)
        try:
            try:
                module = _load(path)
            except Exception as exc:
                reason = _skip_reason(exc)
                if reason:
                    # Needs a live site, pytest or a third-party module: not a
                    # host test here. Listed below, so a skip is never silent.
                    skipped.append((rel, reason))
                else:
                    failures.append((rel, "<load>", f"{type(exc).__name__}: {exc}",
                                     traceback.format_exc()))
                continue

            for name in dir(module):
                if not name.startswith("test_"):
                    continue
                fn = getattr(module, name)
                if not callable(fn):
                    continue

                # Tests taking arguments want a pytest fixture (monkeypatch,
                # tmp_path). Not runnable here, and not a failure — report them.
                try:
                    takes_fixtures = bool(inspect.signature(fn).parameters)
                except (TypeError, ValueError):
                    takes_fixtures = False
                if takes_fixtures:
                    needs_pytest.append(f"{rel}::{name}")
                    continue

                total += 1
                try:
                    fn()
                    passed += 1
                except ModuleNotFoundError as exc:
                    # A third-party import inside the test body (yaml, requests).
                    missing_deps.add(exc.name)
                    needs_pytest.append(f"{rel}::{name}")
                    total -= 1
                except Exception as exc:
                    failures.append((rel, name, f"{type(exc).__name__}: {exc}",
                                     traceback.format_exc()))
        finally:
            _isolate(before)

    print(f"{passed}/{total} passed across {len(paths) - len(skipped)} files")

    if skipped:
        print(f"\n{len(skipped)} file(s) skipped (need a live site, pytest, or a "
              f"third-party module):")
        for rel, reason in skipped:
            print(f"  {rel}: {reason}")

    if needs_pytest:
        extra = f"; missing modules: {', '.join(sorted(missing_deps))}" if missing_deps else ""
        print(f"{len(needs_pytest)} test(s) skipped (need a pytest fixture{extra})")

    if failures:
        print(f"\n{len(failures)} failure(s):")
        for rel, name, msg, tb in failures:
            print(f"\n  {rel}::{name}\n    {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
