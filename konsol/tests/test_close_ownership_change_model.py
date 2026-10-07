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
    eff = M.effect(_change(), _current(), _rows(), {(2025, 11): {"name": "AR-1"}})
    assert eff == {
        "before": {"pct": 100.0, "method": "full", "from": "2025-01-01", "to": None},
        "after": {"pct": 80.0, "method": "full", "from": "2025-10-01", "to": None},
        "current_ends": "2025-09-30",
        "first_period": "FY2025 P10",
        "periods": "FY2025 P10 onward (open-ended)",
        "resign": ["FY2025 P11"],
        "not_shown": NOT_SHOWN,
    }


def test_effect_resign_lists_signed_periods_at_or_after_the_first_in_calendar_order():
    signed = [(2026, 2), ("2025", "10"), (2025, 9), (2025, 12), (2026, 1)]
    eff = M.effect(_change(), _current(), _rows(), signed)
    assert eff["resign"] == ["FY2025 P10", "FY2025 P12", "FY2026 P01", "FY2026 P02"]


def test_effect_no_signed_period():
    assert M.effect(_change(), _current(), _rows(), {})["resign"] == []


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
    eff = M.effect(_change(), _current(), _rows(), {(2025, 11): {}})
    assert eff["not_shown"] == NOT_SHOWN
    flat = json.dumps(eff).lower()
    for word in ("amount", "goodwill_amount", "nci_amount", "balance", "value"):
        assert ('"%s"' % word) not in flat, word
    assert set(eff) == {"before", "after", "current_ends", "first_period", "periods",
                        "resign", "not_shown"}
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
        M.effect(_change(), _current(), _rows(), [(2030, 1)])
    except ValueError:
        return
    raise AssertionError("a signed period missing from the calendar must raise")


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
