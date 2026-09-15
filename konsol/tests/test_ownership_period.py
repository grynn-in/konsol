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
DESCRIPTION = "Set by the Business Combination / Business Disposal that created this period."
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
    fields = {f["fieldname"]: f for f in _meta()["fields"]}
    for fn in DEAL_FIELDS:
        assert fn in fields, f"missing field {fn}"
        assert fields[fn].get("read_only") == 1, f"{fn} must be read_only"
        assert fields[fn].get("description") == DESCRIPTION, f"{fn} description"


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


def _load(flags):
    """The controller loaded by path against a minimal frappe: ``flags`` as
    given, ``throw`` raising _Refused, ``getdate`` real enough for dates."""
    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(**flags)
    frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: None)

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
    period_status.assert_open_between = lambda *a, **k: None
    period_status.first_period_affected = lambda *a, **k: None
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
