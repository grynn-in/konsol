#!/usr/bin/env python3
"""Run konsol's host tests without pytest.

Most of konsol/tests/ is written pytest-style — plain `test_*` functions, no
class — but the bench virtualenv has no pytest and this repo has no CI, so
those files were failing silently for anyone who did not happen to have pytest
on their host. This runs them with nothing but the standard library.

    python3 scripts/run-host-tests.py                 # every host test
    python3 scripts/run-host-tests.py konsol/tests/test_period_status.py
    python3 scripts/run-host-tests.py --require-pytest   # what CI runs

A test that takes pytest fixtures cannot be called here. When pytest is
installed, those tests run through it, one pytest process per file, and count
in the totals (konsol#318). Without pytest they are listed as skips, and
--require-pytest makes that a failure.

Only covers tests that read source files. Anything importing `frappe` needs a
live site:

    bench --site <site> run-tests --module konsol.tests.test_fiscal_year_bench
"""
import importlib.util
import inspect
import os
import sys
import traceback
import unittest

# realpath, not abspath: os.getcwd() resolves symlinks (/tmp -> /private/tmp on
# macOS), so an unresolved ROOT makes relpath() of a named file walk out and back.
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TESTS = os.path.join(ROOT, "konsol", "tests")
#: Test files that must load and run in full wherever these tests run, CI
#: included. They hold the entity-access tests for ClickHouse reads
#: (konsol#158) and need no frappe; if they fell into a "needs frappe" skip,
#: the run would stay green with nothing checked. A skip of either file, or of
#: any test in it, fails the run, and so does either file missing from a full
#: run. When files are named on the command line, only the named ones count.
MUST_RUN = (
    os.path.join("konsol", "tests", "test_entity_access_host.py"),
    os.path.join("konsol", "tests", "test_security_source.py"),
)
# Tests that `import konsol...` need the app root importable. Without this
# sys.path[0] is scripts/, and those files were skipped as "not host tests"
# while the run still reported green.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


#: Files that may skip without failing the run. Everything else that skips is
#: coverage that disappeared, and konsol#248 is the case it exists for: a file
#: is most likely to stop importing exactly when someone adds a dependency to
#: the module it covers.
SKIP_LIST = os.path.join(ROOT, "scripts", "host-test-skips.txt")


def expected_skips():
    """The declared skips, as repo-relative paths. Missing file -> none, so a
    checkout without it fails every skip rather than passing every skip."""
    try:
        with open(SKIP_LIST) as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return frozenset()
    return frozenset(
        line.strip() for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    )


def _undeclared_skips(skipped, declared):
    """One failure per skipped file that is not a declared skip."""
    return [
        (rel, "<skipped>",
         f"the file did not load and is not a declared skip ({reason}). "
         f"If this is deliberate, add it to scripts/host-test-skips.txt and say "
         f"why in the pull request; otherwise the module it covers has gained a "
         f"dependency that a host cannot import.",
         "")
        for rel, reason in skipped
        if rel not in declared
    ]


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


#: Modules that come with a Frappe bench and are never on a host. A test body
#: that imports one has a missing stub, not a missing dependency (konsol#255).
FRAPPE_STACK = frozenset({"frappe", "rq", "redis"})


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


def _must_run_key(path):
    """The MUST_RUN entry ``path`` is, or None. Matched by file identity, so a
    symlinked, absolute or differently-cased spelling of the file still counts."""
    if not os.path.exists(path):
        return None
    for m in MUST_RUN:
        full = os.path.join(ROOT, m)
        if os.path.exists(full) and os.path.samefile(path, full):
            return m
    return None


def _must_run_failures(named, skipped, needs_pytest):
    """Failures for MUST_RUN files that are missing, skipped, or had a test
    skipped. ``named`` is the files given on the command line, if any.
    ``skipped`` and ``needs_pytest`` name a MUST_RUN file by its MUST_RUN key
    (main() reports it that way)."""
    must = set(MUST_RUN)
    if named:
        must = {k for k in map(_must_run_key, named) if k}
    out = []
    for rel in sorted(must):
        if not named and not os.path.exists(os.path.join(ROOT, rel)):
            out.append((rel, "<must-run>", "missing; this file must run on every host, CI included", ""))
    for rel, reason in skipped:
        if rel in must:
            out.append((rel, "<must-run>", f"file skipped ({reason}); it must run on every host, CI included", ""))
    for item in needs_pytest:
        rel, _, name = item.partition("::")
        if rel in must:
            out.append((rel, name, "test skipped; every test in this file must run on every host, CI included", ""))
    return out


def _pytest_available():
    return importlib.util.find_spec("pytest") is not None


def _run_fixture_tests(fixture_tests):
    """Run tests that take pytest fixtures through pytest (konsol#318).

    One pytest process per file: the files stub ``sys.modules`` at import,
    and one collection over the whole suite collides. Returns (passed, total,
    failures, skips) in the runner's own shapes; a test pytest skips (an
    importorskip, say) stays a listed skip. A file whose pytest run produced
    no report (a collection error) fails as a whole, with pytest's output.
    """
    import subprocess
    import tempfile
    import xml.etree.ElementTree as ET

    by_file = {}
    for rel, path, name in fixture_tests:
        by_file.setdefault((rel, path), []).append(name)

    passed = total = 0
    failures, skips = [], []
    for (rel, path), names in by_file.items():
        with tempfile.TemporaryDirectory() as d:
            report = os.path.join(d, "report.xml")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 f"--junitxml={report}", *[f"{path}::{n}" for n in names]],
                cwd=ROOT, capture_output=True, text=True)
            if not os.path.exists(report):
                failures.append((rel, "<pytest>", f"pytest exited {proc.returncode} "
                                 "without a report", proc.stdout + proc.stderr))
                continue
            seen = set()
            for case in ET.parse(report).iter("testcase"):
                name = case.get("name", "")
                # a parametrized test reports one case per parameter set,
                # "name[params]"; each counts, and all belong to its function
                seen.add(name.split("[", 1)[0])
                bad = case.find("failure")
                if bad is None:
                    bad = case.find("error")
                if case.find("skipped") is not None:
                    skips.append(f"{rel}::{name}")
                elif bad is not None:
                    total += 1
                    failures.append((rel, name, (bad.get("message") or "failed").strip(),
                                     bad.text or ""))
                else:
                    total += 1
                    passed += 1
            for name in names:
                if name not in seen:
                    # asked for, never reported: pytest could not collect it
                    total += 1
                    failures.append((rel, name, "pytest did not run this test",
                                     proc.stdout + proc.stderr))
    return passed, total, failures, skips


def main(argv):
    # --require-pytest (CI passes it): tests that take pytest fixtures must
    # run, so a missing pytest fails the run instead of skipping them all.
    require_pytest = "--require-pytest" in argv[1:]
    args = [a for a in argv[1:] if a != "--require-pytest"]
    paths = args or list(_discover())
    total = passed = 0
    fixture_tests = []   # (rel, path, name): run through pytest after the loop
    failures = []
    skipped = []
    load_failures = 0
    needs_pytest = []
    missing_deps = set()

    for path in paths:
        # A MUST_RUN file is reported under its MUST_RUN key, however it was
        # spelled, so _must_run_failures can match its skips.
        rel = _must_run_key(path) or os.path.relpath(os.path.realpath(path), ROOT)
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
                    fixture_tests.append((rel, path, name))
                    continue

                total += 1
                try:
                    fn()
                    passed += 1
                except ModuleNotFoundError as exc:
                    root = (exc.name or "").split(".")[0]
                    if root in FRAPPE_STACK:
                        # Never on a host, so a test body that reaches it is
                        # missing a stub. It fails, rather than vanishing into
                        # "needs pytest": a test that means to skip says so
                        # with importorskip, which is handled below.
                        failures.append((rel, name, f"{type(exc).__name__}: {exc} "
                                         f"(a test body reached the {root} stack: stub "
                                         f"it, or importorskip if the test should skip)",
                                         traceback.format_exc()))
                    elif _skip_reason(exc):
                        # A third-party import inside the test body (yaml, requests).
                        missing_deps.add(exc.name or str(exc))
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

    if fixture_tests and _pytest_available():
        p, t, fixture_failures, fixture_skips = _run_fixture_tests(fixture_tests)
        passed += p
        total += t
        failures.extend(fixture_failures)
        needs_pytest.extend(fixture_skips)
    elif fixture_tests:
        needs_pytest.extend(f"{rel}::{name}" for rel, _, name in fixture_tests)
        if require_pytest:
            failures.append(("<runner>", "--require-pytest",
                             f"pytest is required to run {len(fixture_tests)} test(s) "
                             "that take pytest fixtures, and it is not installed", ""))

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

    declared = expected_skips()
    failures.extend(_undeclared_skips(skipped, declared))

    # A declared skip that ran is good news and a stale list. Report it, but do
    # not fail: it makes a number unexplained, not wrong (konsol#247).
    if not args:
        ran_anyway = sorted(declared - {rel for rel, _ in skipped})
        if ran_anyway:
            print(f"\n{len(ran_anyway)} declared skip(s) ran after all — trim "
                  f"scripts/host-test-skips.txt:")
            for rel in ran_anyway:
                print(f"  {rel}")

    failures.extend(_must_run_failures(args, skipped, needs_pytest))

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
