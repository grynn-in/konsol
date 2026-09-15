"""Ownership Period: the deal fields belong to the deal documents (konsolidat#198 P8).

A Business Combination (acquisition) or Business Disposal fills
``acquisition_date, is_first_acquisition, acquisition_price,
fair_value_adjustment, is_disposal, disposal_date, disposal_price`` on the
Ownership Period it creates or closes, under ``frappe.flags.from_business_combination``.
Typed by hand they would be a second, unreviewed source for the same figures,
so the form shows them read-only and ``validate()`` refuses a change that does
not come from a deal document (or from a patch).
"""
import datetime
import importlib.util
import json
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "ownership_period")

DEAL_FIELDS = (
    "acquisition_date", "is_first_acquisition", "acquisition_price",
    "fair_value_adjustment", "is_disposal", "disposal_date", "disposal_price",
)
#: Who fills which field (PR #202 review, finding 9): a Business Combination
#: creates the period and sets the acquisition figures; a Business Disposal
#: closes an existing period and sets the disposal figures.
ACQUISITION_FIELDS = ("acquisition_date", "is_first_acquisition", "acquisition_price", "fair_value_adjustment")
DISPOSAL_FIELDS = ("is_disposal", "disposal_date", "disposal_price")
ACQUISITION_DESCRIPTION = "Set by the Business Combination / Business Disposal that created this period."
DISPOSAL_DESCRIPTION = "Set by the Business Disposal that closed this period."
SENTENCE = ("Record the acquisition as a Business Combination (or the disposal as a "
            "Business Disposal); these fields are filled from it.")


def _meta():
    with open(os.path.join(DOCTYPE_DIR, "ownership_period.json")) as f:
        return json.load(f)


def _src():
    with open(os.path.join(DOCTYPE_DIR, "ownership_period.py")) as f:
        return f.read()


def _method_body(src, name):
    return src.split(f"def {name}")[1].split("\n    def ")[0]


# --- JSON -----------------------------------------------------------------

def test_the_seven_deal_fields_are_read_only_and_say_who_sets_them():
    """The description names the document that fills the field: a disposal
    closes a period that already exists, it does not create one."""
    fields = {f["fieldname"]: f for f in _meta()["fields"]}
    assert set(ACQUISITION_FIELDS) | set(DISPOSAL_FIELDS) == set(DEAL_FIELDS)
    for fn in DEAL_FIELDS:
        assert fn in fields, f"missing field {fn}"
        assert fields[fn].get("read_only") == 1, f"{fn} must be read_only"
    for fn in ACQUISITION_FIELDS:
        assert fields[fn].get("description") == ACQUISITION_DESCRIPTION, f"{fn} description"
    for fn in DISPOSAL_FIELDS:
        assert fields[fn].get("description") == DISPOSAL_DESCRIPTION, f"{fn} description"


def test_the_ownership_fields_stay_editable():
    """Only the deal figures move to the deal documents; the ownership itself
    (node, dates, share, method) is still declared here."""
    fields = {f["fieldname"]: f for f in _meta()["fields"]}
    for fn in ("consolidation_group", "data_area_id", "effective_date", "end_date",
               "ownership_pct", "consolidation_method"):
        assert not fields[fn].get("read_only"), f"{fn} must stay editable"


# --- source -----------------------------------------------------------------

def test_validate_guards_the_deal_fields():
    src = _src()
    assert "_validate_deal_fields_untouched" in _method_body(src, "validate")
    body = _method_body(src, "_validate_deal_fields_untouched")
    assert "frappe.flags.from_business_combination" in body
    assert "frappe.flags.in_patch" in body
    assert "get_doc_before_save" in body
    assert SENTENCE in src
    for fn in DEAL_FIELDS:
        assert f'"{fn}"' in src


# --- stub-frappe: the guard itself ------------------------------------------

class _Refused(Exception):
    pass


def _getdate(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def _load(flags, next_start=None, first_period_affected=None, gate=None):
    """The controller loaded by path against a minimal frappe: ``flags`` as
    given, ``throw`` raising _Refused, ``getdate`` real enough for dates.
    ``next_start`` is what ``frappe.db.get_value`` answers (the next period's
    effective_date), ``first_period_affected`` and ``gate`` replace the
    period_status functions of those names (default: None / no-op)."""
    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(**flags)
    frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: next_start)

    def throw(msg, *a, **k):
        raise _Refused(msg)

    frappe.throw = throw
    utils = types.ModuleType("frappe.utils")
    utils.getdate = _getdate
    frappe.utils = utils
    document_mod = types.ModuleType("frappe.model.document")

    class Document:
        def __init__(self, before=None, **fields):
            self.__dict__.update(fields)
            self._before = before

        def get(self, field, default=None):
            return self.__dict__.get(field, default)

        def get_doc_before_save(self):
            return self._before

    document_mod.Document = Document
    clickhouse = types.ModuleType("konsol.clickhouse")
    clickhouse.sync_doctype_after_commit = lambda *a, **k: None
    period_status = types.ModuleType("konsol.period_status")
    period_status.assert_open_between = gate or (lambda *a, **k: None)
    period_status.first_period_affected = first_period_affected or (lambda *a, **k: None)
    mods = {
        "frappe": frappe,
        "frappe.utils": utils,
        "frappe.model": types.ModuleType("frappe.model"),
        "frappe.model.document": document_mod,
        "konsol": types.ModuleType("konsol"),
        "konsol.clickhouse": clickhouse,
        "konsol.period_status": period_status,
    }
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("ownership_period_p8_under_test",
                                                      os.path.join(DOCTYPE_DIR, "ownership_period.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module


_SAVED = dict(
    name="OP-ZZG-ZZ01-2024-01-01", consolidation_group="ZZG", data_area_id="ZZ01",
    effective_date=datetime.date(2024, 1, 1), end_date=None, ownership_pct=80.0,
    consolidation_method="full",
    acquisition_date=datetime.date(2024, 1, 1), is_first_acquisition=1,
    acquisition_price=8300.0, fair_value_adjustment=930.0,
    is_disposal=0, disposal_date=None, disposal_price=0.0,
)


def _doc(module, before=_SAVED, **changes):
    saved = module.OwnershipPeriod(**before) if before is not None else None
    return module.OwnershipPeriod(before=saved, **{**(before or {}), **changes})


def _no_flags():
    return {"from_business_combination": False, "in_patch": False}


def test_a_hand_edit_of_any_deal_field_is_refused_by_the_sentence():
    module = _load(_no_flags())
    for field, value in (
        ("acquisition_date", "2024-02-01"),
        ("is_first_acquisition", 0),
        ("acquisition_price", 9000.0),
        ("fair_value_adjustment", 0),
        ("is_disposal", 1),
        ("disposal_date", "2025-06-30"),
        ("disposal_price", 1000.0),
    ):
        doc = _doc(module, **{field: value})
        with pytest.raises(_Refused) as e:
            doc._validate_deal_fields_untouched()
        assert str(e.value) == SENTENCE, field


def test_the_same_change_passes_from_a_deal_document_or_a_patch():
    for flags in ({"from_business_combination": True, "in_patch": False},
                  {"from_business_combination": False, "in_patch": True}):
        module = _load(flags)
        _doc(module, acquisition_price=9000.0, is_disposal=1,
             disposal_date="2025-06-30", disposal_price=1000.0)._validate_deal_fields_untouched()


def test_an_unchanged_document_passes_whatever_the_spelling():
    """The saved document comes back from the database (ints, floats, date
    objects, NULL as None); the form posts strings and "" — the same values,
    not a change."""
    module = _load(_no_flags())
    _doc(module, acquisition_date="2024-01-01", is_first_acquisition="1",
         acquisition_price="8300", fair_value_adjustment=930, is_disposal="0",
         disposal_date="", disposal_price=None)._validate_deal_fields_untouched()


def test_editing_the_ownership_itself_is_still_allowed():
    module = _load(_no_flags())
    _doc(module, ownership_pct=75.0, end_date="2025-12-31",
         consolidation_method="equity")._validate_deal_fields_untouched()


def test_a_new_period_may_not_carry_deal_figures_by_hand():
    module = _load(_no_flags())
    blank = {k: v for k, v in _SAVED.items() if k not in DEAL_FIELDS}
    # declared by hand with no deal figures: fine
    module.OwnershipPeriod(before=None, **blank)._validate_deal_fields_untouched()
    # blanks in either spelling are still blank
    module.OwnershipPeriod(before=None, **blank, acquisition_price=0, is_disposal="0",
                           disposal_date="")._validate_deal_fields_untouched()
    with pytest.raises(_Refused) as e:
        module.OwnershipPeriod(before=None, **blank, acquisition_price=8300.0)._validate_deal_fields_untouched()
    assert str(e.value) == SENTENCE
    # the deal document creates it under the flag
    module = _load({"from_business_combination": True, "in_patch": False})
    module.OwnershipPeriod(before=None, **_SAVED)._validate_deal_fields_untouched()


# --- cancel_span: the one rule for which periods a cancel changes (P19) --------
#
# PR #202 third review, finding 2: the Business Combination gated its cancel
# over ``acquisition_date -> end_date or today`` while the period's own
# before_cancel spanned ``effective_date -> end_date | the next period's start
# (exclusive) | open-ended``; where they disagreed the deal's refusal could
# pass and the period's then refuse with the wrong sentence, or the deal refuse
# where the period would allow. The span is computed once, here, and the deal
# asks the period for it.

def _period(module, **over):
    fields = dict(_SAVED, effective_date=datetime.date(2025, 3, 15), end_date=None)
    fields.update(over)
    return module.OwnershipPeriod(before=None, **fields)


def test_cancel_span_of_a_closed_period_ends_on_its_end_date():
    module = _load(_no_flags())
    span = _period(module, end_date=datetime.date(2025, 12, 31)).cancel_span()
    assert span == (datetime.date(2025, 3, 15), datetime.date(2025, 12, 31), False)


def test_cancel_span_of_an_open_ended_period_has_no_end():
    """No end and no later period: every later declared period is affected,
    which assert_open_between reads as ``end_date=None`` — not "today"."""
    module = _load(_no_flags())
    assert _period(module).cancel_span() == (datetime.date(2025, 3, 15), None, False)
    # "" is the form's spelling of blank
    assert _period(module, end_date="").cancel_span() == (datetime.date(2025, 3, 15), None, False)


def test_cancel_span_stops_at_the_next_periods_first_declared_period_exclusive():
    """A later period for the same node takes over from the first declared
    period starting on or after its effective_date; this period's cancel
    changes nothing from there on, so the span ends there, exclusive."""
    calls = []

    def first_period_affected(date):
        calls.append(date)
        return datetime.date(2026, 1, 1)

    module = _load(_no_flags(), next_start=datetime.date(2025, 12, 20),
                   first_period_affected=first_period_affected)
    # open-ended with a successor: the successor's first period bounds it
    assert _period(module).cancel_span() == (datetime.date(2025, 3, 15), datetime.date(2026, 1, 1), True)
    assert calls == [datetime.date(2025, 12, 20)]
    # an end_date on or after that start: the successor still bounds it
    assert _period(module, end_date=datetime.date(2026, 6, 30)).cancel_span() == (
        datetime.date(2025, 3, 15), datetime.date(2026, 1, 1), True)
    # an end_date before it: the period's own end is the earlier bound
    assert _period(module, end_date=datetime.date(2025, 11, 30)).cancel_span() == (
        datetime.date(2025, 3, 15), datetime.date(2025, 11, 30), False)
    # the successor starts past the last declared period (konsol#191 finding
    # 5): no declared start to bound by, the period's own end stays
    module = _load(_no_flags(), next_start=datetime.date(2027, 1, 1))
    assert _period(module, end_date=datetime.date(2026, 6, 30)).cancel_span() == (
        datetime.date(2025, 3, 15), datetime.date(2026, 6, 30), False)
    assert _period(module).cancel_span() == (datetime.date(2025, 3, 15), None, False)


def test_before_cancel_gates_exactly_the_cancel_span():
    """The period's own before_cancel is the span rule applied with the
    period's sentence; the Business Combination applies the same span with
    its own (test_business_combination.py)."""
    spans = []

    def gate(start, end=None, action="run", end_exclusive=False):
        spans.append((start, end, action, end_exclusive))

    module = _load(_no_flags(), next_start=datetime.date(2025, 12, 20),
                   first_period_affected=lambda date: datetime.date(2026, 1, 1), gate=gate)
    doc = _period(module)
    doc.before_cancel()
    start, end, exclusive = doc.cancel_span()
    assert spans == [(start, end, "cancel an ownership period", exclusive)]
    assert spans[0][:2] == (datetime.date(2025, 3, 15), datetime.date(2026, 1, 1)) and exclusive is True

    module = _load(_no_flags(), gate=gate)
    _period(module, end_date=datetime.date(2025, 12, 31)).before_cancel()
    assert spans[-1] == (datetime.date(2025, 3, 15), datetime.date(2025, 12, 31),
                         "cancel an ownership period", False)
