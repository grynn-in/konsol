"""TDD — orchestrator execution state machine (PRD-3). Pure-python."""
from konsol.orchestrator.dag import Dag, Step
from konsol.orchestrator.state import RunState, Status


def _diamond():
    return Dag([
        Step(id="a", type="t"),
        Step(id="b", type="t", depends_on=["a"]),
        Step(id="c", type="t", depends_on=["a"]),
        Step(id="d", type="t", depends_on=["b", "c"]),
    ])


def test_initial_runnable_is_roots():
    st = RunState(_diamond())
    assert {s.id for s in st.runnable()} == {"a"}
    assert not st.is_done()


def test_dependents_unlock_after_success():
    st = RunState(_diamond())
    st.mark("a", Status.SUCCESS)
    assert {s.id for s in st.runnable()} == {"b", "c"}


def test_join_waits_for_all_parents():
    st = RunState(_diamond())
    st.mark("a", Status.SUCCESS)
    st.mark("b", Status.SUCCESS)
    assert {s.id for s in st.runnable()} == {"c"}  # d still blocked on c
    st.mark("c", Status.SUCCESS)
    assert {s.id for s in st.runnable()} == {"d"}


def test_skipped_dependency_is_treated_as_satisfied():
    st = RunState(_diamond())
    st.mark("a", Status.SKIPPED)
    assert {s.id for s in st.runnable()} == {"b", "c"}


def test_failure_blocks_descendants_and_settles():
    st = RunState(_diamond())
    st.mark("a", Status.FAILED)
    assert st.runnable() == []          # b, c blocked by failed a
    assert st.is_done()                 # nothing more can run
    assert st.has_failed()
    assert st.failed() == {"a"}


def test_done_when_all_success():
    st = RunState(_diamond())
    for sid in ("a", "b", "c", "d"):
        st.mark(sid, Status.SUCCESS)
    assert st.is_done()
    assert not st.has_failed()


def test_retry_resets_failed_step_and_descendants():
    st = RunState(_diamond())
    st.mark("a", Status.SUCCESS)
    st.mark("b", Status.SUCCESS)
    st.mark("c", Status.FAILED)
    st.retry("c")
    # c back to pending and runnable; d stays pending (was never run)
    assert st.status("c") == Status.PENDING
    assert {s.id for s in st.runnable()} == {"c"}
    assert not st.has_failed()


def test_resume_from_resets_step_and_downstream_only():
    st = RunState(_diamond())
    for sid in ("a", "b", "c", "d"):
        st.mark(sid, Status.SUCCESS)
    st.resume_from("b")
    assert st.status("a") == Status.SUCCESS   # upstream untouched
    assert st.status("b") == Status.PENDING
    assert st.status("d") == Status.PENDING   # descendant reset
    assert st.status("c") == Status.SUCCESS   # not downstream of b
    assert {s.id for s in st.runnable()} == {"b"}
