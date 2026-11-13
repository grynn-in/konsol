"""A Published budget dimension must also be a trial-balance dimension (BUD-8-2).

Decided by Deepak Pai on konsol#348 (BUD-8-2); scope from Q10 option b: the
rule fires when the Dimension is Published after the save, with
``in_budget = 1`` and ``in_trial_balance = 0``. A budget variance compares
budget against actuals like-for-like, so every dimension a budget is held on
must also exist on the trial balance.

Every consumer of the budget grain reads Published dimensions only
(``budget_grain.py``, ``api.py``, ``dbt_config.py``), so a Draft or Inactive
row reaches nothing and saves freely. That keeps ``unpublish()``, which works
through ``self.save()``, open for an offender: retiring it is one of the two
fixes. Rejected: refusing every save (Q10 option a), which would trap an
offender that could then not be unpublished.

The controller is loaded by file path with stubbed frappe/konsol modules, as
``test_dimension_name_validation.py`` does; the stub ``Document.save`` runs
``validate`` then ``on_update``, as Frappe's save does.
"""
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
    raise (exc or _ValidationError)(msg)


_REBUILDS = []


def _stub(name, **attrs):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


class _Flags(dict):
    """frappe._dict: attribute access, missing keys read as None."""

    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


class _Doc:  # stand-in for frappe.model.document.Document
    def save(self):
        self.validate()
        self.on_update()

    def get_doc_before_save(self):
        return None

    @property
    def flags(self):
        if "_flags" not in self.__dict__:
            self.__dict__["_flags"] = _Flags()
        return self.__dict__["_flags"]


_stub("frappe", whitelist=lambda *a, **k: (lambda fn: fn),
      throw=_throw, ValidationError=_ValidationError, flags=_Flags())
_stub("konsol", __path__=[_APP])
_stub("frappe.model")
_stub("frappe.model.document", Document=_Doc)
_stub("konsol.schema_lifecycle", check_epm_admin=lambda: None,
      apply_and_rebuild=lambda doc, action: _REBUILDS.append(action))

_spec = importlib.util.spec_from_file_location("dimension_budget_subset_under_test", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

#: Whatever frappe stub won the race in this process: catch its error class.
_ERR = sys.modules["frappe"].ValidationError

NAME = "dim_zz_budget_only"


def _doc(status="Published", in_budget=1, in_trial_balance=0, name=NAME):
    doc = _m.Dimension.__new__(_m.Dimension)
    doc.dimension_name = name
    doc.name = name
    doc.in_budget = in_budget
    doc.in_trial_balance = in_trial_balance
    doc.survives_close = 0
    doc.in_journal = 0
    doc.status = status
    return doc


def _refusal(doc):
    try:
        doc.validate()
    except _ERR as e:
        return str(e)
    raise AssertionError(f"{doc.dimension_name!r} was accepted; it must be refused")


def _accepts(doc):
    try:
        doc.validate()
    except _ERR as e:
        raise AssertionError(f"{doc.dimension_name!r} was refused: {e}")


def test_published_budget_only_dimension_is_refused_by_name():
    msg = _refusal(_doc())
    assert NAME in msg, msg


def test_the_refusal_states_both_fixes():
    msg = _refusal(_doc())
    # The form's labels, the only names of these settings an admin has seen.
    assert "tick Include in Trial Balance" in msg, msg
    assert "untick Include in Budget" in msg, msg


def test_published_with_both_flags_passes():
    _accepts(_doc(in_trial_balance=1))


def test_published_trial_balance_only_passes():
    _accepts(_doc(in_budget=0, in_trial_balance=1))


def test_draft_budget_only_passes():
    _accepts(_doc(status="Draft"))


def test_inactive_budget_only_passes():
    _accepts(_doc(status="Inactive"))


def test_unpublishing_a_published_budget_only_dimension_passes():
    """The retirement path: ``unpublish`` saves, and the save must succeed."""
    doc = _doc()
    del _REBUILDS[:]
    doc.unpublish()
    assert doc.status == "Inactive", doc.status
    assert _REBUILDS == ["Unpublish"], _REBUILDS


def test_publishing_a_draft_budget_only_dimension_is_refused():
    """``publish`` sets Published, then saves: the save is refused."""
    doc = _doc(status="Draft")
    try:
        doc.publish()
    except _ERR as e:
        assert NAME in str(e), str(e)
    else:
        raise AssertionError("publish() of a budget-only dimension was accepted")


def test_the_flags_are_read_the_way_the_intake_reads_them():
    """Text "0" from REST/CSV is unticked: a TEXT "0" TB flag is still refused."""
    msg = _refusal(_doc(in_budget="1", in_trial_balance="0"))
    assert NAME in msg, msg
