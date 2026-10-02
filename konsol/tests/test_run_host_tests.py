"""The host test runner must fail when coverage disappears (konsol#248).

A test file that stops importing is the one case a gate exists for: a file is
most likely to stop importing exactly when someone adds a dependency to the
module it covers, which is when its tests matter most. The runner used to
classify such a file as *skipped*, leave it out of the denominator, and print
`N/N passed` with no failures — it printed `2328/2328 passed` while fifteen
hierarchy tests had silently gone.

These tests pin the behaviour that replaces it: a skip is legitimate only when
`scripts/host-test-skips.txt` says so, and any other skip fails the run.
"""
import contextlib
import importlib.util
import io
import os
import tempfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNNER = os.path.join(ROOT, "scripts", "run-host-tests.py")


def _runner():
    spec = importlib.util.spec_from_file_location("run_host_tests_under_test", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*paths):
    """Run the runner over `paths`; return (exit_code, printed_output)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = _runner().main(["run-host-tests.py", *paths])
    return code, out.getvalue()


@contextlib.contextmanager
def _test_file(body, name="test_zz_generated.py"):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            fh.write(body)
        yield path


NEEDS_FRAPPE = "import frappe  # not installed on a host\n\n\ndef test_x():\n    assert True\n"
PASSES = "def test_x():\n    assert True\n"


def test_unlisted_skip_fails_the_run():
    """A file that stops importing, and is not a declared skip, FAILS.

    This is konsol#248 itself. Before the fix the runner returned 0 here and
    printed `0/0 passed across 0 files`.
    """
    with _test_file(NEEDS_FRAPPE) as path:
        code, out = _run(path)
    assert code == 1, f"an unlisted skip must fail the run; got {code}\n{out}"
    assert "not a declared skip" in out, out


def test_the_headline_does_not_claim_green_when_a_file_vanished():
    """The summary must not read as a pass when a file dropped out."""
    with _test_file(NEEDS_FRAPPE) as path:
        _, out = _run(path)
    headline = out.strip().splitlines()[0]
    assert "passed across" in headline, out
    # the run failed, so a reader must not be able to stop at the first line
    assert "failure(s)" in out, out


def test_a_declared_skip_still_passes():
    """A file named in scripts/host-test-skips.txt may skip without failing."""
    runner = _runner()
    declared = runner.expected_skips()
    assert declared, "the declared-skip list must not be empty"
    # every declared entry is a real file, or the list is stale
    for rel in declared:
        assert os.path.exists(os.path.join(ROOT, rel)), f"declared skip no longer exists: {rel}"
    code, out = _run(*[os.path.join(ROOT, rel) for rel in sorted(declared)])
    assert code == 0, f"declared skips must not fail the run\n{out}"


def test_a_passing_file_is_unaffected():
    with _test_file(PASSES) as path:
        code, out = _run(path)
    assert code == 0, out
    assert "1/1 passed" in out, out


def test_a_real_failure_still_fails():
    """The fix must not swallow ordinary failures."""
    with _test_file("def test_x():\n    assert False, 'boom'\n") as path:
        code, out = _run(path)
    assert code == 1, out
    assert "boom" in out, out


def test_a_broken_konsol_import_still_fails_as_a_load_error():
    """A missing konsol name is a failure, not a skip — unchanged behaviour."""
    body = "from konsol.definitely_not_a_module import nope\n\n\ndef test_x():\n    assert True\n"
    with _test_file(body) as path:
        code, out = _run(path)
    assert code == 1, out
    assert "<load>" in out, out


def test_a_test_that_reaches_the_frappe_stack_mid_body_fails_instead_of_vanishing():
    """frappe, rq and redis are never on a host, so a test body that imports
    one has a missing stub, not a missing dependency. It used to be filed as
    "needs pytest" and dropped from the total: six dimension tests went that
    way after a merge made on_submit import a frappe-bound module, and the
    suite and CI stayed green. Only the file's headline count changed.

    The imported names cannot exist even inside a bench, so this holds where
    frappe is installed too."""
    for module in ("frappe.zz_konsol_never_a_module", "rq.zz_konsol_never_a_module"):
        body = (f"def test_x():\n    import {module}  # noqa: F401\n\n\n"
                "def test_y():\n    assert True\n")
        with _test_file(body) as path:
            code, out = _run(path)
        assert code == 1, (module, out)
        assert "test_x" in out and "stub it" in out, (module, out)
        assert "1/2 passed" in out, (module, out)


def test_importorskip_mid_body_is_still_a_skip_and_is_listed():
    """A test that means to skip says so with importorskip, and still may:
    it is reported as skipped, not passed and not failed."""
    import pytest  # the generated file needs it; skip on a host without it
    del pytest
    body = ("import pytest\n\n\ndef test_x():\n"
            "    pytest.importorskip('frappe.zz_konsol_never_a_module')\n\n\n"
            "def test_y():\n    assert True\n")
    with _test_file(body) as path:
        code, out = _run(path)
    assert code == 0, out
    assert "1/1 passed" in out and "1 test(s) skipped" in out, out


FIXTURE_TESTS = (
    "import pytest\n\n\n"
    "def test_plain():\n    assert True\n\n\n"
    "def test_fixture_passes(tmp_path):\n    assert tmp_path.exists()\n\n\n"
    "def test_fixture_fails(tmp_path):\n    assert False, 'fixture boom'\n"
)


def test_a_test_that_takes_a_fixture_runs_through_pytest_and_counts():
    """konsol#318: a test with pytest fixtures used to be listed as "needs
    pytest" and left out of the total, so CI never ran any of them (60 on
    main). The runner now hands them to pytest and counts the results: one
    passes, one fails, and the failure fails the run."""
    import pytest  # the runner needs it to run these; skip on a host without it
    del pytest
    with _test_file(FIXTURE_TESTS) as path:
        code, out = _run(path)
    assert code == 1, out
    assert "2/3 passed" in out, out
    assert "test_fixture_fails" in out and "fixture boom" in out, out


def test_without_pytest_fixture_tests_are_listed_and_require_pytest_fails():
    """Without pytest the fixture tests can't run. On a laptop they stay a
    listed skip; under --require-pytest (what CI passes) that is a failure."""
    runner = _runner()

    def run(*argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = runner.main(["run-host-tests.py", *argv])
        return code, out.getvalue()

    with _test_file(FIXTURE_TESTS) as path, \
            mock.patch.object(runner, "_pytest_available", lambda: False):
        code, out = run(path)
        assert code == 0 and "1/1 passed" in out and "2 test(s) skipped" in out, out
        code, out = run("--require-pytest", path)
        assert code == 1 and "pytest is required" in out, out


def test_a_parametrized_fixture_test_counts_each_case():
    """A parametrized test is reported by pytest as name[params], one case per
    parameter set. Each case counts; none is mistaken for a test that never ran."""
    import pytest
    del pytest
    body = ("import pytest\n\n\n@pytest.mark.parametrize('n', [1, 2, 3])\n"
            "def test_p(n, tmp_path):\n    assert n < 3, n\n")
    with _test_file(body) as path:
        code, out = _run(path)
    assert code == 1, out
    assert "2/3 passed" in out and "did not run" not in out, out


def _fixture_run(body, *names):
    """Hand `names` in a generated file straight to the pytest half of the
    runner; return (passed, total, failures, skips)."""
    import pytest
    del pytest
    runner = _runner()
    with _test_file(body) as path:
        return runner._run_fixture_tests([("gen.py", path, n) for n in names])


def test_one_name_pytest_cannot_collect_does_not_take_the_file_with_it():
    """Node ids made pytest refuse the whole file over one bad name, so every
    other fixture test in it failed too. Only the missing one fails now."""
    passed, total, failures, _ = _fixture_run(
        FIXTURE_TESTS, "test_fixture_passes", "test_ghost")
    assert (passed, total) == (1, 2), failures
    assert [(f[1], f[2]) for f in failures] == [("test_ghost", "pytest did not run this test")]


def test_a_teardown_error_fails_the_test_once():
    """pytest reports a test whose fixture breaks in teardown twice, a pass and
    an error. It is one test, and it failed."""
    body = ("import pytest\n\n\n@pytest.fixture\ndef broken():\n    yield 1\n"
            "    raise RuntimeError('teardown boom')\n\n\n"
            "def test_t(broken):\n    assert broken == 1\n")
    passed, total, failures, _ = _fixture_run(body, "test_t")
    assert (passed, total) == (0, 1), failures
    assert len(failures) == 1 and "teardown boom" in failures[0][2] + failures[0][3]


def test_an_xfail_counts_as_a_pass_not_a_skip():
    """An expected failure ran and did what it declares; listing it as a skip
    would fail a MUST_RUN file for it."""
    body = ("import pytest\n\n\n@pytest.mark.xfail(reason='known')\n"
            "def test_x(tmp_path):\n    assert False\n")
    passed, total, failures, skips = _fixture_run(body, "test_x")
    assert (passed, total, failures, skips) == (1, 1, [], [])


def test_a_file_pytest_cannot_collect_fails_once_with_its_output():
    """A file that imports on the host but not under pytest is one failure for
    the file, carrying pytest's output, not one per test on top of it."""
    body = ("import sys\nif '_pytest' in sys.modules:\n"
            "    raise ImportError('only under pytest')\n\n\n"
            "def test_a(tmp_path):\n    pass\n\n\ndef test_b(tmp_path):\n    pass\n")
    passed, total, failures, _ = _fixture_run(body, "test_a", "test_b")
    assert (passed, total) == (0, 2), failures
    assert len(failures) == 1 and failures[0][1] == "<pytest>", failures
    assert "only under pytest" in failures[0][3], failures


def test_a_relative_path_resolves_from_where_the_runner_was_started():
    """pytest runs from the app root; a path given relative to another
    directory must still find the file."""
    runner = _runner()
    with _test_file(FIXTURE_TESTS) as path:
        here = os.getcwd()
        os.chdir(os.path.dirname(path))
        try:
            passed, total, failures, _ = runner._run_fixture_tests(
                [("gen.py", os.path.basename(path), "test_fixture_passes")])
        finally:
            os.chdir(here)
    assert (passed, total, failures) == (1, 1, []), failures
