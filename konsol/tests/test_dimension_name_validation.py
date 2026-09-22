"""The dim_ name rule binds trial-balance dimensions, and only those (konsol#255).

What a TRIAL-BALANCE dimension may be called is settled: ``dim_`` followed by
lower-case letters, digits and underscores, and nothing else. ``schema_apply``
enforces it to decide whether it can spell the ClickHouse column, and
``tb_dimension_model`` enforces it to decide whether a trial-balance file may
carry the header.

Neither is where the name is typed. Without a ``validate`` on the doctype an
admin could save ``dim_Cost_Center``, tick the flags, publish it, upload a
file — and only then be told the name was never usable, by a refusal pointing
at the file rather than at the Dimension. These tests put that refusal at the
point of entry.

But ``Dimension`` is konsol's GENERAL dimension registry, not the trial
balance's private list, and its other consumers require no ``dim_`` prefix:

  * ``schema_apply`` spells fact-table columns with ``_SAFE_IDENTIFIER``,
    ``^[a-z][a-z0-9_]*$`` — no prefix;
  * ``budget_grain.budget_dimension_names()`` returns raw ``dimension_name``
    values and uses them as Budget Line fieldnames — no prefix; and
  * ``test_hierarchy_node_ambiguity`` already stubs that function as
    ``["business_unit"]``, so a name with no prefix is the shape the existing
    suite tests this code against.

Applying the trial-balance rule to every Dimension therefore breaks records
the rest of the system is built to hold. The damage is not only "cannot edit
the label": ``unpublish()`` goes through ``self.save()``, so a budget-only
dimension named ``business_unit`` could not be RETIRED at all — the one
operation that un-declaring depends on — and ``config_service.apply_config``,
which has no per-row error handling, would abort a whole bundle on the first
such dimension.

So the rule is scoped to the thing that makes it true: a dimension declared
``in_trial_balance`` will have a ``dim_`` column created and a header matched
for it, so it must be legally named. One that is not declared is outside the
rule, and the refusal has to say WHICH it is, or ticking the flag later just
moves the silent failure instead of removing it.

Two things these tests pin beyond the refusal itself:

  * the message must name the offending value, state the legal shape AND name
    the trial balance as the reason the rule applies, so an admin who ticked
    a box can see what ticking it demanded; and
  * the controller must IMPORT the rule rather than restate it — both the name
    rule and the reading of the Check flag. There are already two copies of
    this regex and a third, looser one (``_SAFE_IDENTIFIER``) on the same
    field, which is how the field drifted in the first place. A regex literal
    in the controller is a regression even if every behavioural test above it
    stays green.

The controller is loaded by file path with stubbed frappe/konsol modules (the
pattern test_trial_balance_submission.py established), with the REAL pure
``konsol.tb_dimension_model`` left reachable so an import of the shared rule
resolves — the point of the exercise is that the controller uses that module,
so stubbing it would test nothing. The stub ``Document.save`` calls
``validate``, because that is what Frappe's save does and it is precisely the
path ``unpublish`` takes.
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
_REBUILDS = []


def _stub(name, **attrs):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


class _Doc:  # stand-in for frappe.model.document.Document
    """``save`` runs ``validate``, because that is what Frappe's save does.

    Without this the tests could not see the defect at all: ``unpublish``
    does its work through ``self.save()``, so a validate that refuses the
    name refuses the retirement.
    """

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

_spec = importlib.util.spec_from_file_location("dimension_under_test", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

with open(_SRC) as _f:
    SOURCE = _f.read()
TREE = ast.parse(SOURCE)

#: Names that must be refused FOR A TRIAL-BALANCE DIMENSION, and why each one
#: is here. Every one of these would break the dim_* column or the header
#: match; none of them breaks a budget fieldname.
ILLEGAL = (
    "dim_Cost_Center",   # upper case: the parsers lowercase headers, so it can match none
    "dim cost center",   # spaces: not an identifier, no column can be spelled
    "Cost_Center",       # no dim_ prefix: nothing recognises it as a dimension
    " dim_x ",           # padding: schema_apply fullmatches the raw field
    "dim_cost-center",   # a hyphen is not an identifier character
    "dim_",              # the prefix alone names no dimension
)

LEGAL = ("dim_cost_center", "dim_x", "dim_project_2", "dim_a_b_c")

#: A real, non-prefixed dimension name. This exact string is what
#: test_hierarchy_node_ambiguity.py:332 stubs budget_dimension_names() to
#: return, so it is not a hypothetical: it is the shape the suite already
#: tests the budget grain against.
BUDGET_ONLY_NAME = "business_unit"


def _doc(name, in_trial_balance=1, **fields):
    """A Dimension carrying `name`, declared for the trial balance by default.

    The default is 1 because that is the case the name rule is ABOUT; every
    test here that means "and it is not in the trial balance" says so.
    """
    doc = _m.Dimension.__new__(_m.Dimension)
    doc.dimension_name = name
    doc.in_trial_balance = in_trial_balance
    doc.in_budget = 0
    doc.status = "Draft"
    for k, v in fields.items():
        setattr(doc, k, v)
    return doc


def _budget_only_doc(name=BUDGET_ONLY_NAME, status="Published"):
    """A published, budget-only dimension — legal for budgets, not for a TB."""
    return _doc(name, in_trial_balance=0, in_budget=1, status=status)


def _refusal(name, **kwargs):
    """Validate `name`, returning the message; fails if it was accepted."""
    del _THROWN[:]
    try:
        _doc(name, **kwargs).validate()
    except _ValidationError as e:
        return str(e)
    raise AssertionError(f"{name!r} was accepted; it must be refused")


def _accepts(doc):
    """Validate `doc`, failing with the refusal if it was refused."""
    del _THROWN[:]
    try:
        doc.validate()
    except _ValidationError as e:
        raise AssertionError(
            f"{doc.dimension_name!r} (in_trial_balance="
            f"{doc.in_trial_balance!r}) was refused: {e}")
    assert not _THROWN, _THROWN


# --- the rule binds trial-balance dimensions ----------------------------


def test_illegal_names_are_refused_when_declared_for_the_trial_balance():
    for name in ILLEGAL:
        _refusal(name)


def test_legal_names_are_accepted_when_declared_for_the_trial_balance():
    for name in LEGAL:
        _accepts(_doc(name))


# --- ...and only those --------------------------------------------------


def test_a_budget_only_dimension_needs_no_dim_prefix():
    """``business_unit`` is what budget_dimension_names() is built to return."""
    _accepts(_budget_only_doc())


def test_a_budget_only_dimension_can_be_unpublished():
    """The retirement path. ``unpublish`` saves, and a save validates.

    Un-declaring a dimension is how a site stops new values arriving for it.
    If the name rule refuses the save, the dimension cannot be retired at
    all — and ``config_service._remove_config_entity`` calls this same
    ``unpublish``, so ``apply_config(prune=True)`` would abort on it too.
    """
    doc = _budget_only_doc()
    del _REBUILDS[:]
    doc.unpublish()
    assert doc.status == "Inactive", doc.status
    assert _REBUILDS == ["Unpublish"], _REBUILDS


def test_a_budget_only_dimension_can_be_relabelled():
    """Editing anything on it goes through validate; none of that is refused."""
    doc = _budget_only_doc()
    doc.label = "Business Unit (EMEA)"
    _accepts(doc)


def test_every_illegal_name_is_fine_while_not_in_the_trial_balance():
    """The scope is the flag, not the name. Nothing here creates a column."""
    for name in ILLEGAL:
        _accepts(_doc(name, in_trial_balance=0))


# --- the edge: ticking the flag on a name the trial balance cannot use ---


def test_ticking_in_trial_balance_on_a_budget_name_is_refused():
    """Otherwise the silent failure has moved, not gone.

    The admin ticks a box on a dimension that has been fine for months; the
    refusal has to arrive on that save, not at upload time.
    """
    doc = _budget_only_doc()
    _accepts(doc)                 # fine as it stands
    doc.in_trial_balance = 1      # ...and refused the moment it is declared
    del _THROWN[:]
    try:
        doc.validate()
    except _ValidationError:
        pass
    else:
        raise AssertionError(
            f"{BUDGET_ONLY_NAME!r} was accepted into the trial balance")


def test_that_refusal_names_the_trial_balance_as_the_reason():
    """"Rename it" is not an answer on its own: say what demanded the name."""
    msg = _refusal(BUDGET_ONLY_NAME)
    assert "trial balance" in msg.lower(), msg


def test_that_refusal_offers_the_other_way_out():
    """Renaming is one fix; not declaring it for the trial balance is the other.

    A dimension the site uses for budgets may simply not belong on a trial
    balance, and renaming it would rewrite every Budget Line fieldname built
    from it. The message must not present the rename as the only move.
    """
    msg = _refusal(BUDGET_ONLY_NAME).lower()
    assert "untick" in msg or "unticking" in msg, msg


# --- the refusal's shape ------------------------------------------------


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


# --- the flag is read the way the intake reads it -----------------------


def test_the_flag_is_read_the_way_the_intake_reads_it():
    """A Check that arrived as the TEXT "0" is off, not truthy.

    ``tb_dimension_model`` names the off-values rather than trusting Python
    truthiness, precisely because a row that came through JSON, CSV or REST
    carries "0". If the controller disagreed, a dimension the intake treats
    as out of the trial balance would be held to the trial balance's rule.
    """
    for off in (0, "0", "", "false", "No", None, False):
        _accepts(_doc(BUDGET_ONLY_NAME, in_trial_balance=off))
    for on in (1, "1", True, "Yes"):
        del _THROWN[:]
        try:
            _doc(BUDGET_ONLY_NAME, in_trial_balance=on).validate()
        except _ValidationError:
            continue
        raise AssertionError(f"in_trial_balance={on!r} did not apply the rule")


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


def test_controller_imports_the_shared_reading_of_the_flag():
    """The flag's meaning is a second rule, and a second chance to drift."""
    imported = {
        alias.name
        for node in ast.walk(TREE) if isinstance(node, ast.ImportFrom)
        and node.module == "konsol.tb_dimension_model"
        for alias in node.names
    }
    assert "_is_on" in imported, (
        "dimension.py must read in_trial_balance with tb_dimension_model's "
        "_is_on, not with its own truthiness")


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


def test_the_budget_grain_would_have_rejected_its_own_dimension():
    """Ground the scope claim: the TB rule refuses the budget suite's name.

    If this ever stops being true the scoping above is arguing with nobody
    and should be revisited.
    """
    from konsol.tb_dimension_model import is_legal_dimension_name

    assert not is_legal_dimension_name(BUDGET_ONLY_NAME)
