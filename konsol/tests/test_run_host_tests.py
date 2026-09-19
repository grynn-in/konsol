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
