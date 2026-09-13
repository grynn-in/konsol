"""One-time migration from implied periods to declared Fiscal Years (konsol#189),
pure: konsol/fiscal_migration_model.py.

Loaded by path; the module loads its siblings (fiscal_patterns_model,
fiscal_status_model) by path too and imports nothing from frappe.
"""
import copy
import datetime
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "fmm_under_test", os.path.join(APP_DIR, "fiscal_migration_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def d(y, m, day):
    return datetime.date(y, m, day)


def ps(year, period, status, start=None, end=None, by=None, on=None):
    return {
        "fiscal_year": year, "fiscal_period": period, "status": status,
        "start_date": start, "end_date": end, "closed_by": by, "closed_on": on,
    }


def _rows_by_period(year_dict):
    return {r["period"]: r for r in year_dict["rows"]}


def _as_existing(created, moves):
    """The created years as `existing_years`, with the moves applied."""
    out = {}
    for y in copy.deepcopy(created):
        out[y["year"]] = {"rows": y["rows"]}
    for mv in moves:
        for row in out[mv["year"]]["rows"]:
            if row["code"] == mv["code"]:
                row["status"] = mv["status"]
    return out


def test_plan_creates_calendar_years():
    result = M.plan({(2025, 3), (2026, 0)}, [ps(2024, 12, "Open")], {})
    assert result["moves"] == []
    assert result["conflicts"] == []
    years = [y["year"] for y in result["create"]]
    assert years == [2024, 2025, 2026]

    y = result["create"][1]
    assert y["start_date"] == d(2025, 1, 1)
    assert y["end_date"] == d(2025, 12, 31)
    assert y["period_pattern"] == "Monthly (12)"
    codes = [r["code"] for r in y["rows"]]
    assert codes == ["OPN"] + ["P%02d" % i for i in range(1, 13)] + ["CLS"]
    rows = _rows_by_period(y)
    assert rows[0]["type"] == "Opening"
    assert rows[13]["type"] == "Closing"
    assert rows[3]["start_date"] == d(2025, 3, 1)
    assert rows[3]["end_date"] == d(2025, 3, 31)
    assert all(r["status"] == "Open" for r in y["rows"])

    # a year that already exists is not created again
    existing = {2025: {"rows": copy.deepcopy(y["rows"])}}
    again = M.plan({(2025, 3)}, [], existing)
    assert again["create"] == []


def test_period_above_13_becomes_adjustment():
    result = M.plan({(2025, 14), (2025, 255), (2025, 256), (2025, -1)}, [], {})
    y = result["create"][0]
    rows = _rows_by_period(y)
    assert set(rows) == set(range(0, 15)) | {255}
    adj = rows[14]
    assert adj["type"] == "Adjustment"
    assert adj["code"] == "P14"
    assert adj["start_date"] == d(2025, 12, 31)
    assert adj["end_date"] == d(2025, 12, 31)
    assert rows[255]["code"] == "P255"
    # rows stay ordered by period number
    assert [r["period"] for r in y["rows"]] == sorted(rows)

    assert len(result["conflicts"]) == 2
    joined = " | ".join(result["conflicts"])
    assert "2025" in joined and "256" in joined and "-1" in joined


def test_ps_status_moves():
    rows_2024 = M.plan({(2024, 1)}, [], {})["create"][0]["rows"]
    existing = {2024: {"rows": copy.deepcopy(rows_2024)}}
    ps_rows = [
        # planned year: Closed with matching dates -> move
        ps(2025, 1, "Closed", d(2025, 1, 1), d(2025, 1, 31), "a@x", d(2025, 2, 5)),
        # planned year: Open -> nothing
        ps(2025, 2, "Open"),
        # planned year, adjustment period from a PS row -> Locked move
        ps(2025, 14, "Locked", by="b@x", on=d(2026, 1, 20)),
        # existing year, target Open -> move
        ps(2024, 12, "Locked", None, None, "c@x", d(2025, 1, 10)),
    ]
    result = M.plan(set(), ps_rows, existing)
    assert result["conflicts"] == []
    assert [y["year"] for y in result["create"]] == [2025]
    assert result["moves"] == [
        {"year": 2024, "code": "P12", "status": "Locked", "closed_by": "c@x", "closed_on": d(2025, 1, 10)},
        {"year": 2025, "code": "P01", "status": "Closed", "closed_by": "a@x", "closed_on": d(2025, 2, 5)},
        {"year": 2025, "code": "P14", "status": "Locked", "closed_by": "b@x", "closed_on": d(2026, 1, 20)},
    ]

    # target already equal -> nothing
    existing[2024]["rows"][12]["status"] = "Locked"
    assert M.plan(set(), [ps(2024, 12, "Locked")], existing)["moves"] == []


def test_conflicts_named():
    rows_2024 = M.plan({(2024, 1)}, [], {})["create"][0]["rows"]
    rows_2024 = [r for r in copy.deepcopy(rows_2024) if r["period"] != 13]  # no CLS
    rows_2024[3]["status"] = "Closed"  # P03 closed since an earlier run
    existing = {2024: {"rows": rows_2024}}
    ps_rows = [
        ps(2025, 4, "Closed", d(2025, 4, 1), d(2025, 4, 29)),   # dates differ
        ps(2024, 13, "Closed"),                                # target missing
        ps(2024, 3, "Locked"),                                 # target changed
    ]
    result = M.plan(set(), ps_rows, existing)
    assert result["moves"] == []
    conflicts = result["conflicts"]
    assert len(conflicts) == 3

    dates = [c for c in conflicts if "2025-04-29" in c]
    assert len(dates) == 1
    assert "2025" in dates[0] and "4" in dates[0] and "2025-04-30" in dates[0]

    missing = [c for c in conflicts if "13" in c and "2024" in c]
    assert len(missing) == 1

    changed = [c for c in conflicts if "changed since an earlier run" in c]
    assert len(changed) == 1
    assert "2024" in changed[0] and "P03" in changed[0]


def test_used_period_missing_from_existing_year_is_a_conflict():
    rows_2024 = M.plan({(2024, 1)}, [], {})["create"][0]["rows"]
    existing = {2024: {"rows": copy.deepcopy(rows_2024)}}

    result = M.plan({(2024, 14)}, [], existing)
    assert result["create"] == []
    assert result["moves"] == []
    assert len(result["conflicts"]) == 1
    assert "2024" in result["conflicts"][0] and "14" in result["conflicts"][0]

    result = M.plan({(2024, 5)}, [], existing)
    assert result["conflicts"] == []


def test_second_plan_is_empty():
    used = {(2024, 5), (2025, 1), (2025, 14)}
    ps_rows = [
        ps(2024, 1, "Locked", d(2024, 1, 1), d(2024, 1, 31), "a@x", d(2024, 2, 3)),
        ps(2024, 2, "Closed", by="a@x", on=d(2024, 3, 3)),
        ps(2025, 1, "Open"),
    ]
    first = M.plan(used, ps_rows, {})
    assert first["conflicts"] == []
    assert len(first["moves"]) == 2

    existing = _as_existing(first["create"], first["moves"])
    second = M.plan(used, ps_rows, existing)
    assert second == {"create": [], "moves": [], "conflicts": []}
