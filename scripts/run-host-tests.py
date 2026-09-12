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
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "konsol", "tests")
# Tests that `import konsol...` need the app root importable. Without this
# sys.path[0] is scripts/, and those files were skipped as "not host tests"
# while the run still reported green.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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


def _is_skip(exc):
    """A test's own "skip me": unittest.SkipTest, or pytest's Skipped (from
    importorskip / pytest.skip), which is a BaseException and would otherwise
    escape the runner."""
    return isinstance(exc, unittest.SkipTest) or type(exc).__name__ == "Skipped"


def _skip_reason(exc):
    """Why a file cannot run on a host, or None when its load error is a real
    failure. A broken konsol import (a renamed name, a missing submodule of an
    installed package, a syntax error) must fail the run, not vanish into the
    skipped count."""
    if _is_skip(exc):
        return f"skipped: {exc}"
    if isinstance(exc, ImportError):
        root = (exc.name or "").split(".")[0]
        if root == "frappe":
            return "needs frappe"
        if isinstance(exc, ModuleNotFoundError) and root and root != "konsol":
            # Only when the package itself is absent; a missing submodule of
            # an installed (or stubbed) one, e.g. requests.nonexistent, is a bug.
            if root in sys.modules:
                return None
            try:
                present = importlib.util.find_spec(root) is not None
            except (ImportError, ValueError):
                present = True
            return None if present else f"needs {root}"
        if exc.name is None and str(exc).startswith("needs "):
            return str(exc)  # a test's own guard, e.g. ImportError("needs yaml")
    return None


def _is_stub(module):
    """A stand-in module a test built (types.ModuleType), not a real import."""
    return getattr(module, "__file__", None) is None and not hasattr(module, "__path__")


def _isolate(before):
    """Undo what one file did to konsol and to any stub frappe, so a stub
    frappe installed by one file cannot bind the konsol modules a later file
    imports. Real frappe modules stay loaded: frappe raises the gc threshold
    every time it is imported, and re-importing it per file overflows it."""
    # If the file left a stub `frappe`, the real frappe.* submodules it
    # imported are orphans under that stub: drop them with it.
    stub_frappe = "frappe" in sys.modules and _is_stub(sys.modules["frappe"])
    for key in list(sys.modules):
        root = key.split(".")[0]
        if key in before or root not in ("frappe", "konsol"):
            continue
        if root == "konsol" or stub_frappe or _is_stub(sys.modules[key]):
            del sys.modules[key]
    for key, mod in before.items():
        if key.split(".")[0] in ("frappe", "konsol") and sys.modules.get(key) is not mod:
            sys.modules[key] = mod


def main(argv):
    paths = argv[1:] or list(_discover())
    total = passed = 0
    failures = []
    skipped = []
    load_failures = 0
    needs_pytest = []
    missing_deps = set()

    for path in paths:
        rel = os.path.relpath(path, ROOT)
        before = dict(sys.modules)
        try:
            try:
                module = _load(path)
            except (Exception, unittest.SkipTest) as exc:
                reason = _skip_reason(exc)
                if reason:
                    # Needs a live site, pytest or a third-party module: not a
                    # host test here. Listed below, so a skip is never silent.
                    skipped.append((rel, reason))
                else:
                    load_failures += 1
                    failures.append((rel, "<load>", f"{type(exc).__name__}: {exc}",
                                     traceback.format_exc()))
                continue
            except KeyboardInterrupt:
                raise
            except BaseException as exc:
                if _is_skip(exc):
                    skipped.append((rel, f"skipped: {exc}"))
                else:  # SystemExit at import would end the run silently
                    load_failures += 1
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
                    if _skip_reason(exc):
                        # A third-party import inside the test body (yaml, requests).
                        missing_deps.add(exc.name)
                        needs_pytest.append(f"{rel}::{name}")
                        total -= 1
                    else:  # a konsol module, or a submodule of an installed package
                        failures.append((rel, name, f"{type(exc).__name__}: {exc}",
                                         traceback.format_exc()))
                except KeyboardInterrupt:
                    raise
                except BaseException as exc:
                    if _is_skip(exc):
                        # importorskip inside a test body: skipped, not failed
                        needs_pytest.append(f"{rel}::{name}")
                        total -= 1
                    else:  # includes SystemExit, which would end the run silently
                        failures.append((rel, name, f"{type(exc).__name__}: {exc}",
                                         traceback.format_exc()))
        finally:
            _isolate(before)

    ran = len(paths) - len(skipped) - load_failures
    print(f"{passed}/{total} passed across {ran} files")

    if skipped:
        print(f"\n{len(skipped)} file(s) skipped (need a live site, pytest, or a "
              f"third-party module):")
        for rel, reason in skipped:
            print(f"  {rel}: {reason}")

    if needs_pytest:
        extra = f"; missing modules: {', '.join(sorted(missing_deps))}" if missing_deps else ""
        print(f"{len(needs_pytest)} test(s) skipped (need a pytest fixture, "
              f"a skip, or a module{extra})")

    if failures:
        print(f"\n{len(failures)} failure(s):")
        for rel, name, msg, tb in failures:
            print(f"\n  {rel}::{name}\n    {msg}")
            if name == "<load>":
                # the cause is usually deep inside a konsol import
                for line in tb.rstrip().splitlines()[-6:]:
                    print(f"      {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
