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
    """frappe.utils.getdate, near enough: a date, a datetime, an ISO date or
    datetime string, or the ``dd-mm-yyyy`` a form may post."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value)
    try:
        return datetime.date.fromisoformat(text[:10])
    except ValueError:
        return datetime.datetime.strptime(text[:10], "%d-%m-%Y").date()


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
    clickhouse.after_commit_once = lambda *a, **k: None
    period_status = types.ModuleType("konsol.period_status")
    period_status.assert_open_between = gate or (lambda *a, **k: None)
    period_status.first_period_affected = first_period_affected or (lambda *a, **k: None)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = [APP_DIR]   # the real pure konsol.fiscal_structure_model imports through the stub
    mods = {
        "frappe": frappe,
        "frappe.utils": utils,
        "frappe.model": types.ModuleType("frappe.model"),
        "frappe.model.document": document_mod,
        "konsol": konsol,
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
    owner_node="CG-ZZG-", owner_label="ZZG", owner_group="ZZG", owner_data_area_id=None,
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


def _row(name, effective, end=None, docstatus=1, group="ZZG", entity="ZZ01",
         owner="CG-ZZG-", owner_label="ZZG", pct=80.0, supersedes=None):
    return types.SimpleNamespace(name=name, consolidation_group=group, data_area_id=entity,
                                 owner_node=owner, owner_label=owner_label, ownership_pct=pct,
                                 effective_date=effective, end_date=end, docstatus=docstatus,
                                 supersedes=supersedes)


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


def _site(rows, nodes=None):
    """``_load`` with get_all / get_doc / exists over ``rows``: get_all honours
    the node, the owner, ``name !=`` and ``docstatus !=`` filters the
    controller sends. With ``nodes`` (name -> Consolidation Group row, see
    ``_TREE``), ``frappe.db.get_value`` resolves a node by filters (the owned
    node) or by name (the owner)."""
    module = _load(_no_flags())
    frappe = module.frappe
    log, calls = [], []
    if nodes is not None:
        frappe.db.get_value = _node_lookup(nodes, calls)

    def get_all(doctype, filters=None, fields=None, **k):
        calls.append(("get_all", filters, doctype))
        if doctype == "Consolidation Group":
            # the cycle walk's one read of every node (PR #459 finding C)
            assert nodes is not None, "a tree is needed to read the nodes"
            assert not filters, filters
            return [types.SimpleNamespace(**vars(n)) for n in nodes.values()]
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

    def sql(query, values=(), as_dict=False, **k):
        """The two statements the cycle check sends: the serialising lock on
        Ownership Period's tabDocType row, then (PR #459 finding C) ONE
        locking read of every non-cancelled link (``docstatus != 2``,
        optionally ``name != %s``). Anything else is a test's mistake."""
        calls.append(("sql", query, tuple(values)))
        if "`tabDocType`" in query:
            return []
        assert "`tabOwnership Period`" in query, query
        assert as_dict, "the controller reads links as dicts"
        assert "`docstatus` != 2" in query and "`owner_node`" in query and "`docstatus`" in query, query
        exclude = values[-1] if "`name` != %s" in query else None
        return [types.SimpleNamespace(**vars(r)) for r in rows
                if r.docstatus != 2 and r.name != exclude]

    frappe.get_all = get_all
    frappe.get_doc = get_doc
    frappe.db.sql = sql
    frappe.db.exists = lambda doctype, name, *a, **k: any(r.name == name for r in rows)
    return module, log, calls


_PRED = "OP-ZZG-ZZ01-2025-01-01"


def _change(module, **over):
    fields = dict(doctype="Ownership Period", name="OP-ZZG-ZZ01-2025-10-01", consolidation_group="ZZG", data_area_id="ZZ01",
                  owner_node="CG-ZZG-", owner_label="ZZG",
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


# --- F8 (#290): the node check, by behaviour (was a source grep in
# test_consolidation.py) ------------------------------------------------------

def _node_site(nodes):
    """frappe.db.get_value for Consolidation Group: ``nodes`` maps
    (group, entity) → parent. A blank entity must be asked as ["is", "not set"]
    (a blank Link is NULL; ownership_period._BLANK)."""
    module = _load(_no_flags())
    asked = []

    def get_value(doctype, filters, fields, as_dict=False):
        assert doctype == "Consolidation Group", doctype
        # PR #449 review, finding 8: the owned node is read as the
        # ``_NODE_FIELDS`` row ``owner_refusal`` documents, through node_by_key.
        assert fields == module._NODE_FIELDS and as_dict, (fields, as_dict)
        asked.append(filters)
        entity = filters["data_area_id"]
        entity = "" if entity == ["is", "not set"] else entity
        key = (filters["consolidation_group"], entity)
        if key not in nodes:
            return None
        return types.SimpleNamespace(name=f"CG-{key[0]}-{key[1]}", consolidation_group=key[0],
                                     data_area_id=entity or None, parent_consolidation_group=nodes[key])

    module.frappe.db = types.SimpleNamespace(get_value=get_value)
    return module, asked


def _node_refusal(module, **fields):
    try:
        module.OwnershipPeriod(**fields)._validate_node_exists()
    except _Refused as e:
        return str(e)
    raise AssertionError("not refused")


def test_f8_a_typo_group_is_refused_by_the_sentence():
    module, _ = _node_site({("ZZG", "ZZ01"): "CG-ZZG-"})
    assert _node_refusal(module, consolidation_group="ZZ_TYPO", data_area_id="ZZ01") == (
        "No Consolidation Group node 'ZZ_TYPO' / entity 'ZZ01'. Create the node first — "
        "an Ownership Period for a node that does not exist is read by nothing.")
    assert _node_refusal(module, consolidation_group="ZZ_TYPO", data_area_id=None) == (
        "No Consolidation Group node 'ZZ_TYPO' / the group node. Create the node first — "
        "an Ownership Period for a node that does not exist is read by nothing.")


def test_f8_a_real_node_passes_and_a_blank_entity_is_asked_as_not_set():
    module, asked = _node_site({("ZZG", "ZZ01"): "CG-ZZG-", ("ZZSUB", ""): "CG-ZZG-"})
    module.OwnershipPeriod(consolidation_group="ZZG", data_area_id="ZZ01")._validate_node_exists()
    module.OwnershipPeriod(consolidation_group="ZZSUB", data_area_id="")._validate_node_exists()
    assert asked[-1]["data_area_id"] == ["is", "not set"]


def test_f8_a_root_node_takes_no_period():
    module, _ = _node_site({("ZZG", ""): None})
    assert _node_refusal(module, consolidation_group="ZZG", data_area_id="") == (
        "'ZZG' is a root group: nobody owns the top of a hierarchy, so it is 100% by "
        "construction and an Ownership Period on it would never be applied.")


# --- the warehouse's Date range, by behaviour (was a source grep in
# test_consolidation.py; PR #459 finding J) ------------------------------------

def test_a_date_outside_the_warehouse_range_is_refused_by_sentence_at_both_bounds():
    """ClickHouse Date holds 1970-01-01..2149-06-06 and clamps silently, so a
    period dated outside it would say one thing in Frappe and another in the
    warehouse. The range is fiscal_structure_model's, imported, never a
    copy (PR #449 finding 7)."""
    from konsol.fiscal_structure_model import MAX_DATE, MIN_DATE
    module = _whole_site()
    assert (module._CH_DATE_MIN, module._CH_DATE_MAX) == (MIN_DATE.isoformat(), MAX_DATE.isoformat())
    for field, value in (("effective_date", "1900-01-01"), ("end_date", "2200-01-01"),
                         ("effective_date", "1969-12-31"), ("end_date", "2149-06-07")):
        doc = module.OwnershipPeriod(before=None, **_declared(**{"effective_date": "2024-01-01", field: value}))
        sentence = _refusal(doc, "_validate_dates_representable")
        assert sentence.startswith(f"{field} {value} is outside the range the warehouse can store "
                                   f"({MIN_DATE.isoformat()} to {MAX_DATE.isoformat()})"), sentence
        assert "silently clamped" in sentence
    # both bounds themselves are representable
    module.OwnershipPeriod(before=None, **_declared(effective_date=MIN_DATE.isoformat(),
                                                    end_date=MAX_DATE.isoformat()))._validate_dates_representable()
    # the old "open" sentinel is translated to blank, not refused
    doc = module.OwnershipPeriod(before=None, **_declared(end_date="9999-12-31"))
    doc._validate_dates_representable()
    assert doc.end_date is None


# --- #429: proportional is offered, and the warehouse computes it as full ------
#
# gold_consolidated_trial_balance excludes only ``equity`` and ``none`` and
# computes group_amount = translated × pct and nci_amount = translated × (1 − pct)
# with no method branch, so a period declared ``proportional`` consolidates as a
# full subsidiary with NCI — the venturer's share of each line, with no NCI, is
# what proportional means. Per the 17 Sep principle (refuse what makes a number
# wrong) validate() refuses it by name until a model computes it; the Select
# keeps the option because #117 decides whether to implement or drop it.

def _whole_site():
    """``_site`` over the ``_TREE`` nodes, so ``validate()`` runs end to end."""
    module, _, _ = _site([], nodes=_TREE)
    return module


def _declared(**over):
    """A period declared by hand: ``_SAVED`` without the deal figures."""
    return dict({k: v for k, v in _SAVED.items() if k not in DEAL_FIELDS},
                doctype="Ownership Period", **over)


def _cg(name, group, entity, parent):
    return types.SimpleNamespace(name=name, consolidation_group=group, data_area_id=entity,
                                 parent_consolidation_group=parent)


#: Two hierarchies: ZZG (root group node; entity nodes ZZ01, ZZ02; sub-group
#: ZZSUB with entity node ZZ03) and ZZOTHER (another root).
_TREE = {n.name: n for n in (
    _cg("CG-ZZG-", "ZZG", None, None),
    _cg("CG-ZZG-ZZ01", "ZZG", "ZZ01", "CG-ZZG-"),
    _cg("CG-ZZG-ZZ02", "ZZG", "ZZ02", "CG-ZZG-"),
    _cg("CG-ZZSUB-", "ZZSUB", None, "CG-ZZG-"),
    _cg("CG-ZZSUB-ZZ03", "ZZSUB", "ZZ03", "CG-ZZSUB-"),
    _cg("CG-ZZOTHER-", "ZZOTHER", None, None),
    _cg("CG-ZZOTHER-ZZ09", "ZZOTHER", "ZZ09", "CG-ZZOTHER-"),
)}


def _node_lookup(nodes, calls):
    """``frappe.db.get_value`` for Consolidation Group over ``nodes``: by a
    filters dict (group + entity, a blank entity asked as ["is", "not set"])
    or by name. Anything else answers None."""

    def get_value(doctype, filters, fields=None, as_dict=False, **k):
        calls.append(("get_value", doctype, filters, fields))
        if doctype != "Consolidation Group":
            return None
        if isinstance(filters, dict):
            entity = filters["data_area_id"]
            entity = None if entity == ["is", "not set"] else entity
            hits = [n for n in nodes.values()
                    if n.consolidation_group == filters["consolidation_group"]
                    and (n.data_area_id or None) == entity]
            node = hits[0] if hits else None
        else:
            node = nodes.get(filters)
        if node is None:
            return None
        assert as_dict, "the controller reads nodes as dicts"
        return types.SimpleNamespace(**vars(node))

    return get_value


def test_429_proportional_is_refused_by_validate_naming_the_method_and_the_issue():
    module = _whole_site()
    with pytest.raises(_Refused) as e:
        module.OwnershipPeriod(before=None, **_declared(consolidation_method="proportional")).validate()
    message = str(e.value)
    assert "proportional" in message.lower(), message
    assert "konsol#429" in message, message
    assert "full" in message and "equity" in message, message


def test_429_full_equity_and_none_still_pass_validate():
    module = _whole_site()
    for method in ("full", "equity", "none"):
        module.OwnershipPeriod(before=None, **_declared(consolidation_method=method)).validate()


# --- #117 option A (Deepak Pai, 7 Nov 2026), pair 1 task A: the link names its
# owner -----------------------------------------------------------------------
#
# Ownership Period is the link of the ownership graph: it names the OWNED node
# (consolidation_group, data_area_id) as before, and now the OWNER node, a Link
# to a Consolidation Group node (an entity node or a pure group node). The
# owner is pre-filled from the owned node's tree parent when blank: that is the
# one default in the design, visible on the form and named in the field's
# description. New rows are named by the owner (``owner_label``: the owner's
# entity code, else its group code) so two owners of one node from one date do
# not collide; existing names are not renamed. Validate refuses, by name: an
# owner that is the owned node or sits under another root (§1 refusal 1), and
# links into one node whose overlapping windows sum past 100% (§1 refusal 4).
# Gaps/overlaps are per (owner, owned) edge. The cycle refusal is task B.

OWNER_FIELDS = ("owner_node", "owner_label", "owner_group", "owner_data_area_id")


def test_117_owner_node_is_a_required_visible_editable_link_naming_its_prefill():
    fields = {f["fieldname"]: f for f in _meta()["fields"]}
    order = [f["fieldname"] for f in _meta()["fields"]]
    f = fields["owner_node"]
    assert f["fieldtype"] == "Link" and f["options"] == "Consolidation Group"
    assert f["label"] == "Owner Node"
    assert f.get("reqd") == 1, "an owner is always required"
    assert not f.get("read_only") and not f.get("hidden"), "visible and editable"
    assert f.get("in_list_view") == 1
    assert order.index("tab_ownership") < order.index("owner_node") < order.index("consolidation_group")
    desc = f.get("description", "")
    assert "pre-filled" in desc.lower() and "parent" in desc.lower(), desc


def test_117_the_derived_owner_fields_are_read_only_and_system_set():
    fields = {f["fieldname"]: f for f in _meta()["fields"]}
    for fn in ("owner_label", "owner_group", "owner_data_area_id"):
        assert fields[fn].get("read_only") == 1, fn
        assert not fields[fn].get("reqd"), fn
    assert fields["owner_label"]["fieldtype"] == "Data"
    assert fields["owner_group"]["fieldtype"] == "Data"
    assert fields["owner_data_area_id"]["fieldtype"] == "Link"
    assert fields["owner_data_area_id"]["options"] == "Entity"


def test_117_new_rows_are_named_by_the_owner_and_old_names_are_kept():
    src = _src()
    assert "rename_doc" not in src, "the 329 existing names are not renamed"
    assert "_resolve_owner" in _method_body(src, "before_insert"), "owner_label is set before naming"
    assert "_resolve_owner" in _method_body(src, "before_validate")


def _named(module, **over):
    doc = module.OwnershipPeriod(before=None, **_declared(name=None, **over))
    doc.autoname()
    return doc.name


def test_117_a_new_row_is_named_owner_owned_date_so_two_sub_groups_of_one_owner_differ():
    """PR #449 review, finding 3: ``format:OP-{owner_label}-{data_area_id}-
    {effective_date}`` dropped the owned node's code, so two sub-group
    periods (blank entity) from one owner and date collided. The controller
    names the row ``OP-{owner}-{owned}-{date}`` with the owned label the
    entity code, else the group code; an entity-owned row's name is what the
    format gave (``OP-ZZG-ZZ01-2024-01-01``), so nothing else changes."""
    module = _load(_no_flags())
    s = _named(module, owner_label="ZZG", consolidation_group="ZZSUB", data_area_id=None,
               effective_date="2026-01-01")
    t = _named(module, owner_label="ZZG", consolidation_group="ZZSUB2", data_area_id=None,
               effective_date="2026-01-01")
    assert (s, t) == ("OP-ZZG-ZZSUB-2026-01-01", "OP-ZZG-ZZSUB2-2026-01-01")
    assert _named(module, owner_label="ZZG", consolidation_group="ZZG", data_area_id="ZZ01",
                  effective_date=datetime.date(2024, 1, 1)) == "OP-ZZG-ZZ01-2024-01-01"
    assert _named(module, owner_label="ZZ01", consolidation_group="ZZSUB", data_area_id="ZZ03",
                  effective_date="2026-07-01") == "OP-ZZ01-ZZ03-2026-07-01"
    # the method names the row; no `format:` autoname that could drop a field
    assert "autoname" not in _meta(), _meta().get("autoname")


def test_117_autoname_writes_the_date_as_iso_whatever_shape_it_was_posted_in():
    """PR #459 re-review finding F: the name took ``effective_date`` as typed,
    so a posted ``dd-mm-yyyy`` or a value with a time component gave a
    differently shaped name for the same link and date, and the collision
    guard the name is compared no longer held."""
    module = _load(_no_flags())
    for posted in (datetime.date(2026, 7, 1), datetime.datetime(2026, 7, 1, 0, 0), "2026-07-01",
                   "2026-07-01 00:00:00", "01-07-2026"):
        assert _named(module, owner_label="ZZG", consolidation_group="ZZSUB", data_area_id=None,
                      effective_date=posted) == "OP-ZZG-ZZSUB-2026-07-01", posted


def test_117_autoname_refuses_by_sentence_when_the_owner_has_no_label():
    """Finding F: a blank ``owner_label`` gave ``OP-None-…``. The owner is
    resolved first; when it still has no label (the Owner Node names no node,
    or is blank and the owned node has no tree parent) the save is refused by
    sentence, never named after None."""
    module = _whole_site()
    doc = module.OwnershipPeriod(before=None, **_declared(name=None, owner_node="CG-ZZ_TYPO-",
                                                          owner_label=None, owner_group=None))
    assert _refusal(doc, "autoname") == (
        "Cannot name this Ownership Period: Owner Node CG-ZZ_TYPO- is not a Consolidation Group "
        "node, so there is no owner label to name it by. Choose the node that holds the share.")
    assert doc.get("name") is None
    doc = module.OwnershipPeriod(before=None, **_declared(name=None, consolidation_group="ZZ_TYPO",
                                                          owner_node=None, owner_label=None, owner_group=None))
    assert _refusal(doc, "autoname") == (
        "Cannot name this Ownership Period: it has no Owner Node and `ZZ01` has no tree parent "
        "to pre-fill one from, so there is no owner label to name it by. Choose the node that holds the share.")
    # a label the owner resolves to on the spot is used
    doc = module.OwnershipPeriod(before=None, **_declared(name=None, owner_node="CG-ZZG-ZZ02",
                                                          owner_label=None, owner_group=None))
    doc.autoname()
    assert doc.name == "OP-ZZ02-ZZ01-2024-01-01"
    # and no effective date is refused too, never named after today
    doc = module.OwnershipPeriod(before=None, **_declared(name=None, effective_date=None))
    assert "Effective Date" in _refusal(doc, "autoname")


def test_117_the_field_map_names_the_owner_columns_after_data_area_id():
    """The warehouse columns task C adds to epm_staging.ownership_periods
    (fixed names: owner_group, owner_data_area_id, after data_area_id). The
    Link and the label are Frappe's; they are not synced."""
    module = _load(_no_flags())
    ch = module.OwnershipPeriod.CH_FIELD_MAP
    assert ch["owner_group"] == "owner_group"
    assert ch["owner_data_area_id"] == "owner_data_area_id"
    cols = list(ch)
    assert cols[cols.index("data_area_id") + 1:cols.index("data_area_id") + 3] == ["owner_group", "owner_data_area_id"]
    assert "owner_node" not in ch and "owner_label" not in ch
    fields = {f["fieldname"] for f in _meta()["fields"]}
    assert set(ch.values()) <= fields


def test_117_a_blank_owner_is_prefilled_from_the_tree_parent_and_labelled():
    module, _, _ = _site([], nodes=_TREE)
    doc = module.OwnershipPeriod(before=None, **_declared(owner_node=None, owner_label=None,
                                                          owner_group=None))
    doc.before_insert()
    assert doc.owner_node == "CG-ZZG-", "the owned node's tree parent"
    assert (doc.owner_group, doc.owner_data_area_id, doc.owner_label) == ("ZZG", None, "ZZG")
    # a group node owned by the root: the same default
    doc = module.OwnershipPeriod(before=None, **_declared(consolidation_group="ZZSUB", data_area_id=None,
                                                          owner_node=""))
    doc.before_insert()
    assert doc.owner_node == "CG-ZZG-" and doc.owner_label == "ZZG"
    # no node: nothing to pre-fill from, and no error here (validate refuses by name)
    doc = module.OwnershipPeriod(before=None, **_declared(consolidation_group="ZZ_TYPO", owner_node=None))
    doc.before_insert()
    assert not doc.owner_node


def test_117_a_given_owner_is_kept_and_an_entity_owner_is_labelled_by_its_code():
    module, _, _ = _site([], nodes=_TREE)
    doc = module.OwnershipPeriod(before=None, **_declared(consolidation_group="ZZSUB", data_area_id="ZZ03",
                                                          owner_node="CG-ZZG-ZZ01"))
    doc.before_insert()
    assert doc.owner_node == "CG-ZZG-ZZ01", "a chosen owner is not overwritten by the default"
    assert (doc.owner_group, doc.owner_data_area_id, doc.owner_label) == ("ZZG", "ZZ01", "ZZ01")
    # before_validate re-derives the three from the owner on every save, so an
    # edited owner never leaves a stale label or warehouse column behind
    doc.owner_node = "CG-ZZG-ZZ02"
    doc.before_validate()
    assert (doc.owner_group, doc.owner_data_area_id, doc.owner_label) == ("ZZG", "ZZ02", "ZZ02")


def _refusal(doc, method="validate"):
    try:
        getattr(doc, method)()
    except _Refused as e:
        return str(e)
    raise AssertionError("not refused")


def test_117_an_owner_that_is_the_owned_node_is_refused_by_name():
    module = _whole_site()
    doc = module.OwnershipPeriod(before=None, **_declared(owner_node="CG-ZZG-ZZ01"))
    assert _refusal(doc) == (
        "`ZZ01` cannot own itself: Owner Node CG-ZZG-ZZ01 is the owned node. "
        "Name the node that holds the share.")


def test_117_an_owner_under_another_root_is_refused_by_name():
    module = _whole_site()
    for owner in ("CG-ZZOTHER-", "CG-ZZOTHER-ZZ09"):
        doc = module.OwnershipPeriod(before=None, **_declared(owner_node=owner))
        assert _refusal(doc) == (
            f"Owner Node {owner} is under root CG-ZZOTHER-, not under CG-ZZG- like `ZZ01`: "
            f"an owner is a node of the same hierarchy as the node it owns."), owner


def test_117_an_owner_that_is_not_a_node_is_refused_by_name():
    module = _whole_site()
    doc = module.OwnershipPeriod(before=None, **_declared(owner_node="CG-ZZ_TYPO-"))
    assert _refusal(doc) == "Owner Node CG-ZZ_TYPO- is not a Consolidation Group node."


def test_117_the_parent_a_sibling_or_a_grandparent_may_own():
    module = _whole_site()
    module.OwnershipPeriod(before=None, **_declared(owner_node="CG-ZZG-")).validate()
    module.OwnershipPeriod(before=None, **_declared(owner_node="CG-ZZG-ZZ02")).validate()
    module.OwnershipPeriod(before=None, **_declared(consolidation_group="ZZSUB", data_area_id="ZZ03",
                                                    owner_node="CG-ZZG-")).validate()
    # a group node may be owned by an entity node beside it
    module.OwnershipPeriod(before=None, **_declared(consolidation_group="ZZSUB", data_area_id=None,
                                                    owner_node="CG-ZZG-ZZ01")).validate()


# refusal 4: Σ of the links into one node over overlapping windows ≤ 100

_EC = dict(consolidation_group="ZZSUB", data_area_id="ZZ03")   # the owned node, `ZZ03`


def _link(name, owner, label, pct, start, end=None, **over):
    row = _row(name, start, end=end, group="ZZSUB", entity="ZZ03", owner=owner, owner_label=label, pct=pct)
    for k, v in over.items():
        setattr(row, k, v)
    return row


def _new_link(module, owner, pct, start, end=None, **over):
    fields = dict(_declared(**_EC, name=None, owner_node=owner, ownership_pct=pct,
                            effective_date=start, end_date=end), **over)
    return module.OwnershipPeriod(before=None, **fields)


def test_117_total_issued_into_one_node_may_not_exceed_100_by_the_sentence():
    """The design's sentence (§1 refusal 4), with the holders listed largest
    first and the window where they all hold."""
    module, _, _ = _site([
        _link("OP-ZZ01-ZZ03-2026-01-01", "CG-ZZG-ZZ01", "ZZ01", 60, _D(2026, 1, 1)),
        _link("OP-ZZG-ZZ03-2026-01-01", "CG-ZZG-", "ZZG", 20, _D(2026, 1, 1)),
    ], nodes=_TREE)
    doc = _new_link(module, "CG-ZZG-ZZ02", 30, "2026-01-01")
    assert _refusal(doc) == (
        "`ZZ03` is already 80% owned in 2026-01-01..open (`ZZ01` 60%, `ZZG` 20%); "
        "this 30% would exceed 100%.")
    # exactly 100 is the whole node, and allowed
    _new_link(module, "CG-ZZG-ZZ02", 20, "2026-01-01").validate()


def test_117_the_window_named_is_where_the_overlapping_links_all_hold():
    module, _, _ = _site([
        _link("OP-ZZ01-ZZ03-2026-01-01", "CG-ZZG-ZZ01", "ZZ01", 60, _D(2026, 1, 1)),
        _link("OP-ZZG-ZZ03-2026-07-01", "CG-ZZG-", "ZZG", 30, _D(2026, 7, 1), _D(2026, 12, 31)),
    ], nodes=_TREE)
    doc = _new_link(module, "CG-ZZG-ZZ02", 20, "2026-01-01")
    assert _refusal(doc) == (
        "`ZZ03` is already 90% owned in 2026-07-01..2026-12-31 (`ZZ01` 60%, `ZZG` 30%); "
        "this 20% would exceed 100%.")
    # the new link ends before the second holder starts: only the first counts
    _new_link(module, "CG-ZZG-ZZ02", 20, "2026-01-01", "2026-06-30").validate()
    # a fractional share is written as typed
    doc = _new_link(module, "CG-ZZG-ZZ02", 10.5, "2026-07-01")
    assert _refusal(doc) == (
        "`ZZ03` is already 90% owned in 2026-07-01..2026-12-31 (`ZZ01` 60%, `ZZG` 30%); "
        "this 10.5% would exceed 100%.")


def test_117_total_issued_ignores_cancelled_links_and_the_superseded_predecessor():
    module, _, _ = _site([
        _link("OP-ZZ01-ZZ03-2026-01-01", "CG-ZZG-ZZ01", "ZZ01", 60, _D(2026, 1, 1)),
        _link("OP-ZZG-ZZ03-2026-01-01", "CG-ZZG-", "ZZG", 60, _D(2026, 1, 1), docstatus=2),
    ], nodes=_TREE)
    _new_link(module, "CG-ZZG-ZZ02", 40, "2026-01-01").validate()
    # a change to ZZ01's 60% supersedes it: the predecessor's 60% is not counted twice
    module, _, _ = _site([
        _link("OP-ZZ01-ZZ03-2026-01-01", "CG-ZZG-ZZ01", "ZZ01", 60, _D(2026, 1, 1)),
        _link("OP-ZZG-ZZ03-2026-01-01", "CG-ZZG-", "ZZG", 30, _D(2026, 1, 1)),
    ], nodes=_TREE)
    _new_link(module, "CG-ZZG-ZZ01", 70, "2026-07-01", supersedes="OP-ZZ01-ZZ03-2026-01-01",
              superseded_end_date=None).validate()
    # but a second holder pushing past 100 with the predecessor gone is still refused
    doc = _new_link(module, "CG-ZZG-ZZ01", 71, "2026-07-01", supersedes="OP-ZZ01-ZZ03-2026-01-01",
                    superseded_end_date=None)
    assert _refusal(doc) == (
        "`ZZ03` is already 30% owned in 2026-07-01..open (`ZZG` 30%); this 71% would exceed 100%.")


def test_117_a_strangers_draft_neither_cuts_its_predecessor_nor_counts_for_the_total():
    """PR #459 re-review finding D (revising #449 finding 4). The sweep let
    ANY other non-cancelled draft's ``supersedes`` end its predecessor, so a
    third party's period was accepted past 100% on the strength of a draft
    that may still be deleted. Only THIS document's own ``supersedes`` and
    SUBMITTED successors cut a predecessor, and a stranger's draft is not a
    link yet, so it is not counted either: its own approval re-runs this
    check against whatever is submitted by then. ZZ01 holds 60% open; a
    stranger's draft of 30% from 2027-01-01 supersedes it; ZZ02's 70% from
    2027-01-01 is refused (60 + 70), 40% passes (60 + 40)."""
    a = _link("OP-ZZ01-ZZ03-2026-01-01", "CG-ZZG-ZZ01", "ZZ01", 60, _D(2026, 1, 1))
    stranger = _link("OP-ZZ01-ZZ03-2027-01-01", "CG-ZZG-ZZ01", "ZZ01", 30, _D(2027, 1, 1),
                     docstatus=0, supersedes=a.name)
    module, _, _ = _site([a, stranger], nodes=_TREE)
    doc = _new_link(module, "CG-ZZG-ZZ02", 70, "2027-01-01")
    assert _refusal(doc) == (
        "`ZZ03` is already 60% owned in 2027-01-01..open (`ZZ01` 60%); this 70% would exceed 100%.")
    _new_link(module, "CG-ZZG-ZZ02", 40, "2027-01-01").validate()
    # the self case: this document's own supersedes cuts its predecessor (A 60,
    # own change 70 superseding A, another holder's 30: 30 + 70 = 100)
    other = _link("OP-ZZG-ZZ03-2026-01-01", "CG-ZZG-", "ZZG", 30, _D(2026, 1, 1))
    module, _, _ = _site([a, other], nodes=_TREE)
    _new_link(module, "CG-ZZG-ZZ01", 70, "2027-01-01", supersedes=a.name, superseded_end_date=None).validate()


def test_117_a_draft_change_is_checked_against_the_submitted_graph_when_it_is_approved():
    """#449 finding 4 revised by #459 finding D: while ZZ01's change (70% from
    2027-01-01, superseding its open 60%) is a draft, ZZ02's 30% from
    2026-06-01 is checked against the submitted graph alone (60 + 30 = 90,
    accepted; 41 is refused by the 60%), never against 60 + 70. The change's
    own approval then runs this check with ZZ02's share submitted: 70 + 30 =
    100 passes, 70 + 31 does not."""
    a = _link("OP-ZZ01-ZZ03-2026-01-01", "CG-ZZG-ZZ01", "ZZ01", 60, _D(2026, 1, 1))
    change = _link("OP-ZZ01-ZZ03-2027-01-01", "CG-ZZG-ZZ01", "ZZ01", 70, _D(2027, 1, 1),
                   docstatus=0, supersedes=a.name)
    module, _, _ = _site([a, change], nodes=_TREE)
    _new_link(module, "CG-ZZG-ZZ02", 30, "2026-06-01").validate()
    doc = _new_link(module, "CG-ZZG-ZZ02", 41, "2026-06-01")
    assert _refusal(doc) == (
        "`ZZ03` is already 60% owned in 2026-06-01..open (`ZZ01` 60%); this 41% would exceed 100%.")
    # approval of the change: validate runs with docstatus 1 and ZZ02's 31% submitted
    zz02 = _link("OP-ZZ02-ZZ03-2026-06-01", "CG-ZZG-ZZ02", "ZZ02", 31, _D(2026, 6, 1))
    module, _, _ = _site([a, zz02], nodes=_TREE)
    doc = _new_link(module, "CG-ZZG-ZZ01", 70, "2027-01-01", name=change.name, docstatus=1,
                    supersedes=a.name, superseded_end_date=None)
    assert _refusal(doc) == (
        "`ZZ03` is already 31% owned in 2027-01-01..open (`ZZ02` 31%); this 70% would exceed 100%.")
    zz02.ownership_pct = 30
    module, _, _ = _site([a, zz02], nodes=_TREE)
    _new_link(module, "CG-ZZG-ZZ01", 70, "2027-01-01", name=change.name, docstatus=1,
              supersedes=a.name, superseded_end_date=None).validate()


def test_117_when_several_successors_name_one_predecessor_the_earliest_cut_wins():
    """PR #459 re-review finding E: the cuts were a dict keyed by predecessor,
    so with two successors naming one predecessor the last by ordering won.
    Two SUBMITTED successors of one predecessor is a state the overlap check
    refuses, so this models rows written before it existed, whose end dates
    were never written: the predecessor ends the day before the EARLIEST
    successor. ZZ01 60% open; successors 65% from 2026-07-01 and 70% from
    2027-01-01 both name it; ZZ02's 30% over 2026-08-01..2026-12-31 meets
    65 only (95), not 60 + 65 (155)."""
    a = _link("OP-ZZ01-ZZ03-2026-01-01", "CG-ZZG-ZZ01", "ZZ01", 60, _D(2026, 1, 1))
    early = _link("OP-ZZ01-ZZ03-2026-07-01", "CG-ZZG-ZZ01", "ZZ01", 65, _D(2026, 7, 1), supersedes=a.name)
    late = _link("OP-ZZ01-ZZ03-2027-01-01", "CG-ZZG-ZZ01", "ZZ01", 70, _D(2027, 1, 1), supersedes=a.name)
    module, _, _ = _site([a, early, late], nodes=_TREE)
    _new_link(module, "CG-ZZG-ZZ02", 30, "2026-08-01", "2026-12-31").validate()
    doc = _new_link(module, "CG-ZZG-ZZ02", 36, "2026-08-01", "2026-12-31")
    assert _refusal(doc) == (
        "`ZZ03` is already 65% owned in 2026-08-01..open (`ZZ01` 65%); this 36% would exceed 100%.")


def test_117_gaps_and_overlaps_are_checked_per_owner_owned_edge():
    """Two owners may hold the same node over the same dates: those are two
    links. One owner twice over the same dates is still an overlap."""
    module, _, calls = _site([
        _link("OP-ZZ01-ZZ03-2026-01-01", "CG-ZZG-ZZ01", "ZZ01", 60, _D(2026, 1, 1)),
    ], nodes=_TREE)
    _new_link(module, "CG-ZZG-", 20, "2026-01-01")._check_no_gaps_or_overlaps()
    filters = [c[1] for c in calls if c[0] == "get_all"][-1]
    assert filters["owner_node"] == "CG-ZZG-", "the edge, not the node"
    assert filters["consolidation_group"] == "ZZSUB" and filters["data_area_id"] == "ZZ03"
    with pytest.raises(_Refused) as e:
        _new_link(module, "CG-ZZG-ZZ01", 20, "2026-06-01")._check_no_gaps_or_overlaps()
    assert str(e.value).startswith("Ownership period overlaps with OP-ZZ01-ZZ03-2026-01-01")


def test_117_a_cancel_restores_within_the_edge_only():
    """The later periods a restore may collide with are the same edge's; a
    second owner's link over those dates is not in the way."""
    other_owner = _link("OP-ZZG-ZZ03-2026-01-01", "CG-ZZG-", "ZZG", 20, _D(2026, 1, 1))
    pred = _link("OP-ZZ01-ZZ03-2025-01-01", "CG-ZZG-ZZ01", "ZZ01", 60, _D(2025, 1, 1), _D(2025, 9, 30))
    module, log, _ = _site([pred, other_owner], nodes=_TREE)
    _new_link(module, "CG-ZZG-ZZ01", 70, "2025-10-01", docstatus=2, name="OP-ZZ01-ZZ03-2025-10-01",
              supersedes=pred.name, superseded_end_date=None).on_cancel()
    assert log == [(pred.name, "end_date", None)]


def test_117_validate_runs_the_owner_and_total_checks_after_the_node_check():
    body = _method_body(_src(), "validate")
    steps = [line.strip() for line in body.splitlines() if line.strip().startswith("self._")]
    assert steps.index("self._validate_node_exists()") < steps.index("self._validate_owner()")
    assert steps.index("self._check_no_gaps_or_overlaps()") < steps.index("self._check_total_issued()")


# --- #117 pair 1 task B: a link that closes a cycle is refused by name (§3) ----
#
# From the OWNED node, follow outgoing non-cancelled links (owner → owned,
# docstatus != 2, undated: a dated cycle is still a cycle) and refuse when the
# owner node is reached, naming every node on the path. Two links saved
# concurrently could each pass that walk under REPEATABLE READ, so the check
# first takes the serialising lock the group uses for its difference account
# (``SELECT … FROM tabDocType WHERE name = 'Ownership Period' FOR UPDATE``,
# consolidation_group.py:183) and reads the links FOR UPDATE, which sees the
# latest committed rows. The self-link is refusal 1's (task A). The build-time
# backstop (``build_rows`` raising on a repeated node) is task C's DFS.

CYCLE_TAIL = " Cross-holdings are refused until a treasury-share rule is decided (konsol#117)."


def _cycle_sentence(*labels):
    return "This link would close a cycle: " + " → ".join(f"`{l}`" for l in labels) + "." + CYCLE_TAIL


def _edge(name, owner, owner_label, group, entity, start=_D(2026, 1, 1), end=None, **over):
    """An existing link ``owner`` → (group, entity)."""
    return _row(name, start, end=end, docstatus=over.pop("docstatus", 1), group=group, entity=entity,
                owner=owner, owner_label=owner_label, pct=over.pop("pct", 80.0))


#: ZZ01 owns ZZ02; ZZ02 owns ZZ03 (a chain under the ZZG root).
_ZZ01_OWNS_ZZ02 = _edge("OP-ZZ01-ZZ02-2026-01-01", "CG-ZZG-ZZ01", "ZZ01", "ZZG", "ZZ02")
_ZZ02_OWNS_ZZ03 = _edge("OP-ZZ02-ZZ03-2026-01-01", "CG-ZZG-ZZ02", "ZZ02", "ZZSUB", "ZZ03")


def _closing_link(module, owner, group, entity, pct=20, **over):
    """A new link ``owner`` → (group, entity) under validate."""
    fields = dict(_declared(name=None, consolidation_group=group, data_area_id=entity, owner_node=owner,
                            ownership_pct=pct, effective_date="2026-01-01", end_date=None), **over)
    return module.OwnershipPeriod(before=None, **fields)


def test_117b_a_link_closing_a_two_node_cycle_is_refused_by_the_sentence():
    """Shape 6 (§4): ZZ01 holds ZZ02; ZZ02 taking a share of ZZ01 is the
    cross-holding, named owner → owned → … → owner."""
    module, _, _ = _site([_ZZ01_OWNS_ZZ02], nodes=_TREE)
    doc = _closing_link(module, "CG-ZZG-ZZ02", "ZZG", "ZZ01")
    assert _refusal(doc) == _cycle_sentence("ZZ02", "ZZ01", "ZZ02")


def test_117b_a_three_node_cycle_names_every_node_on_the_path():
    module, _, _ = _site([_ZZ01_OWNS_ZZ02, _ZZ02_OWNS_ZZ03], nodes=_TREE)
    doc = _closing_link(module, "CG-ZZSUB-ZZ03", "ZZG", "ZZ01")
    assert _refusal(doc) == _cycle_sentence("ZZ03", "ZZ01", "ZZ02", "ZZ03")
    # a group node on the cycle is named by its group code
    sub_owns_zz01 = _edge("OP-ZZSUB-ZZ01-2026-01-01", "CG-ZZSUB-", "ZZSUB", "ZZG", "ZZ01")
    module, _, _ = _site([sub_owns_zz01], nodes=_TREE)
    doc = _closing_link(module, "CG-ZZG-ZZ01", "ZZSUB", None)
    assert _refusal(doc) == _cycle_sentence("ZZ01", "ZZSUB", "ZZ01")


def test_117b_a_dated_cycle_is_still_a_cycle():
    """The walk is undated: a link that ended years ago still closes the ring."""
    old = _edge("OP-ZZ01-ZZ02-2019-01-01", "CG-ZZG-ZZ01", "ZZ01", "ZZG", "ZZ02",
                start=_D(2019, 1, 1), end=_D(2019, 12, 31))
    module, _, _ = _site([old], nodes=_TREE)
    doc = _closing_link(module, "CG-ZZG-ZZ02", "ZZG", "ZZ01")
    assert _refusal(doc) == _cycle_sentence("ZZ02", "ZZ01", "ZZ02")


def test_117b_a_chain_and_a_direct_plus_indirect_holding_pass():
    module, _, _ = _site([_ZZ01_OWNS_ZZ02, _ZZ02_OWNS_ZZ03], nodes=_TREE)
    # the root takes ZZ01: the chain ZZG → ZZ01 → ZZ02 → ZZ03 has no ring
    _closing_link(module, "CG-ZZG-", "ZZG", "ZZ01").validate()
    # shape 1: ZZ01 also holds ZZ03 directly; two paths, no cycle
    _closing_link(module, "CG-ZZG-ZZ01", "ZZSUB", "ZZ03").validate()
    # a sibling with no links at all
    _closing_link(module, "CG-ZZG-ZZ02", "ZZSUB", None).validate()


def test_117b_a_cancelled_link_does_not_count_but_a_draft_does():
    cancelled = _edge("OP-ZZ01-ZZ02-2026-01-01", "CG-ZZG-ZZ01", "ZZ01", "ZZG", "ZZ02", docstatus=2)
    module, _, _ = _site([cancelled], nodes=_TREE)
    _closing_link(module, "CG-ZZG-ZZ02", "ZZG", "ZZ01").validate()
    draft = _edge("OP-ZZ01-ZZ02-2026-01-01", "CG-ZZG-ZZ01", "ZZ01", "ZZG", "ZZ02", docstatus=0)
    module, _, _ = _site([draft], nodes=_TREE)
    doc = _closing_link(module, "CG-ZZG-ZZ02", "ZZG", "ZZ01")
    assert _refusal(doc) == _cycle_sentence("ZZ02", "ZZ01", "ZZ02")


def test_117b_a_ring_through_a_tree_edge_no_link_names_is_refused():
    """``build_rows`` owns a node no link names by its tree parent, so that
    implicit edge closes a ring as surely as a link does (PR #449 review,
    finding 2). Tree ZZG > ZZSUB > ZZ03 with no period on ZZSUB or ZZ03;
    ZZ03 holds ZZ01: a share of ZZSUB for ZZ01 would ring ZZ01 → ZZSUB →
    ZZ03 → ZZ01 (the first edge implicit) and raise OwnershipCycle at every
    reconcile. It is refused here instead, by the same sentence."""
    zz03_owns_zz01 = _edge("OP-ZZ03-ZZ01-2026-01-01", "CG-ZZSUB-ZZ03", "ZZ03", "ZZG", "ZZ01")
    module, _, _ = _site([zz03_owns_zz01], nodes=_TREE)
    doc = _closing_link(module, "CG-ZZG-ZZ01", "ZZSUB", None)
    assert _refusal(doc) == _cycle_sentence("ZZ01", "ZZSUB", "ZZ03", "ZZ01")


def test_117b_a_submitted_link_into_the_tree_child_replaces_the_implicit_edge_a_draft_does_not():
    """The implicit edge exists only while no SUBMITTED link names the child:
    that is what ``build_rows`` reads (``_links``: docstatus 1). A draft into
    the child is not yet a link there, so the ring through the tree stands
    until it is submitted; refusing it now is what keeps the reconcile green."""
    zz03_owns_zz01 = _edge("OP-ZZ03-ZZ01-2026-01-01", "CG-ZZSUB-ZZ03", "ZZ03", "ZZG", "ZZ01")
    zz02_owns_zz03 = _edge("OP-ZZ02-ZZ03-2026-01-01", "CG-ZZG-ZZ02", "ZZ02", "ZZSUB", "ZZ03")
    module, _, _ = _site([zz03_owns_zz01, zz02_owns_zz03], nodes=_TREE)
    _closing_link(module, "CG-ZZG-ZZ01", "ZZSUB", None).validate()
    draft = _edge("OP-ZZ02-ZZ03-2026-01-01", "CG-ZZG-ZZ02", "ZZ02", "ZZSUB", "ZZ03", docstatus=0)
    module, _, _ = _site([zz03_owns_zz01, draft], nodes=_TREE)
    doc = _closing_link(module, "CG-ZZG-ZZ01", "ZZSUB", None)
    assert _refusal(doc) == _cycle_sentence("ZZ01", "ZZSUB", "ZZ03", "ZZ01")


def test_117b_a_submitted_link_with_no_owner_into_the_tree_child_leaves_the_tree_edge_standing():
    """PR #459 re-review finding B. ``build_rows`` drops a node's tree edge
    only when a submitted link names the node AND that link's owner is set
    and exists; the validator dropped it for any submitted row naming the
    node, so a ring through such a tree edge passed validate and raised
    OwnershipCycle at every reconcile. Tree ZZG > ZZSUB > ZZ03, ZZG > ZZ01; a
    submitted period into ZZ03 with a NULL owner (a row the owner patch has
    not reached); link ZZ03 → ZZ01; then ZZ01 → ZZSUB is refused: ZZ01 →
    ZZSUB → ZZ03 (the tree edge) → ZZ01."""
    ownerless = _edge("OP-ZZSUB-ZZ03-2024-01-01", None, None, "ZZSUB", "ZZ03", start=_D(2024, 1, 1))
    zz03_owns_zz01 = _edge("OP-ZZ03-ZZ01-2026-01-01", "CG-ZZSUB-ZZ03", "ZZ03", "ZZG", "ZZ01", docstatus=0)
    module, _, _ = _site([ownerless, zz03_owns_zz01], nodes=_TREE)
    doc = _closing_link(module, "CG-ZZG-ZZ01", "ZZSUB", None)
    assert _refusal(doc) == _cycle_sentence("ZZ01", "ZZSUB", "ZZ03", "ZZ01")
    # a submitted link into ZZ03 whose owner node is GONE keeps the tree edge too
    dangling = _edge("OP-ZZGONE-ZZ03-2024-01-01", "CG-ZZG-ZZGONE", "ZZGONE", "ZZSUB", "ZZ03", start=_D(2024, 1, 1))
    module, _, _ = _site([dangling, zz03_owns_zz01], nodes=_TREE)
    doc = _closing_link(module, "CG-ZZG-ZZ01", "ZZSUB", None)
    assert _refusal(doc) == _cycle_sentence("ZZ01", "ZZSUB", "ZZ03", "ZZ01")


def test_117b_editing_a_link_does_not_see_its_own_stored_row():
    """Reversing a link in place (ZZ02 → ZZ01 becomes ZZ01 → ZZ02): the stored
    row is this document, not a second link, so there is no ring."""
    stored = _edge("OP-ZZ02-ZZ01-2026-01-01", "CG-ZZG-ZZ02", "ZZ02", "ZZG", "ZZ01")
    module, _, calls = _site([stored], nodes=_TREE)
    _closing_link(module, "CG-ZZG-ZZ01", "ZZG", "ZZ02", name=stored.name).validate()
    reads = [c for c in calls if c[0] == "sql" and "`tabOwnership Period`" in c[1]]
    assert reads and all("`name` != %s" in c[1] and c[2][-1] == stored.name for c in reads), reads
    # a NEW document (no name yet) sends no name clause: `name != NULL` matches nothing
    module, _, calls = _site([stored], nodes=_TREE)
    _closing_link(module, "CG-ZZG-", "ZZG", "ZZ01").validate()
    reads = [c for c in calls if c[0] == "sql" and "`tabOwnership Period`" in c[1]]
    assert reads and all("`name` != %s" not in c[1] and "%s" not in c[1] for c in reads), reads


def _cycle_check_reads(rows, owner, group, entity):
    module, _, calls = _site(rows, nodes=_TREE)
    _closing_link(module, owner, group, entity).validate()
    return calls


def test_117b_the_check_locks_then_reads_the_links_once_for_update_and_the_tree_once_whatever_the_walk():
    """PR #459 re-review finding C. Under REPEATABLE READ a plain read in a
    transaction can miss rows committed after its first read (CLAUDE.md), so
    the links are read with ONE locking read after the serialising lock
    (consolidation_group.py:183 pattern) — never per visited node, and never a
    second FOR UPDATE over the submitted rows. The tree is read once, plainly
    (a concurrent tree edit is the group controller's to guard; the walk's
    race is over the links). The counts do not grow with the walk: a one-edge
    graph and a chain the walk follows through three nodes send the same
    statements."""
    short = _cycle_check_reads([_ZZ01_OWNS_ZZ02], "CG-ZZG-", "ZZSUB", "ZZ03")
    long = _cycle_check_reads([_ZZ01_OWNS_ZZ02, _ZZ02_OWNS_ZZ03,
                               _edge("OP-ZZ03-ZZSUB-2026-01-01", "CG-ZZSUB-ZZ03", "ZZ03", "ZZSUB", None)],
                              "CG-ZZG-", "ZZG", "ZZ01")
    for calls in (short, long):
        sqls = [c for c in calls if c[0] == "sql"]
        assert len(sqls) == 2, sqls
        lock, links = sqls
        assert lock[1] == "SELECT `name` FROM `tabDocType` WHERE `name` = %s FOR UPDATE", lock
        assert lock[2] == ("Ownership Period",)
        assert links[1].rstrip().endswith("FOR UPDATE"), links
        assert "`docstatus` != 2" in links[1] and "`owner_node`" in links[1] and "`docstatus`" in links[1], links
        assert "`owner_node` = %s" not in links[1], "no per-node read"
        tree_reads = [c for c in calls if c[0] == "get_all" and c[2] == "Consolidation Group"]
        assert len(tree_reads) == 1 and not tree_reads[0][1], tree_reads
    assert [c[1] for c in short if c[0] == "sql"] == [c[1] for c in long if c[0] == "sql"]


def test_117b_validate_checks_the_cycle_after_the_owner_and_before_the_windows():
    body = _method_body(_src(), "validate")
    steps = [line.strip() for line in body.splitlines() if line.strip().startswith("self._")]
    assert steps.index("self._validate_owner()") < steps.index("self._validate_no_cycle()")
    assert steps.index("self._validate_no_cycle()") < steps.index("self._check_no_gaps_or_overlaps()")
    assert CYCLE_TAIL.strip() in _src()


# --- #117 pair 1 F: the owner sentences are one helper the deals share -----------

def test_117_node_by_key_is_the_one_node_by_key_read_the_deals_share():
    """PR #449 review, finding 9: Business Combination and Business Disposal
    each copied ``_NODE_FIELDS`` and their own node lookup. One definition:
    they import ``node_by_key`` from here, and the name list lives here only
    (the one structural check kept, PR #459 finding J); what the read sends
    is asserted on the stubbed ``get_value`` call, not on source text."""
    module, _, calls = _site([], nodes=_TREE)
    node = module.node_by_key("ZZG", "ZZ01")
    assert (node.name, node.consolidation_group, node.data_area_id, node.parent_consolidation_group) == (
        "CG-ZZG-ZZ01", "ZZG", "ZZ01", "CG-ZZG-")
    assert calls[-1] == ("get_value", "Consolidation Group",
                         {"consolidation_group": "ZZG", "data_area_id": "ZZ01"}, module._NODE_FIELDS)
    assert module.node_by_key("ZZSUB", None).name == "CG-ZZSUB-", "a blank entity is the group's own node"
    assert calls[-1][2] == {"consolidation_group": "ZZSUB", "data_area_id": ["is", "not set"]}
    assert module.node_by_key("ZZSUB", "").name == "CG-ZZSUB-"
    assert module.node_by_key("ZZ_TYPO", "ZZ01") is None
    # the document's own node read is that read: same doctype, filters and fields
    calls.clear()
    owned = module.OwnershipPeriod(before=None, **_declared())._node()
    assert owned.name == "CG-ZZG-ZZ01"
    assert calls == [("get_value", "Consolidation Group",
                      {"consolidation_group": "ZZG", "data_area_id": "ZZ01"}, module._NODE_FIELDS)]
    # one definition of the node field list across the three controllers
    defined_in = []
    for ctl in ("ownership_period", "business_combination", "business_disposal"):
        with open(os.path.join(APP_DIR, "consolidation", "doctype", ctl, ctl + ".py")) as f:
            if "\n_NODE_FIELDS = " in f.read():
                defined_in.append(ctl)
    assert defined_in == ["ownership_period"], defined_in


def test_117f_owner_refusal_is_a_module_function_the_validate_uses():
    src = _src()
    assert "\ndef owner_refusal(" in src
    assert "owner_refusal(" in _method_body(src, "_validate_owner")


def test_117f_owner_refusal_names_the_callers_field_and_gives_none_when_sound():
    module, _, _ = _site([], nodes=_TREE)
    owned = module.OwnershipPeriod(before=None, **_declared())._node()   # CG-ZZG-ZZ01
    assert module.owner_refusal("CG-ZZG-", owned, "ZZ01") is None
    assert module.owner_refusal("CG-ZZG-ZZ02", owned, "ZZ01", field="Acquirer Node") is None
    assert module.owner_refusal("CG-ZZG-ZZ01", owned, "ZZ01", field="Acquirer Node") == (
        "`ZZ01` cannot own itself: Acquirer Node CG-ZZG-ZZ01 is the owned node. "
        "Name the node that holds the share.")
    assert module.owner_refusal("CG-ZZ_TYPO-", owned, "ZZ01", field="Disposer Node") == (
        "Disposer Node CG-ZZ_TYPO- is not a Consolidation Group node.")
    assert module.owner_refusal("CG-ZZOTHER-ZZ09", owned, "ZZ01") == (
        "Owner Node CG-ZZOTHER-ZZ09 is under root CG-ZZOTHER-, not under CG-ZZG- like `ZZ01`: "
        "an owner is a node of the same hierarchy as the node it owns.")
    # the owned node not there yet (a deal drafted before the tree has it): only
    # the owner's existence can be checked
    assert module.owner_refusal("CG-ZZG-", None, "ZZ01") is None
    assert module.owner_refusal("CG-ZZ_TYPO-", None, "ZZ01") == (
        "Owner Node CG-ZZ_TYPO- is not a Consolidation Group node.")


# --- a link rebuilds the closure (demo-ext-simple, 8 Nov 2026) ---------------

def _link_doc(module, docstatus):
    return module.OwnershipPeriod(dict(_SAVED), docstatus=docstatus, supersedes=None)


def _capture_after_commit(module):
    calls = []
    module.after_commit_once = lambda key, fn: calls.append((key, fn))
    module.OwnershipPeriod._end_predecessor = lambda self: None
    module.OwnershipPeriod._restore_predecessor = lambda self: None
    return calls


def test_submit_and_cancel_queue_the_closure_rebuild():
    """The closure (epm_staging.consolidation_ancestry) is built from the
    LINKS (konsol#117 pair 1), but only the group's own on_update rebuilt it:
    a link submitted after the tree existed left every path on the tree
    parent, so an owner that is another entity (Mini Holding AG owning its
    subsidiaries, demo-ext-simple 8 Nov 2026) resolved to 0% / method none
    and assert_ownership_chain_complete stopped the build until
    reconcile_all ran by hand. Submit and cancel now queue the same rebuild
    the group queues, under the same dedupe key."""
    module = _load({"in_install": False, "in_import": False, "in_migrate": False, "in_patch": False})
    calls = _capture_after_commit(module)
    _link_doc(module, 1).on_submit()
    assert [k for k, _ in calls] == [("consolidation_hierarchy",)]
    assert calls[0][1].__name__ == "_resync_closure"
    calls.clear()
    _link_doc(module, 2).on_cancel()
    assert [k for k, _ in calls] == [("consolidation_hierarchy",)]


def test_resync_closure_calls_the_groups_resync_staging():
    """The callback imports the group controller only when it runs (after the
    commit), so the link controller never imports the tree controller at load."""
    module = _load({"in_install": False, "in_import": False, "in_migrate": False, "in_patch": False})
    ran = []
    fake = types.ModuleType("konsol.consolidation.doctype.consolidation_group.consolidation_group")
    fake.ConsolidationGroup = types.SimpleNamespace(resync_staging=lambda: ran.append("resync"))
    names = ["konsol.consolidation", "konsol.consolidation.doctype",
             "konsol.consolidation.doctype.consolidation_group",
             "konsol.consolidation.doctype.consolidation_group.consolidation_group"]
    saved = {n: sys.modules.get(n) for n in names}
    for n in names[:-1]:
        sys.modules.setdefault(n, types.ModuleType(n))
    sys.modules[names[-1]] = fake
    try:
        module._resync_closure()
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    assert ran == ["resync"]
