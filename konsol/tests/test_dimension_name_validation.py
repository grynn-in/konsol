"""An illegal dimension name is refused on save, not three layers later (konsol#255).

What a Dimension may be CALLED is already settled: ``dim_`` followed by
lower-case letters, digits and underscores, and nothing else. ``schema_apply``
enforces it to decide whether it can spell the ClickHouse column, and
``tb_dimension_model`` enforces it to decide whether a trial-balance file may
carry the header.

Neither is where the name is typed. Without a ``validate`` on the doctype an
admin could save ``dim_Cost_Center``, tick the flags, publish it, upload a
file — and only then be told the name was never usable, by a refusal pointing
at the file rather than at the Dimension. These tests put the refusal at the
point of entry.

Two things they pin beyond the refusal itself:

  * the message must name the offending value AND state the legal shape, so
    the admin can fix it without reading the code; and
  * the controller must IMPORT the rule rather than restate it. There are
    already two copies of this regex and a third, looser one
    (``_SAFE_IDENTIFIER``) on the same field — which is how the field drifted
    in the first place. A regex literal in the controller is a regression
    even if every behavioural test above it stays green.

The controller is loaded by file path with stubbed frappe/konsol modules (the
pattern test_trial_balance_submission.py established), with the REAL pure
``konsol.tb_dimension_model`` left reachable so an import of the shared rule
resolves — the point of the exercise is that the controller uses that module,
so stubbing it would test nothing.
"""
import ast
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_HERE, "..")
_SRC = os.path.join(_APP, "epm", "doctype", "dimension", "dimension.py")


class _ValidationError(Exception):
    """Stand-in for frappe.ValidationError."""


def _throw(msg, exc=None, **kwargs):
    """frappe.throw: record how it was called, then raise."""
    _THROWN.append((msg, exc))
    raise (exc or _ValidationError)(msg)


_THROWN = []


def _stub(name, **attrs):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


class _Doc:  # stand-in for frappe.model.document.Document
    pass


_stub("frappe", whitelist=lambda *a, **k: (lambda fn: fn),
      throw=_throw, ValidationError=_ValidationError)
# __path__ makes the stub a package so the controller's import of the REAL
# pure konsol.tb_dimension_model resolves while schema_lifecycle stays stubbed.
_stub("konsol", __path__=[_APP])
_stub("frappe.model")
_stub("frappe.model.document", Document=_Doc)
_stub("konsol.schema_lifecycle", check_epm_admin=lambda: None,
      apply_and_rebuild=lambda *a, **k: None)

_spec = importlib.util.spec_from_file_location("dimension_under_test", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

with open(_SRC) as _f:
    SOURCE = _f.read()
TREE = ast.parse(SOURCE)

#: Names that must be refused, and why each one is here.
ILLEGAL = (
    "dim_Cost_Center",   # upper case: the parsers lowercase headers, so it can match none
    "dim cost center",   # spaces: not an identifier, no column can be spelled
    "Cost_Center",       # no dim_ prefix: nothing recognises it as a dimension
    " dim_x ",           # padding: schema_apply fullmatches the raw field
    "dim_cost-center",   # a hyphen is not an identifier character
    "dim_",              # the prefix alone names no dimension
)

LEGAL = ("dim_cost_center", "dim_x", "dim_project_2", "dim_a_b_c")


def _doc(name):
    """A Dimension instance carrying `name`, with no frappe machinery."""
    doc = _m.Dimension.__new__(_m.Dimension)
    doc.dimension_name = name
    return doc


def _refusal(name):
    """Validate `name`, returning the message; fails if it was accepted."""
    del _THROWN[:]
    try:
        _doc(name).validate()
    except _ValidationError as e:
        return str(e)
    raise AssertionError(f"{name!r} was accepted; it must be refused")


# --- the refusal --------------------------------------------------------


def test_illegal_names_are_refused_on_save():
    for name in ILLEGAL:
        _refusal(name)


def test_legal_names_are_accepted():
    for name in LEGAL:
        del _THROWN[:]
        _doc(name).validate()
        assert not _THROWN, f"{name!r} was refused: {_THROWN}"


def test_refusal_names_the_offending_value():
    for name in ILLEGAL:
        msg = _refusal(name)
        assert name in msg, f"message for {name!r} does not quote it: {msg}"


def test_refusal_states_the_legal_shape():
    """The admin must be able to fix the name from the message alone."""
    msg = _refusal("dim_Cost_Center")
    low = msg.lower()
    assert "dim_" in msg, msg
    assert "lower" in low, msg
    assert "digit" in low, msg
    assert "underscore" in low, msg


def test_padding_is_visible_in_the_refusal():
    """A name that is only wrong in its whitespace must not read as correct."""
    msg = _refusal(" dim_x ")
    assert "' dim_x '" in msg or '" dim_x "' in msg, msg


def test_refusal_is_a_validation_error():
    """frappe.throw(..., frappe.ValidationError) — the house pattern."""
    _refusal("dim_Cost_Center")
    assert _THROWN and _THROWN[0][1] is _ValidationError, _THROWN


def test_the_name_is_never_auto_corrected():
    """Refuse; never lowercase or strip on the user's behalf.

    Two Dimensions differing only in case must not silently collapse onto one
    warehouse column, so a refused name is left exactly as it was typed.
    """
    for name in ("dim_Cost_Center", " dim_x "):
        doc = _doc(name)
        try:
            doc.validate()
        except _ValidationError:
            pass
        assert doc.dimension_name == name, (
            f"validate rewrote {name!r} to {doc.dimension_name!r}")


# --- the rule is imported, not restated ---------------------------------


def test_controller_imports_the_shared_rule():
    imported = {
        alias.name
        for node in ast.walk(TREE) if isinstance(node, ast.ImportFrom)
        and node.module == "konsol.tb_dimension_model"
        for alias in node.names
    }
    assert "is_legal_dimension_name" in imported, (
        "dimension.py must import the shared rule from konsol.tb_dimension_model")


def test_controller_carries_no_regex_of_its_own():
    """A third copy of the rule is the thing this row exists to prevent."""
    assert "import re" not in SOURCE, "dimension.py must not import re"
    for node in ast.walk(TREE):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "[a-z0-9_]" not in node.value, (
                f"dimension.py restates the rule as a regex: {node.value!r}")


def test_validate_delegates_to_the_shared_helper():
    validate = next(
        (n for n in ast.walk(TREE)
         if isinstance(n, ast.FunctionDef) and n.name == "validate"), None)
    assert validate is not None, "dimension.py has no validate"
    called = {
        c.func.id for c in ast.walk(validate)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }
    assert "is_legal_dimension_name" in called, (
        "validate must decide with the shared helper, not a rule of its own")


def test_the_shared_helper_agrees_with_the_controller():
    """The controller and tb_dimension_model cannot disagree about a name."""
    from konsol.tb_dimension_model import is_legal_dimension_name

    for name in ILLEGAL:
        assert not is_legal_dimension_name(name), name
    for name in LEGAL:
        assert is_legal_dimension_name(name), name
