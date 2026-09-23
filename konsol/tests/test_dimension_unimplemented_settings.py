"""A Dimension setting that drives nothing must refuse to be set (konsol#255).

``survives_close`` is a Check on Dimension: on, each (account, dimension value)
P&L balance would zero into retained earnings carrying that same value; off,
the P&L closes to a retained-earnings row outside the dimension.

Only the OFF branch exists. On 23 September 2026 Deepak Pai settled the close
as "one lump, no dimensions", which is what the warehouse already implements
and what the field already defaults to. The ON branch is a real option,
declared so a site can see it was considered, but nobody has asked for it and
nothing reads the field — not the close, not ``schema_apply``, not dbt.

So today an admin can tick "Survives Year-End Close", save, and watch every
number in the system stay exactly where it was. That is the silent no-op
konsol#247 rules out and konsol#255 exists to remove: refuse, or report; never
accept and ignore.

``depends_on: eval:doc.in_trial_balance`` on the field is not that refusal.
It is form-level only — Frappe persists ``survives_close = 1`` without
complaint from a patch, a fixture, a REST call or a data import, which is
exactly how a stale tick gets onto a site nobody is watching.

Three things these tests pin beyond the refusal itself:

  * the message must say the feature is UNBUILT, not that the value is wrong.
    "Invalid value" teaches an admin that they mistyped; the truth is that
    they asked for something real that konsol has not built, and they are
    entitled to learn that the close currently collapses to one undimensioned
    line whatever the box says;
  * the message must cite konsol#255, because this refusal is temporary. When
    the ON branch is built the refusal is DELETED, and the deletion has to be
    findable from the string an admin quotes in a support ticket; and
  * the flag must be read with ``tb_dimension_model._is_on``, not ``bool()``.
    A Check that arrived through JSON, CSV or REST carries the TEXT ``"0"``,
    which is truthy in Python — a ``bool()`` reading would refuse saves on
    every such row while letting the genuinely ticked ones through unexamined.

The refusal is scoped to dimensions declared ``in_trial_balance``, and that
scope is load-bearing rather than cosmetic. Outside the trial balance the
setting is not merely unimplemented, it is meaningless: no ``dim_`` column
exists, no close touches the dimension, and the field is not even shown.
Refusing there would also spring a trap — unticking ``in_trial_balance`` on a
site that already holds ``survives_close = 1`` is a save, and a refusal on
that save would make the stale tick impossible to walk away from. The sibling
name rule is scoped the same way and for the same reason
(test_dimension_name_validation.py).

The controller is loaded by file path with stubbed frappe/konsol modules (the
pattern test_trial_balance_submission.py established and
test_dimension_name_validation.py reuses), with the REAL pure
``konsol.tb_dimension_model`` left reachable so the controller's import of the
shared flag reading resolves — stubbing it would test nothing, since using it
is the point. The stub ``Document.save`` calls ``validate``, because that is
what Frappe's save does.
"""
import ast
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_HERE, "..")
_SRC = os.path.join(_APP, "epm", "doctype", "dimension", "dimension.py")

#: The field under test, and a legal trial-balance name to hang it on, so no
#: test here can fail for the sibling row's reason instead of this one.
FIELD = "survives_close"
LEGAL_NAME = "dim_cost_center"


class _ValidationError(Exception):
    """Stand-in for frappe.ValidationError."""


def _throw(msg, exc=None, **kwargs):
    """frappe.throw: record how it was called, then raise."""
    _THROWN.append((msg, exc))
    raise (exc or _ValidationError)(msg)


_THROWN = []
_REBUILDS = []


def _stub(name, **attrs):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


class _Doc:  # stand-in for frappe.model.document.Document
    """``save`` runs ``validate``, because that is what Frappe's save does."""

    def save(self):
        self.validate()


_stub("frappe", whitelist=lambda *a, **k: (lambda fn: fn),
      throw=_throw, ValidationError=_ValidationError)
# __path__ makes the stub a package so the controller's import of the REAL
# pure konsol.tb_dimension_model resolves while schema_lifecycle stays stubbed.
_stub("konsol", __path__=[_APP])
_stub("frappe.model")
_stub("frappe.model.document", Document=_Doc)
_stub("konsol.schema_lifecycle", check_epm_admin=lambda: None,
      apply_and_rebuild=lambda doc, action: _REBUILDS.append(action))

_spec = importlib.util.spec_from_file_location("dimension_under_test_255", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

with open(_SRC) as _f:
    SOURCE = _f.read()
TREE = ast.parse(SOURCE)

#: Check-shaped values that mean ticked, and that must therefore be refused.
#: ``"1"`` is not a curiosity: it is what a Check arriving through JSON, CSV
#: or a REST payload actually looks like, which is the route the form-level
#: ``depends_on`` cannot see.
ON_VALUES = (1, "1", True, "Yes", "yes", "true")

#: Check-shaped values that mean unticked, and that must therefore save.
#: ``"0"`` is the one that matters: it is truthy in Python, so a ``bool()``
#: reading would refuse a dimension whose box is plainly empty.
OFF_VALUES = (0, "0", "", "false", "No", None, False)


def _doc(survives_close=0, in_trial_balance=1, name=LEGAL_NAME, **fields):
    """A Dimension with a legal name, in the trial balance by default."""
    doc = _m.Dimension.__new__(_m.Dimension)
    doc.dimension_name = name
    doc.in_trial_balance = in_trial_balance
    doc.in_budget = 0
    doc.survives_close = survives_close
    doc.status = "Draft"
    for k, v in fields.items():
        setattr(doc, k, v)
    return doc


def _refusal(**kwargs):
    """Validate a doc built from `kwargs`; return the message it was refused
    with. Fails if it was accepted."""
    del _THROWN[:]
    doc = _doc(**kwargs)
    try:
        doc.validate()
    except _ValidationError as e:
        return str(e)
    raise AssertionError(
        f"{FIELD}={kwargs.get('survives_close')!r} was accepted; konsol "
        f"has not built that branch, so accepting it changes no number")


def _accepts(doc):
    """Validate `doc`, failing with the refusal if it was refused."""
    del _THROWN[:]
    try:
        doc.validate()
    except _ValidationError as e:
        raise AssertionError(
            f"{FIELD}={doc.survives_close!r} (in_trial_balance="
            f"{doc.in_trial_balance!r}) was refused: {e}")
    assert not _THROWN, _THROWN


# --- ticking an unbuilt setting is refused ------------------------------


def test_ticking_survives_close_is_refused():
    """The whole point. A setting nothing reads must not be settable."""
    _refusal(survives_close=1)


def test_the_text_one_is_refused_exactly_as_the_integer_is():
    """The route the form-level depends_on cannot see: JSON, CSV, REST, patch.

    A Check arriving from any of those carries a string. If only the integer
    were refused, the refusal would guard the one path that was already
    guarded and miss every path that was not.
    """
    integer = _refusal(survives_close=1)
    for on in ON_VALUES:
        assert _refusal(survives_close=on) == integer, (
            f"{FIELD}={on!r} was refused differently from the integer 1")


def test_survives_close_off_saves_normally():
    """The shipped default, and the branch the warehouse actually implements."""
    _accepts(_doc(survives_close=0))


def test_every_off_shaped_value_saves():
    """Including the text "0", which is truthy in Python and must not refuse."""
    for off in OFF_VALUES:
        _accepts(_doc(survives_close=off))


def test_a_dimension_with_no_such_field_at_all_saves():
    """An older row, or a doc built before the field existed, is not ticked."""
    doc = _doc()
    del doc.survives_close
    _accepts(doc)


def test_the_refusal_is_a_validation_error():
    """frappe.throw(..., frappe.ValidationError) — the house pattern."""
    _refusal(survives_close=1)
    assert _THROWN and _THROWN[0][1] is _ValidationError, _THROWN


def test_the_tick_is_never_silently_cleared():
    """Refuse; never untick on the admin's behalf.

    Clearing the box and saving would look like the setting was accepted and
    is off, which is the silent no-op wearing a different hat.
    """
    doc = _doc(survives_close=1)
    try:
        doc.validate()
    except _ValidationError:
        pass
    assert doc.survives_close == 1, (
        f"validate rewrote {FIELD} to {doc.survives_close!r}")


# --- the message says UNBUILT, not INVALID ------------------------------


def test_the_message_says_the_feature_is_not_implemented():
    """An admin must learn the option is real and konsol has not built it."""
    msg = _refusal(survives_close=1).lower()
    assert "not implemented" in msg, msg


def test_the_message_does_not_call_the_value_invalid():
    """"Invalid" teaches the admin they mistyped. They did not.

    They asked for a behaviour konsol declares and does not provide, and the
    message has to own that rather than blame the input.
    """
    msg = _refusal(survives_close=1).lower()
    for blame in ("invalid", "not valid", "illegal", "not a legal", "not allowed"):
        assert blame not in msg, f"message blames the value ({blame!r}): {msg}"


def test_the_message_names_the_setting_an_admin_ticked():
    """By its LABEL, which is the only name an admin ever sees on the form."""
    msg = _refusal(survives_close=1)
    assert "Survives Year-End Close" in msg, msg


def test_the_message_says_what_the_close_actually_does_today():
    """The admin ticked a box to change the close. Say what the close does.

    Without this the refusal is "no" with no account of the behaviour being
    refused, and the admin has no way to know whether they still have a
    problem.
    """
    msg = _refusal(survives_close=1).lower()
    assert "retained earnings" in msg, msg
    assert "one" in msg or "single" in msg, msg
    assert "dimension" in msg, msg


def test_the_message_cites_the_issue_so_the_deletion_is_findable():
    """This refusal is temporary. When the branch ships, it is deleted.

    The citation is how someone holding the message in a support ticket finds
    the decision, and how whoever builds the close finds the code to remove.
    """
    assert "konsol#255" in _refusal(survives_close=1)


def test_the_source_says_the_refusal_is_to_be_deleted():
    """Findable from the code too, not only from the string."""
    low = SOURCE.lower()
    assert "konsol#255" in low, "dimension.py must cite the issue"
    assert "delete" in low or "remove" in low, (
        "dimension.py must say this refusal goes when the branch is built")


# --- scope: the trial balance, and only the trial balance ---------------


def test_not_in_the_trial_balance_is_not_refused():
    """Outside the trial balance the setting is meaningless, not unbuilt.

    No dim_ column exists for the dimension and no close touches it, so there
    is no behaviour for the tick to fail to deliver.
    """
    for on in ON_VALUES:
        _accepts(_doc(survives_close=on, in_trial_balance=0))


def test_unticking_in_trial_balance_is_always_possible():
    """The escape hatch for a site that already holds a ticked one.

    ``survives_close = 1`` can already be on a site — a fixture or a REST call
    put it there before this refusal existed. Unticking ``in_trial_balance``
    is a save. If that save were refused the stale tick would be a trap, and
    ``unpublish()`` — which goes through ``self.save()`` — would be refused
    with it, so the dimension could not even be retired.
    """
    doc = _doc(survives_close=1)
    doc.in_trial_balance = 0
    _accepts(doc)
    del _REBUILDS[:]
    doc.unpublish()
    assert doc.status == "Inactive", doc.status
    assert _REBUILDS == ["Unpublish"], _REBUILDS


def test_the_in_trial_balance_flag_is_read_the_way_the_intake_reads_it():
    """A scope decided by truthiness would apply to rows the intake excludes."""
    for off in OFF_VALUES:
        _accepts(_doc(survives_close=1, in_trial_balance=off))


# --- the flag reading is imported, not restated -------------------------


def _validate_node():
    node = next(
        (n for n in ast.walk(TREE)
         if isinstance(n, ast.FunctionDef) and n.name == "validate"), None)
    assert node is not None, "dimension.py has no validate"
    return node


def test_survives_close_is_read_with_the_shared_is_on():
    """Not bool(), and not truthiness. See the module docstring."""
    reads = [
        ast.unparse(call) for call in ast.walk(_validate_node())
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        and call.func.id == "_is_on" and FIELD in ast.unparse(call)
    ]
    assert reads, (
        f"validate must read {FIELD} with tb_dimension_model._is_on; no such "
        f"call found")


def test_survives_close_is_not_read_with_bool_or_truthiness():
    """The two ways the reading drifts back to Python's idea of true."""
    for call in ast.walk(_validate_node()):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "bool"):
            assert FIELD not in ast.unparse(call), (
                f"validate reads {FIELD} with bool(): {ast.unparse(call)}")
    for test in ast.walk(_validate_node()):
        if not isinstance(test, (ast.If, ast.IfExp)):
            continue
        cond = ast.unparse(test.test)
        if FIELD in cond:
            assert "_is_on" in cond, (
                f"validate branches on {FIELD} without _is_on: {cond}")


def test_the_shared_reading_is_imported():
    imported = {
        alias.name
        for node in ast.walk(TREE) if isinstance(node, ast.ImportFrom)
        and node.module == "konsol.tb_dimension_model"
        for alias in node.names
    }
    assert "_is_on" in imported, (
        "dimension.py must import _is_on from konsol.tb_dimension_model")


def test_the_shared_reading_agrees_with_these_tests():
    """Ground the ON/OFF tables against the module the controller uses."""
    from konsol.tb_dimension_model import _is_on

    for on in ON_VALUES:
        assert _is_on(on), on
    for off in OFF_VALUES:
        assert not _is_on(off), off


# --- nothing reads the field, which is why it is refused ----------------


def test_nothing_in_the_warehouse_reads_survives_close():
    """The premise of the whole row, checked rather than asserted in prose.

    If this fails, the ON branch has been built (or half-built) and the
    refusal above is now wrong — delete it and this test together, citing
    konsol#255.
    """
    readers = []
    for dirpath, dirnames, filenames in os.walk(_APP):
        dirnames[:] = [d for d in dirnames if d not in (".git", "tests", "__pycache__")]
        for filename in filenames:
            if not filename.endswith((".py", ".sql", ".yml", ".yaml")):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, encoding="utf-8", errors="replace") as fh:
                if FIELD in fh.read():
                    readers.append(os.path.relpath(path, _APP))
    # dimension.py is the refusal itself; it is the one place the name may
    # appear outside the doctype JSON that declares the field.
    assert readers == [os.path.join("epm", "doctype", "dimension", "dimension.py")], (
        f"{FIELD} is read somewhere: {readers}")
