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


def main(argv):
    paths = argv[1:] or list(_discover())
    total = passed = 0
    failures = []
    skipped = []
    needs_pytest = []
    missing_deps = set()

    for path in paths:
        rel = os.path.relpath(path, ROOT)
        try:
            module = _load(path)
        except Exception as exc:
            # A module needing frappe (or pytest) is not a failure here — it is
            # simply not a host test. Say so rather than reporting a red run.
            skipped.append((rel, f"{type(exc).__name__}: {exc}"))
            continue

        for name in dir(module):
            if not name.startswith("test_"):
                continue
            fn = getattr(module, name)
            if not callable(fn):
                continue

            # Tests taking arguments want a pytest fixture (monkeypatch, tmp_path).
            # Not runnable here, and not a failure — report them honestly.
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

    print(f"{passed}/{total} passed across {len(paths) - len(skipped)} files")

    if skipped:
        print(f"\n{len(skipped)} file(s) skipped (need a live site, pytest, or a "
              f"third-party module)")

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
