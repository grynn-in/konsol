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


# --- O52: the ownership-change link (#305-Q1-1, plan-w5b.md §4d) ------------

SUPERSEDE_FIELDS = ("supersedes", "superseded_end_date")


def test_the_supersede_fields_exist_read_only_after_consolidation_method():
    """A change draft names the period it ends on approval; the predecessor's
    old end date is kept so a cancel can restore it. Both are set by the
    product, never typed, and sit right after consolidation_method."""
    order = [f["fieldname"] for f in _meta()["fields"]]
    fields = {f["fieldname"]: f for f in _meta()["fields"]}
    for fn in SUPERSEDE_FIELDS:
        assert fn in fields, f"missing field {fn}"
        assert fields[fn].get("read_only") == 1, f"{fn} must be read_only"
        assert not fields[fn].get("reqd"), f"{fn} must be optional"
    at = order.index("consolidation_method")
    assert order[at + 1:at + 3] == list(SUPERSEDE_FIELDS)
    sup = fields["supersedes"]
    assert sup["fieldtype"] == "Link" and sup["options"] == "Ownership Period"
    assert sup.get("description") == "The period this change ends on approval."
    assert not sup.get("hidden"), "supersedes is shown on the form"
    end = fields["superseded_end_date"]
    assert end["fieldtype"] == "Date"
    assert end.get("hidden") == 1, "superseded_end_date is hidden"


def test_the_supersede_fields_are_not_synced_to_the_warehouse():
    """Nothing new goes to ClickHouse: the fields are not in CH_FIELD_MAP."""
    module = _load(_no_flags())
    ch = module.OwnershipPeriod.CH_FIELD_MAP
    for fn in SUPERSEDE_FIELDS:
        assert fn not in ch, f"{fn} must not be synced"
        assert fn not in ch.values(), f"{fn} must not be a warehouse column"
    fields = {f["fieldname"] for f in _meta()["fields"]}
    assert set(ch) <= fields, "every synced field is declared"


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


# --- O53: a change ends its predecessor on approval (#305-Q1-1) ----------------
#
# Deepak Pai, 7 Oct 2026 (konsol#305 issuecomment-6031976783): approving an
# ownership change end-dates the period it supersedes to the change's
# effective_date - 1 day; a cancel restores the end date stored on the change
# (``superseded_end_date``, blank = open-ended); a change between two existing
# periods is refused. Rejected: Q1-2 (cancel and amend the current period), Q1-3
# (changes only through Business Combination / Disposal).

_D = datetime.date


def _row(name, effective, end=None, docstatus=1, group="ZZG", entity="ZZ01"):
    return types.SimpleNamespace(name=name, consolidation_group=group, data_area_id=entity,
                                 effective_date=effective, end_date=end, docstatus=docstatus)


class _Stored:
    """A stored Ownership Period as ``frappe.get_doc`` returns it: ``db_set``
    records the name, the field and the value."""

    def __init__(self, frappe, row, log):
        self.__dict__.update(vars(row))
        self._frappe, self._log = frappe, log

    def get(self, field, default=None):
        return self.__dict__.get(field, default)

    def db_set(self, field, value, *a, **k):
        self._log.append((self.name, field, value))
        setattr(self, field, value)


def _site(rows):
    """``_load`` with get_all / get_doc / exists over ``rows``: get_all honours
    the node, ``name !=`` and ``docstatus !=`` filters the controller sends."""
    module = _load(_no_flags())
    frappe = module.frappe
    log, calls = [], []

    def get_all(doctype, filters=None, fields=None, **k):
        calls.append(("get_all", filters))
        out = []
        for r in rows:
            ok = True
            for key, cond in (filters or {}).items():
                value = getattr(r, key)
                if isinstance(cond, list) and cond and cond[0] == "!=":
                    ok = ok and value != cond[1]
                elif isinstance(cond, list) and cond and cond[0] == "is":
                    ok = ok and not value
                elif isinstance(cond, list) and cond and cond[0] == ">":
                    ok = ok and value > cond[1]
                elif isinstance(cond, list):
                    raise AssertionError(f"unexpected filter {key} {cond}")
                else:
                    ok = ok and value == cond
            if ok:
                out.append(types.SimpleNamespace(**vars(r)))
        return out

    def get_doc(doctype, name, *a, **k):
        assert doctype == "Ownership Period"
        calls.append(("get_doc", name, k.get("for_update")))
        for r in rows:
            if r.name == name:
                return _Stored(frappe, r, log)
        raise AssertionError(f"no such period {name}")

    frappe.get_all = get_all
    frappe.get_doc = get_doc
    frappe.db.exists = lambda doctype, name, *a, **k: any(r.name == name for r in rows)
    return module, log, calls


_PRED = "OP-ZZG-ZZ01-2025-01-01"


def _change(module, **over):
    fields = dict(doctype="Ownership Period", name="OP-ZZG-ZZ01-2025-10-01", consolidation_group="ZZG", data_area_id="ZZ01",
                  effective_date="2025-10-01", end_date=None, ownership_pct=80.0,
                  consolidation_method="full", supersedes=_PRED, superseded_end_date=None, docstatus=0)
    fields.update(over)
    return module.OwnershipPeriod(before=None, **fields)


def test_a_change_superseding_an_open_ended_predecessor_validates():
    module, _, calls = _site([_row(_PRED, _D(2025, 1, 1))])
    _change(module)._check_no_gaps_or_overlaps()
    filters = calls[0][1]
    assert filters["consolidation_group"] == "ZZG" and filters["data_area_id"] == "ZZ01"
    assert filters["docstatus"] == ["!=", 2]
    # a predecessor with an end date after the change's start is superseded too
    module, _, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 12, 31))])
    _change(module, end_date="2025-12-31", superseded_end_date="2025-12-31")._check_no_gaps_or_overlaps()


def test_without_supersedes_a_new_period_still_overlaps():
    module, _, _ = _site([_row(_PRED, _D(2025, 1, 1))])
    with pytest.raises(_Refused) as e:
        _change(module, supersedes=None)._check_no_gaps_or_overlaps()
    assert str(e.value).startswith(f"Ownership period overlaps with {_PRED}")


def test_a_predecessor_of_another_node_is_refused_as_overlapping():
    other = _row("OP-ZZG-ZZ02-2025-01-01", _D(2025, 1, 1), entity="ZZ02")
    module, log, _ = _site([_row(_PRED, _D(2025, 1, 1)), other])
    with pytest.raises(_Refused) as e:
        _change(module, supersedes=other.name)._check_no_gaps_or_overlaps()
    assert str(e.value).startswith(f"Ownership period overlaps with {_PRED}")
    # the node has no period of its own: still refused, never end-dates ZZ02's
    module, log, _ = _site([other])
    with pytest.raises(_Refused) as e:
        _change(module, supersedes=other.name)._check_no_gaps_or_overlaps()
    assert other.name in str(e.value) and "covers" in str(e.value)
    assert log == []


def test_a_predecessor_that_starts_later_or_is_not_submitted_is_refused():
    module, _, _ = _site([_row(_PRED, _D(2025, 12, 1))])
    with pytest.raises(_Refused) as e:
        _change(module)._check_no_gaps_or_overlaps()
    assert str(e.value).startswith(f"Ownership period overlaps with {_PRED}")
    # same start day: not before, refused
    module, _, _ = _site([_row(_PRED, _D(2025, 10, 1))])
    with pytest.raises(_Refused) as e:
        _change(module)._check_no_gaps_or_overlaps()
    assert "overlaps" in str(e.value)
    # a draft predecessor is not a holding
    module, _, _ = _site([_row(_PRED, _D(2025, 1, 1), docstatus=0)])
    with pytest.raises(_Refused) as e:
        _change(module)._check_no_gaps_or_overlaps()
    assert "overlaps" in str(e.value)


def test_a_predecessor_that_does_not_cover_the_first_day_is_refused():
    """Ending it at effective_date - 1 would lengthen it, not end it."""
    module, _, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 6, 30))])
    with pytest.raises(_Refused) as e:
        _change(module)._check_no_gaps_or_overlaps()
    assert _PRED in str(e.value) and "covers" in str(e.value)


def test_a_change_between_two_existing_periods_is_refused():
    later = _row("OP-ZZG-ZZ01-2026-01-01", _D(2026, 1, 1))
    module, _, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 12, 31)), later])
    with pytest.raises(_Refused) as e:
        _change(module, end_date="2025-12-31", superseded_end_date="2025-12-31")._check_no_gaps_or_overlaps()
    assert later.name in str(e.value) and "between" in str(e.value)


def test_submit_ends_the_predecessor_the_day_before():
    module, log, calls = _site([_row(_PRED, _D(2025, 1, 1))])
    _change(module, docstatus=1).on_submit()
    assert log == [(_PRED, "end_date", _D(2025, 9, 30))]
    assert ("get_doc", _PRED, True) in calls, "the predecessor is read FOR UPDATE"
    # a stored end date that still matches
    module, log, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 12, 31))])
    _change(module, docstatus=1, end_date="2025-12-31", superseded_end_date="2025-12-31").on_submit()
    assert log == [(_PRED, "end_date", _D(2025, 9, 30))]


def test_submit_without_supersedes_touches_no_other_period():
    module, log, calls = _site([_row(_PRED, _D(2025, 1, 1))])
    _change(module, supersedes=None, docstatus=1).on_submit()
    assert log == [] and not [c for c in calls if c[0] == "get_doc"]


def test_submit_refuses_a_predecessor_whose_end_date_moved_since_the_draft():
    for stored, now in ((None, _D(2025, 11, 30)), ("2025-12-31", None), ("2025-12-31", _D(2025, 11, 30))):
        module, log, _ = _site([_row(_PRED, _D(2025, 1, 1), end=now)])
        with pytest.raises(_Refused) as e:
            _change(module, docstatus=1, superseded_end_date=stored).on_submit()
        msg = str(e.value)
        assert _PRED in msg and "changed since" in msg and "record the change again" in msg, msg
        assert log == []


def test_cancel_restores_the_stored_end_date():
    module, log, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 9, 30))])
    _change(module, docstatus=2).on_cancel()
    assert log == [(_PRED, "end_date", None)]
    module, log, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 9, 30))])
    _change(module, docstatus=2, superseded_end_date="2025-12-31").on_cancel()
    assert log == [(_PRED, "end_date", _D(2025, 12, 31))]
    # no supersedes: nothing to restore
    module, log, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 9, 30))])
    _change(module, docstatus=2, supersedes=None).on_cancel()
    assert log == []


def test_cancel_refuses_a_restore_that_would_overlap_a_later_period():
    """A later change superseded this one: restoring the predecessor open-ended
    would overlap it. That one is cancelled first."""
    later = _row("OP-ZZG-ZZ01-2026-01-01", _D(2026, 1, 1))
    module, log, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 9, 30)),
                            _row("OP-ZZG-ZZ01-2025-10-01", _D(2025, 10, 1), end=_D(2025, 12, 31)), later])
    with pytest.raises(_Refused) as e:
        _change(module, docstatus=2).on_cancel()
    assert later.name in str(e.value) and "first" in str(e.value)
    assert log == []


# --- R52g: review S6, S10, S16 (konsol#305 wave 5b) -----------------------------

_S6_CHANGE = "OP-ZZG-ZZ01-2025-07-01"


def test_s6_a_change_ends_where_its_predecessor_ended():
    """S6: a change supersedes the current period for the rest of its span, so
    its end date is the predecessor's stored end date (blank = open). An edited
    End Date on the draft is refused, so the effect's ``after.to`` is true by
    construction."""
    module, log, _ = _site([_row(_PRED, _D(2025, 1, 1))])
    with pytest.raises(_Refused) as e:
        _change(module, name=_S6_CHANGE, effective_date="2025-07-01",
                end_date="2025-09-30")._check_no_gaps_or_overlaps()
    assert str(e.value) == (
        f"{_S6_CHANGE} supersedes {_PRED}, so it ends where {_PRED} ended (open): "
        f"clear or correct End Date.")
    # a stored date and a different (or blank) end date: refused, naming the date
    module, _, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 12, 31))])
    for end in ("2025-09-30", None, ""):
        with pytest.raises(_Refused) as e:
            _change(module, name=_S6_CHANGE, effective_date="2025-07-01", end_date=end,
                    superseded_end_date="2025-12-31")._check_no_gaps_or_overlaps()
        assert str(e.value) == (
            f"{_S6_CHANGE} supersedes {_PRED}, so it ends where {_PRED} ended (2025-12-31): "
            f"clear or correct End Date."), end
    # matching: saves, whatever the spelling (blank equals blank, str equals date)
    module, _, _ = _site([_row(_PRED, _D(2025, 1, 1))])
    for end, stored in ((None, None), ("", None), (None, ""), ("", "")):
        _change(module, effective_date="2025-07-01", end_date=end,
                superseded_end_date=stored)._check_no_gaps_or_overlaps()
    module, _, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 12, 31))])
    _change(module, effective_date="2025-07-01", end_date=_D(2025, 12, 31),
            superseded_end_date="2025-12-31")._check_no_gaps_or_overlaps()
    assert log == []


def test_s6_without_supersedes_an_end_date_is_unchanged():
    module, _, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 6, 30))])
    _change(module, supersedes=None, effective_date="2025-07-01",
            end_date="2025-09-30")._check_no_gaps_or_overlaps()


def test_s16_cancel_never_writes_a_cancelled_predecessor():
    """S16: a predecessor cancelled since gets no end date back, as a deleted
    one gets none."""
    module, log, calls = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 9, 30), docstatus=2)])
    _change(module, docstatus=2).on_cancel()
    assert log == []
    module, log, _ = _site([_row(_PRED, _D(2025, 1, 1), end=_D(2025, 9, 30), docstatus=2)])
    _change(module, docstatus=2, superseded_end_date="2025-12-31").on_cancel()
    assert log == []
    # a deleted predecessor: still nothing
    module, log, _ = _site([])
    _change(module, docstatus=2).on_cancel()
    assert log == []


def test_s10_the_dead_ownership_change_flag_is_gone():
    """S10: ``frappe.flags.from_ownership_change`` was set and read nowhere."""
    assert "from_ownership_change" not in _src()
