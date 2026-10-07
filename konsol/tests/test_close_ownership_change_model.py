"""konsol#305 O51: ownership_change_model — the refusals of a proposed
ownership change and its structural effect (decision #305-4.2-1; #305-Q1-1,
Deepak Pai 7 Oct; sentences from archive/konsol-305-d2/wireframe-4.2.md as
confirmed). Pure: loaded by path, imports no frappe.
"""
import ast
import importlib.util
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "ownership_change_model.py")
_spec = importlib.util.spec_from_file_location("ownership_change_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

_OP_JSON = os.path.join(
    APP_DIR, "consolidation", "doctype", "ownership_period", "ownership_period.json")

NOT_SHOWN = "Goodwill, NCI and results are not previewed; they change at the next build."
FIRST_DAY_HEAD = ("Ownership changes take effect on the first day of a period: "
                  "the warehouse reads ownership on each period's first day. ")


def _month_end(y, m):
    nxt = (y + (m // 12), (m % 12) + 1)
    import datetime
    return (datetime.date(nxt[0], nxt[1], 1) - datetime.timedelta(days=1)).isoformat()


def _rows(statuses=None):
    """FY2024 P12 and FY2025 P01-P12, plus FY2026 P01-P03, as
    fiscal_calendar.fiscal_period_rows() gives them, with one Adjustment
    period (FY2025 P13) that must never count as a first day."""
    statuses = statuses or {}
    out = []

    def add(fy, fp, start, end, ptype="Regular"):
        out.append({
            "fiscal_year": fy, "fiscal_period": fp,
            "period_code": "P%02d" % fp, "period_label": "P%02d" % fp,
            "period_type": ptype, "start_date": start, "end_date": end,
            "quarter": (fp - 1) // 3 + 1 if fp <= 12 else 4,
            "status": statuses.get((fy, fp), "Open"),
        })

    add(2024, 12, "2024-12-01", "2024-12-31")
    for m in range(1, 13):
        add(2025, m, "2025-%02d-01" % m, _month_end(2025, m))
    add(2025, 13, "2025-12-31", "2025-12-31", ptype="Adjustment")
    for m in range(1, 4):
        add(2026, m, "2026-%02d-01" % m, _month_end(2026, m))
    return out


def _current(**kw):
    c = {"name": "OP-ECL_GROUP-ZZ5B1-2025-01-01", "effective_date": "2025-01-01",
         "end_date": None, "ownership_pct": 100.0, "consolidation_method": "full"}
    c.update(kw)
    return c


def _change(**kw):
    c = {"entity": "ZZ5B1", "effective_date": "2025-10-01",
         "ownership_pct": 80, "consolidation_method": "full"}
    c.update(kw)
    return c


def _sig(run, on="2025-10-04", by="Jane Doe"):
    """One signed run as ``ownership_change.context`` hands it over (O64)."""
    return {"run": run, "signed_on": on, "signed_by_name": by}


def _signed(*keys):
    """{key: signature} for each signed period key, each run named after its
    key and signed on 4 Oct 2025 by Jane Doe."""
    return {k: _sig("AR-%s-%s" % k) for k in keys}


def _problems(change=None, current="default", later=None, pending=None, rows=None):
    return M.problems(
        change if change is not None else _change(),
        _current() if current == "default" else current,
        later, pending,
        rows if rows is not None else _rows())


# --- METHODS -----------------------------------------------------------------

def test_methods_are_the_doctype_select_options():
    with open(_OP_JSON) as f:
        meta = json.load(f)
    field = [d for d in meta["fields"] if d.get("fieldname") == "consolidation_method"][0]
    assert tuple(field["options"].split("\n")) == M.METHODS
    assert M.METHODS == ("full", "proportional", "equity", "none")


# --- problems: each refusal, exact sentence ---------------------------------

def test_a_valid_change_has_no_problems():
    assert _problems() == []


def test_1_no_current_ownership():
    assert _problems(current=None) == [
        "ZZ5B1 has no ownership for FY2025 P10: record its first ownership in Desk "
        "(an acquisition is a Business Combination)."]


def test_2_not_a_first_day_names_the_period_and_the_next_first_day():
    got = _problems(change=_change(effective_date="2025-10-15"))
    assert got == [FIRST_DAY_HEAD + "2025-10-15 is inside FY2025 P10; choose "
                   "FY2025 P11, from 2025-11-01, or FY2025 P10, from 2025-10-01."]


def test_2_an_adjustment_period_start_is_not_a_first_day():
    # 2025-12-31 is P13's start (Adjustment) but inside Regular P12.
    got = _problems(change=_change(effective_date="2025-12-31"))
    assert got == [FIRST_DAY_HEAD + "2025-12-31 is inside FY2025 P12; choose "
                   "FY2026 P01, from 2026-01-01, or FY2025 P12, from 2025-12-01."]


def test_2_the_last_declared_period_has_no_next_first_day():
    got = _problems(change=_change(effective_date="2026-03-15"))
    assert got == [FIRST_DAY_HEAD + "2026-03-15 is inside FY2026 P03; choose "
                   "FY2026 P03, from 2026-03-01."]


def test_2_a_date_in_no_declared_period_is_refused_not_guessed():
    got = _problems(change=_change(effective_date="2027-05-01"), current=None)
    assert got == [
        "ZZ5B1 has no ownership for 2027-05-01: record its first ownership in Desk "
        "(an acquisition is a Business Combination).",
        "2027-05-01 is in no declared Regular fiscal period: an ownership "
        "change must start on the first day of one."]


def test_3_period_not_open():
    got = _problems(change=_change(effective_date="2024-12-01"),
                    current=_current(effective_date="2024-01-01"),
                    rows=_rows({(2024, 12): "Closed"}))
    assert got == ["FY2024 P12 is Closed: an ownership change must start in an Open period."]


def test_3_locked_period():
    got = _problems(rows=_rows({(2025, 10): "Locked"}))
    assert got == ["FY2025 P10 is Locked: an ownership change must start in an Open period."]


def test_4_pct_out_of_range_or_not_a_number():
    sentence = ["Ownership % must be a number from 0 to 100."]
    for bad in (-1, 100.01, "abc", None, "", True, "nan", float("nan"),
                "inf", float("inf"), float("-inf")):
        assert _problems(change=_change(ownership_pct=bad)) == sentence, bad
    for ok in (0, 100, "80", 33.3333, "0.5"):
        # current is 50 % so that 100 is a change, not "Nothing changes".
        assert _problems(change=_change(ownership_pct=ok),
                         current=_current(ownership_pct=50.0)) == [], ok


def test_5_method_not_declared():
    for bad in ("Full", "", None, "partial"):
        assert _problems(change=_change(consolidation_method=bad)) == [
            "Method must be one of full, proportional, equity, none."], bad


def test_6_nothing_changes():
    assert _problems(change=_change(ownership_pct="100.0")) == [
        "Nothing changes: ZZ5B1 is already 100 % full from 2025-01-01."]


def test_6_a_method_change_alone_is_a_change():
    assert _problems(change=_change(ownership_pct=100, consolidation_method="equity")) == []


def test_7_must_start_after_the_current_start():
    for cur_start in ("2025-10-01", "2025-11-01"):
        got = _problems(current=_current(effective_date=cur_start))
        assert got == ["The change must start after the current period's start (%s)."
                       % cur_start], cur_start


def test_8_a_later_period_exists():
    assert _problems(later="2026-01-01") == [
        "ZZ5B1 already has an ownership period from 2026-01-01: change or cancel that one first."]


def test_9_a_pending_change_exists():
    assert _problems(pending="OP-ECL_GROUP-ZZ5B1-2025-10-01") == [
        "A change for ZZ5B1 is already awaiting approval (OP-ECL_GROUP-ZZ5B1-2025-10-01): "
        "edit that draft."]


def test_refusals_come_in_order():
    got = _problems(
        change=_change(effective_date="2024-12-01", ownership_pct="nan",
                       consolidation_method="partial"),
        current=_current(effective_date="2024-12-01"),
        later="2026-01-01", pending="OP-X",
        rows=_rows({(2024, 12): "Closed"}))
    assert got == [
        "FY2024 P12 is Closed: an ownership change must start in an Open period.",
        "Ownership % must be a number from 0 to 100.",
        "Method must be one of full, proportional, equity, none.",
        "The change must start after the current period's start (2024-12-01).",
        "ZZ5B1 already has an ownership period from 2026-01-01: change or cancel that one first.",
        "A change for ZZ5B1 is already awaiting approval (OP-X): edit that draft.",
    ]
    got = _problems(change=_change(effective_date="2025-10-15"), current=None)
    assert got[0].startswith("ZZ5B1 has no ownership for FY2025 P10:")
    assert got[1].startswith(FIRST_DAY_HEAD)


def test_problems_accept_date_objects():
    import datetime
    got = _problems(change=_change(effective_date=datetime.date(2025, 10, 1)),
                    current=_current(effective_date=datetime.date(2025, 1, 1)))
    assert got == []


def test_a_change_without_an_entity_raises():
    ch = _change()
    del ch["entity"]
    try:
        _problems(change=ch)
    except KeyError:
        return
    raise AssertionError("a change with no entity must raise, never name 'None'")


# --- effect ------------------------------------------------------------------

def test_effect_100_full_to_80_full_from_p10_with_p11_signed():
    eff = M.effect(_change(), _current(), _rows(), {(2025, 11): _sig("AR-1")})
    assert eff == {
        "before": {"pct": 100.0, "method": "full", "from": "2025-01-01", "to": None},
        "after": {"pct": 80.0, "method": "full", "from": "2025-10-01", "to": None},
        "current_name": "OP-ECL_GROUP-ZZ5B1-2025-01-01",
        "current_ends": "2025-09-30",
        "first_period": "FY2025 P10",
        "periods": "FY2025 P10 onward (open-ended)",
        "resign": ["FY2025 P11"],
        "resign_detail": [{"period": "FY2025 P11", "signed_on": "2025-10-04",
                           "signed_by_name": "Jane Doe"}],
        "not_shown": NOT_SHOWN,
    }


def test_effect_resign_lists_signed_periods_at_or_after_the_first_in_calendar_order():
    signed = _signed((2026, 2), ("2025", "10"), (2025, 9), (2025, 12), (2026, 1))
    eff = M.effect(_change(), _current(), _rows(), signed)
    assert eff["resign"] == ["FY2025 P10", "FY2025 P12", "FY2026 P01", "FY2026 P02"]
    # resign_detail is parallel to resign, in the same calendar order.
    assert [d["period"] for d in eff["resign_detail"]] == eff["resign"]


def test_effect_no_signed_period():
    eff = M.effect(_change(), _current(), _rows(), {})
    assert eff["resign"] == []
    assert eff["resign_detail"] == []


def test_effect_bounded_current_names_the_last_period():
    eff = M.effect(_change(), _current(end_date="2026-03-31"), _rows(), ())
    assert eff["periods"] == "FY2025 P10 to FY2026 P03"
    assert eff["before"]["to"] == "2026-03-31"
    assert eff["after"]["to"] == "2026-03-31"
    assert eff["current_ends"] == "2025-09-30"


def test_effect_across_a_year_start():
    eff = M.effect(_change(effective_date="2026-01-01"), _current(), _rows(), ())
    assert eff["current_ends"] == "2025-12-31"
    assert eff["first_period"] == "FY2026 P01"


def test_effect_never_contains_an_amount():
    eff = M.effect(_change(), _current(), _rows(), _signed((2025, 11)))
    assert eff["not_shown"] == NOT_SHOWN
    flat = json.dumps(eff).lower()
    for word in ("amount", "goodwill_amount", "nci_amount", "balance", "value"):
        assert ('"%s"' % word) not in flat, word
    assert set(eff) == {"before", "after", "current_name", "current_ends", "first_period",
                        "periods", "resign", "resign_detail", "not_shown"}
    assert set(eff["before"]) == set(eff["after"]) == {"pct", "method", "from", "to"}


def test_effect_refuses_an_invalid_change_rather_than_guessing():
    cases = [
        (_change(), None),
        (_change(effective_date="2025-10-15"), _current()),
        (_change(ownership_pct="nan"), _current()),
        (_change(consolidation_method="partial"), _current()),
    ]
    for ch, cur in cases:
        try:
            M.effect(ch, cur, _rows(), ())
        except ValueError:
            continue
        raise AssertionError("effect must raise for %r / %r" % (ch, cur))


def test_effect_signed_key_outside_the_calendar_raises():
    try:
        M.effect(_change(), _current(), _rows(), _signed((2030, 1)))
    except ValueError:
        return
    raise AssertionError("a signed period missing from the calendar must raise")


def _rows_with_closing_and_opening():
    """_rows() plus a Closing FY2025 P14 on 2025-12-31 and an Opening FY2026
    P00 on 2026-01-01 (the same day as FY2026 P01), as a calendar declares
    them (fiscal_period.json: 0=Opening, 13=Closing, 14=Adjustment)."""
    rows = _rows()
    rows.append({"fiscal_year": 2025, "fiscal_period": 14, "period_code": "P14",
                 "period_label": "P14", "period_type": "Closing",
                 "start_date": "2025-12-31", "end_date": "2025-12-31",
                 "quarter": 4, "status": "Open"})
    rows.append({"fiscal_year": 2026, "fiscal_period": 0, "period_code": "P00",
                 "period_label": "P00", "period_type": "Opening",
                 "start_date": "2026-01-01", "end_date": "2026-01-01",
                 "quarter": 1, "status": "Open"})
    return rows


def test_effect_lists_a_signed_closing_period_without_raising():
    # The model lists every signed key it is given that starts on or after
    # the change. Which keys it is given is ownership_change's job (R52f:
    # only those signoff_gate.periods_marked_from marks, so never a Closing
    # period in practice); a non-Regular key it IS given never raises.
    eff = M.effect(_change(), _current(), _rows_with_closing_and_opening(),
                   _signed((2025, 14)))
    assert eff["resign"] == ["FY2025 P14"]


def test_effect_lists_a_signed_adjustment_period():
    eff = M.effect(_change(), _current(), _rows(), _signed((2025, 13)))
    assert eff["resign"] == ["FY2025 P13"]


def test_effect_orders_signed_periods_across_closing_and_opening():
    signed = _signed((2026, 1), (2025, 14), (2026, 0), (2025, 12), (2025, 13), (2025, 9))
    eff = M.effect(_change(), _current(), _rows_with_closing_and_opening(), signed)
    assert eff["resign"] == ["FY2025 P12", "FY2025 P13", "FY2025 P14",
                             "FY2026 P00", "FY2026 P01"]


def test_effect_skips_a_signed_non_regular_period_before_the_change():
    rows = _rows_with_closing_and_opening()
    eff = M.effect(_change(effective_date="2026-02-01"), _current(), rows,
                   _signed((2025, 14), (2026, 0), (2026, 2)))
    assert eff["resign"] == ["FY2026 P02"]


def test_effect_signed_key_outside_the_calendar_raises_naming_it():
    try:
        M.effect(_change(), _current(), _rows_with_closing_and_opening(),
                 _signed((2025, 14), (2030, 7)))
    except ValueError as e:
        assert "2030" in str(e) and "7" in str(e), str(e)
        return
    raise AssertionError("a signed period missing from the calendar must raise")


def test_effect_first_day_check_stays_regular_only():
    # The Opening FY2026 P00 starts 2026-01-01, but the first day is still
    # judged on Regular rows; a Closing period's start is never a first day.
    try:
        M.effect(_change(effective_date="2025-12-31"), _current(),
                 _rows_with_closing_and_opening(), ())
    except ValueError:
        return
    raise AssertionError("a Closing period's start must not count as a first day")


# --- O64: the predecessor's name and who signed each re-sign period ------------
# wireframe-4.2.md §1 ("FY2025 P11 (signed 4 Oct by Jane Doe)") and §3
# ("Ends OP-… on 2025-09-30"), confirmed as drawn by Deepak Pai 7 Oct.

def test_o64_current_name_is_the_predecessors_document_name():
    eff = M.effect(_change(), _current(name="OP-PRED-7"), _rows(), {})
    assert eff["current_name"] == "OP-PRED-7"


def test_o64_resign_detail_names_the_date_and_signer_of_a_signed_p11():
    signed = {(2025, 11): _sig("AR-11", on="2025-12-04", by="Jane Doe"),
              (2025, 9): _sig("AR-9", on="2025-10-02", by="Raj Patel")}
    eff = M.effect(_change(), _current(), _rows(), signed)
    assert eff["resign"] == ["FY2025 P11"]   # P09 is before the change
    assert eff["resign_detail"] == [
        {"period": "FY2025 P11", "signed_on": "2025-12-04", "signed_by_name": "Jane Doe"}]


def test_o64_signed_on_from_a_datetime_is_an_iso_date():
    import datetime
    signed = {(2025, 11): _sig("AR-11", on=datetime.datetime(2025, 12, 4, 17, 30))}
    eff = M.effect(_change(), _current(), _rows(), signed)
    assert eff["resign_detail"][0]["signed_on"] == "2025-12-04"


def _o64_raises(signed):
    try:
        M.effect(_change(), _current(), _rows(), signed)
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError for %r" % (signed,))


def test_o64_failure_path_a_listed_run_without_a_signer_name_raises_naming_the_run():
    for by in (None, ""):
        msg = _o64_raises({(2025, 11): _sig("AR-NOSIGNER", by=by)})
        assert "AR-NOSIGNER" in msg and "FY2025 P11" in msg, msg


def test_o64_failure_path_a_listed_run_without_a_signed_date_raises_naming_the_run():
    for on in (None, ""):
        msg = _o64_raises({(2025, 11): _sig("AR-NODATE", on=on)})
        assert "AR-NODATE" in msg and "FY2025 P11" in msg, msg


def test_o64_failure_path_a_bare_key_with_no_signature_raises_naming_the_period():
    """A key list carries no signer: a listed period is never shown with a
    blank or guessed signature."""
    for signed in ([(2025, 11)], {(2025, 11): None}, {(2025, 11): "AR-11"}):
        msg = _o64_raises(signed)
        assert "FY2025 P11" in msg, msg


def test_o64_an_unlisted_run_before_the_change_needs_no_signer():
    """Only the periods the effect lists are shown, so only they must carry a
    signer; a signed P09 before the change is not read for one."""
    eff = M.effect(_change(), _current(), _rows(), {(2025, 9): _sig("AR-9", by=None)})
    assert eff["resign"] == [] and eff["resign_detail"] == []


def test_o64_resign_detail_never_shows_a_user_id():
    eff = M.effect(_change(), _current(), _rows(), _signed((2025, 11)))
    assert set(eff["resign_detail"][0]) == {"period", "signed_on", "signed_by_name"}


def test_effect_uses_the_one_period_name_helper():
    src = open(_PATH).read()
    assert "period_name.py" in src
    assert "FY%d" not in src


def test_module_imports_no_frappe():
    tree = ast.parse(open(_PATH).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith(("frappe", "konsol")) for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("frappe", "konsol"))


# --- R52f (review S5): the model no longer promises non-Regular periods ------

def test_r52f_the_model_does_not_promise_to_list_non_regular_periods():
    """ownership_change passes only the signed keys an approval marks
    (signoff_gate.periods_marked_from, Regular only); the model's docstring
    and comments must not promise Closing/Opening/Adjustment periods
    (the O54a text this row reverses)."""
    with open(M.__file__, encoding="utf-8") as fh:
        src = fh.read()
    assert "O54a" not in src, "the O54a promise is still in the model"
    assert "periods_marked_from" in src, "the model must name the rule its caller filters by"
