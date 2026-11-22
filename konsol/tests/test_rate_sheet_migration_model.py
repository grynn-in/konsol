"""Group Exchange Rate to Rate Sheet, the pure migration planner:
konsol/rate_sheet_migration_model.py (konsol#592 pair 1, task T5).

Loaded by path; the module imports nothing from frappe or konsol.

Decided by Deepak Pai on 22 Nov 2026 (#592): actuals rates become one
Statutory sheet per (year, period), holding Average and Closing per pair;
budget sets become one Constant sheet per year (QA-1, QB-1). Corrections
supersede (#312-3): a migrated correction history becomes a chain of sheets,
the earlier one cancelled, the later naming it in ``supersedes``, named
``RS-S-<FY>-<PP>``, ``-2``, ``-3``. There is no Disabled flag (P1-Q3 A).

The live-shaped fixture mirrors 8069: 14 pairs into USD, FY2010 to FY2024 at
P12 and FY2025 P7 to P12, two pairs whose Closing and Average carry a
different Quoted Per, and the 2012 CAD chain
(``GER-2012-12-CAD-USD-Average-01`` superseded by ``-02``).
"""
import importlib.util
import os
from decimal import Decimal

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "rate_sheet_migration_model.py")
_spec = importlib.util.spec_from_file_location("rsmm_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

ACT = "ACTUAL"
#: 14 pairs into USD. JPY and INR quote Closing and Average per a different unit.
PAIRS = ("AUD", "CAD", "CHF", "CNY", "DKK", "EUR", "GBP", "HKD", "INR", "JPY", "NOK", "SEK", "SGD", "ZAR")
PER = {("JPY", "Closing"): 100, ("JPY", "Average"): 1, ("INR", "Closing"): 1, ("INR", "Average"): 100}
PERIODS = [(y, 12) for y in range(2010, 2025)] + [(2025, p) for p in range(7, 13)]


def _year(regular=12):
    rows = [{"period": 0, "type": "Opening"}]
    rows += [{"period": p, "type": "Regular"} for p in range(1, regular + 1)]
    rows.append({"period": regular + 1, "type": "Closing"})
    return rows


CALENDAR = {y: _year() for y in range(2009, 2028)}


def ger(cur, rtype, fy, fp, quote, per=1, docstatus=1, seq=1, rate_set=ACT, to="USD", supersedes=None,
        amended_from=None, change_reason=None, modified=None, name=None):
    return {
        "name": name or f"GER-{fy}-{fp:02d}-{cur}-{to}-{rtype}-{seq:02d}",
        "rate_set": rate_set, "from_currency": cur, "to_currency": to, "rate_type": rtype,
        "fiscal_year": fy, "fiscal_period": fp, "quote": quote, "quoted_per": per,
        "docstatus": docstatus, "supersedes": supersedes, "amended_from": amended_from,
        "change_reason": change_reason, "source": "Manual", "owner": "analyst@example.com",
        "creation": f"{fy}-12-20 10:00:00", "modified": modified or f"{fy}-12-21 10:00:00",
    }


def _quote(cur, rtype, fy, fp):
    """A deterministic, distinct quote per key, at its own Quoted Per."""
    base = Decimal(PAIRS.index(cur) + 1) / Decimal(10) + Decimal(fy - 2000) / Decimal(1000) + Decimal(fp) / Decimal(10**5)
    if rtype == "Average":
        base += Decimal("0.0001")
    per = PER.get((cur, rtype), 1)
    return float(base * per), per


def live_rates():
    rows = []
    for fy, fp in PERIODS:
        for cur in PAIRS:
            for rtype in ("Closing", "Average"):
                if (fy, fp, cur, rtype) == (2012, 12, "CAD", "Average"):
                    continue
                q, per = _quote(cur, rtype, fy, fp)
                rows.append(ger(cur, rtype, fy, fp, q, per))
    # the 2012 CAD chain: -01 approved, then corrected by -02, whose approval cancelled -01
    rows.append(ger("CAD", "Average", 2012, 12, 0.9811, docstatus=2, seq=1, modified="2026-10-14 09:30:00"))
    rows.append(ger("CAD", "Average", 2012, 12, 0.9781, seq=2, supersedes="GER-2012-12-CAD-USD-Average-01",
                    change_reason="Wrong source day", modified="2026-10-14 09:30:00"))
    return rows


def run(rates, cycles=(), budgets=(), calendar=CALENDAR):
    return M.plan(rates, ACT, cycles, calendar, approved_budgets=budgets)


def sheet(plan, name):
    found = [s for s in plan["sheets"] if s["name"] == name]
    assert len(found) == 1, (name, [s["name"] for s in plan["sheets"]])
    return found[0]


def row(sh, cur, to="USD"):
    found = [r for r in sh["rows"] if (r["from_currency"], r["to_currency"]) == (cur, to)]
    assert len(found) == 1, (sh["name"], cur)
    return found[0]


def true_rate(quote, per):
    return Decimal(str(quote)) / Decimal(per)


# -- the live-shaped fixture ------------------------------------------------------

def test_live_fixture_makes_21_approved_statutory_sheets_and_the_2012_chain():
    p = run(live_rates())
    assert p["refusals"] == []
    assert p["dropped"] == []
    names = [s["name"] for s in p["sheets"]]
    assert len(names) == 22 and len(set(names)) == 22
    approved = [s for s in p["sheets"] if s["docstatus"] == 1]
    assert len(approved) == 21
    assert {(s["fiscal_year"], s["fiscal_period"]) for s in approved} == set(PERIODS)
    assert all(s["rate_basis"] == "Statutory" for s in p["sheets"])
    assert all(len(s["rows"]) == 14 for s in p["sheets"])
    assert "RS-S-2010-12" in names and "RS-S-2025-07" in names


def test_the_2012_cad_chain_is_a_cancelled_sheet_then_its_superseding_sheet():
    p = run(live_rates())
    first, second = sheet(p, "RS-S-2012-12"), sheet(p, "RS-S-2012-12-2")
    assert first["docstatus"] == 2 and first["supersedes"] is None
    assert second["docstatus"] == 1 and second["supersedes"] == "RS-S-2012-12"
    assert "amended_from" not in second or second["amended_from"] is None
    assert row(first, "CAD")["average_quote"] == Decimal("0.9811")
    assert row(second, "CAD")["average_quote"] == Decimal("0.9781")
    assert row(first, "CAD")["legacy_documents"] == ["GER-2012-12-CAD-USD-Average-01",
                                                     "GER-2012-12-CAD-USD-Closing-01"]
    assert row(second, "CAD")["legacy_documents"] == ["GER-2012-12-CAD-USD-Average-02",
                                                      "GER-2012-12-CAD-USD-Closing-01"]
    # the pairs the correction did not touch stand as they are in both versions
    for cur in PAIRS:
        if cur != "CAD":
            assert row(first, cur) == row(second, cur)
    assert row(first, "CAD")["closing_quote"] == row(second, "CAD")["closing_quote"]
    assert second["change_reason"] == "Wrong source day"
    assert not [s for s in p["sheets"] if s["name"].startswith("RS-S-2012-12-3")]


def test_mixed_quoted_per_is_rescaled_exactly_to_the_larger_unit():
    p = run(live_rates())
    for fy, fp in PERIODS:
        name = M.statutory_name(fy, fp)
        sh = sheet(p, name if (fy, fp) != (2012, 12) else "RS-S-2012-12-2")
        for cur in ("JPY", "INR"):
            r = row(sh, cur)
            assert r["quoted_per"] == 100
            for rtype, field in (("Closing", "closing_quote"), ("Average", "average_quote")):
                q, per = _quote(cur, rtype, fy, fp)
                assert true_rate(r[field], r["quoted_per"]) == true_rate(q, per), (name, cur, rtype)
        assert row(sh, "EUR")["quoted_per"] == 1


def test_rescale_is_exact_decimal():
    assert M.rescale(0.006607, 1, 100) == Decimal("0.6607")
    assert M.rescale(0.1, 1, 10000) == Decimal("1000")
    assert M.rescale(Decimal("12.345678901"), 10, 1000) == Decimal("1234.5678901")
    assert M.rescale(0.5, 100, 100) == Decimal("0.5")


def test_rows_are_migrated_and_every_sheet_carries_its_audit_rows():
    p = run(live_rates())
    sh = sheet(p, "RS-S-2012-12")
    assert {r["source"] for r in sh["rows"]} == {"Migrated"}
    assert "GER-2012-12-CAD-USD-Average-01" in {a["name"] for a in sh["audit_rows"]}
    assert len(sh["audit_rows"]) == 28


# -- refusals ---------------------------------------------------------------------

def test_a_pair_missing_one_rate_type_refuses_naming_the_key():
    rates = [ger("EUR", "Closing", 2024, 12, 1.08), ger("EUR", "Average", 2024, 12, 1.07),
             ger("GBP", "Closing", 2024, 12, 1.27)]
    p = run(rates)
    assert len(p["refusals"]) == 1
    assert "GBP" in p["refusals"][0] and "2024" in p["refusals"][0] and "Average" in p["refusals"][0]
    try:
        M.assert_no_refusals(p)
    except M.MigrationRefused as e:
        assert "GBP" in str(e)
    else:
        raise AssertionError("refused plan passed")


def test_assert_no_refusals_passes_a_clean_plan():
    M.assert_no_refusals(run(live_rates()))


def test_two_approved_rates_for_one_key_refuse():
    rates = [ger("EUR", "Closing", 2024, 12, 1.08), ger("EUR", "Closing", 2024, 12, 1.09, seq=2),
             ger("EUR", "Average", 2024, 12, 1.07)]
    p = run(rates)
    assert any("EUR" in r and "GER-2024-12-EUR-USD-Closing-02" in r for r in p["refusals"])


def test_an_undeclared_period_refuses():
    rates = [ger("EUR", "Closing", 2030, 12, 1.08), ger("EUR", "Average", 2030, 12, 1.07)]
    p = run(rates)
    assert any("2030" in r and "declared" in r for r in p["refusals"])


def test_a_row_without_a_rate_set_refuses():
    rates = [ger("EUR", "Closing", 2024, 12, 1.08, rate_set=None), ger("EUR", "Average", 2024, 12, 1.07)]
    p = run(rates)
    assert any("GER-2024-12-EUR-USD-Closing-01" in r for r in p["refusals"])


# -- chains -----------------------------------------------------------------------

def _base(fy=2024, fp=12, pairs=("EUR", "GBP")):
    return [ger(c, t, fy, fp, 1.0 + i / 10) for i, c in enumerate(pairs) for t in ("Closing", "Average")]


def test_a_chain_of_three_makes_three_versions():
    rates = [r for r in _base() if r["name"] != "GER-2024-12-EUR-USD-Closing-01"]
    rates += [
        ger("EUR", "Closing", 2024, 12, 1.01, docstatus=2, seq=1, modified="2026-01-01 10:00:00"),
        ger("EUR", "Closing", 2024, 12, 1.02, docstatus=2, seq=2, supersedes="GER-2024-12-EUR-USD-Closing-01",
            change_reason="first fix", modified="2026-02-01 10:00:00"),
        ger("EUR", "Closing", 2024, 12, 1.03, seq=3, supersedes="GER-2024-12-EUR-USD-Closing-02",
            change_reason="second fix", modified="2026-02-01 10:00:00"),
    ]
    p = run(rates)
    assert p["refusals"] == [] and p["dropped"] == []
    v1, v2, v3 = sheet(p, "RS-S-2024-12"), sheet(p, "RS-S-2024-12-2"), sheet(p, "RS-S-2024-12-3")
    assert [v["docstatus"] for v in (v1, v2, v3)] == [2, 2, 1]
    assert [v["supersedes"] for v in (v1, v2, v3)] == [None, "RS-S-2024-12", "RS-S-2024-12-2"]
    assert [row(v, "EUR")["closing_quote"] for v in (v1, v2, v3)] == [Decimal("1.01"), Decimal("1.02"),
                                                                     Decimal("1.03")]
    assert v3["change_reason"] == "second fix"


def test_two_corrections_in_one_moment_make_one_version_and_apart_make_two():
    def with_fixes(t_eur, t_gbp):
        rates = [r for r in _base() if r["name"] not in ("GER-2024-12-EUR-USD-Average-01",
                                                         "GER-2024-12-GBP-USD-Average-01")]
        for cur, t in (("EUR", t_eur), ("GBP", t_gbp)):
            rates += [ger(cur, "Average", 2024, 12, 2.0, docstatus=2, modified=t),
                      ger(cur, "Average", 2024, 12, 2.5, seq=2, supersedes=f"GER-2024-12-{cur}-USD-Average-01",
                          change_reason=f"fix {cur}", modified=t)]
        return run(rates)

    same = with_fixes("2026-03-01 10:00:00", "2026-03-01 10:00:00")
    assert [s["name"] for s in same["sheets"]] == ["RS-S-2024-12", "RS-S-2024-12-2"]
    apart = with_fixes("2026-03-01 10:00:00", "2026-03-05 10:00:00")
    assert [s["name"] for s in apart["sheets"]] == ["RS-S-2024-12", "RS-S-2024-12-2", "RS-S-2024-12-3"]
    middle = sheet(apart, "RS-S-2024-12-2")
    assert row(middle, "EUR")["average_quote"] == Decimal("2.5")
    assert row(middle, "GBP")["average_quote"] == Decimal("2.0")
    assert middle["docstatus"] == 2


def test_a_cancelled_rate_amended_into_an_approved_one_is_a_version():
    rates = [r for r in _base() if r["name"] != "GER-2024-12-EUR-USD-Closing-01"]
    rates += [ger("EUR", "Closing", 2024, 12, 1.01, docstatus=2, modified="2026-01-01 10:00:00"),
              ger("EUR", "Closing", 2024, 12, 1.05, name="GER-2024-12-EUR-USD-Closing-01-1",
                  amended_from="GER-2024-12-EUR-USD-Closing-01", change_reason="amended")]
    p = run(rates)
    assert [s["name"] for s in p["sheets"]] == ["RS-S-2024-12", "RS-S-2024-12-2"]
    assert row(sheet(p, "RS-S-2024-12-2"), "EUR")["closing_quote"] == Decimal("1.05")


def test_a_cancelled_rate_with_no_successor_is_dropped_and_named():
    rates = _base() + [ger("EUR", "Closing", 2024, 12, 1.5, docstatus=2, seq=7)]
    p = run(rates)
    assert [s["name"] for s in p["sheets"]] == ["RS-S-2024-12"]
    assert [d["document"] for d in p["dropped"]] == ["GER-2024-12-EUR-USD-Closing-07"]
    assert p["dropped"][0]["reason"]


# -- drafts -----------------------------------------------------------------------

def test_drafts_in_a_period_with_no_approved_rate_become_a_draft_sheet():
    rates = _base() + [ger(c, t, 2025, 1, 1.2, docstatus=0) for c in ("EUR", "GBP") for t in ("Closing", "Average")]
    p = run(rates)
    draft = sheet(p, "RS-S-2025-01")
    assert draft["docstatus"] == 0 and draft["supersedes"] is None and len(draft["rows"]) == 2
    assert sheet(p, "RS-S-2024-12")["docstatus"] == 1


def test_a_pending_correction_is_dropped_and_named():
    rates = _base() + [ger("EUR", "Closing", 2024, 12, 1.9, docstatus=0, seq=2,
                           supersedes="GER-2024-12-EUR-USD-Closing-01", change_reason="pending")]
    p = run(rates)
    assert [s["name"] for s in p["sheets"]] == ["RS-S-2024-12"]
    assert [d["document"] for d in p["dropped"]] == ["GER-2024-12-EUR-USD-Closing-02"]
    assert "correction" in p["dropped"][0]["reason"]


def test_a_plain_draft_in_an_approved_period_is_dropped_and_named():
    rates = _base() + [ger("CHF", c, 2024, 12, 1.1, docstatus=0) for c in ("Closing", "Average")]
    p = run(rates)
    assert len(sheet(p, "RS-S-2024-12")["rows"]) == 2
    assert sorted(d["document"] for d in p["dropped"]) == ["GER-2024-12-CHF-USD-Average-01",
                                                           "GER-2024-12-CHF-USD-Closing-01"]


# -- Closing-type periods ---------------------------------------------------------

def test_a_closing_type_period_is_dropped_into_the_audit_not_a_sheet():
    """Was "migrated for audit" as a sheet (T5); T25b drops it instead: the
    controller and the publish refuse such a sheet."""
    rates = _base(fp=13)
    p = run(rates)
    assert p["sheets"] == [] and p["refusals"] == []
    assert sorted(d["document"] for d in p["dropped"]) == sorted(r["name"] for r in rates)
    assert not any("RS-S-2024-13" in w for w in p["warnings"])


# -- Constant sheets from budget sets ---------------------------------------------

def _cycle(name, fy, rate_set, docstatus=1):
    return {"name": name, "fiscal_year": fy, "rate_set": rate_set, "docstatus": docstatus}


def _budget_rates(rate_set, fy, quotes):
    """quotes: {cur: [(quote, per) per period]} for periods 1..n, both types."""
    rows = []
    for cur, qs in quotes.items():
        for fp, (q, per) in enumerate(qs, start=1):
            for t in ("Closing", "Average"):
                rows.append(ger(cur, t, fy, fp, q, per=per, rate_set=rate_set,
                                name=f"GER-{fy}-{fp:02d}-{cur}-USD-{t}-{rate_set}-01"))
    return rows


def test_uniform_budget_rates_become_an_approved_constant_sheet():
    rates = _budget_rates("BUD26", 2026, {"EUR": [(1.1, 1)] * 12, "JPY": [(0.0066, 1), (0.66, 100)] * 6})
    p = run(rates, cycles=[_cycle("BC-2026", 2026, "BUD26")])
    assert p["refusals"] == [] and p["dropped"] == []
    c = sheet(p, "RS-C-2026")
    assert c["rate_basis"] == "Constant" and c["docstatus"] == 1 and c["fiscal_period"] is None
    assert row(c, "EUR")["rate"] == Decimal("1.1") and row(c, "EUR")["quoted_per"] == 1
    assert row(c, "JPY")["quoted_per"] == 100 and row(c, "JPY")["rate"] == Decimal("0.66")
    assert len(row(c, "EUR")["legacy_documents"]) == 24
    assert "average_quote" not in row(c, "EUR")


def test_non_uniform_budget_rates_become_a_draft_constant_sheet_with_a_warning():
    rates = _budget_rates("BUD26", 2026, {"EUR": [(1.1, 1)] * 6 + [(1.2, 1)] * 6, "GBP": [(1.3, 1)] * 12})
    p = run(rates, cycles=[_cycle("BC-2026", 2026, "BUD26")])
    c = sheet(p, "RS-C-2026")
    assert c["docstatus"] == 0
    assert row(c, "EUR")["rate"] is None
    assert row(c, "GBP")["rate"] == Decimal("1.3")
    assert any("RS-C-2026" in w and "EUR" in w for w in p["warnings"])


def test_a_set_with_no_approved_cycle_is_dropped():
    rates = _budget_rates("BUD26", 2026, {"EUR": [(1.1, 1)] * 2})
    p = run(rates, cycles=[_cycle("BC-2026", 2026, "BUD26", docstatus=0)])
    assert p["sheets"] == []
    assert len(p["dropped"]) == 4 and all("Budget Cycle" in d["reason"] for d in p["dropped"])


def test_two_cycles_on_different_sets_in_one_year_refuse():
    rates = (_budget_rates("BUDA", 2026, {"EUR": [(1.1, 1)]}) + _budget_rates("BUDB", 2026, {"EUR": [(1.2, 1)]}))
    cycles = [_cycle("BC-A", 2026, "BUDA"), _cycle("BC-B", 2026, "BUDB")]
    p = run(rates, cycles=cycles)
    assert any("2026" in r and "BUDA" in r and "BUDB" in r for r in p["refusals"])


def test_an_approved_budget_picks_the_cycle_when_two_sets_share_a_year():
    rates = (_budget_rates("BUDA", 2026, {"EUR": [(1.1, 1)]}) + _budget_rates("BUDB", 2026, {"EUR": [(1.2, 1)]}))
    cycles = [_cycle("BC-A", 2026, "BUDA"), _cycle("BC-B", 2026, "BUDB")]
    budgets = [{"name": "AB-1", "fiscal_year": 2026, "budget_cycle": "BC-B", "rate_set": "BUDB", "docstatus": 1}]
    p = run(rates, cycles=cycles, budgets=budgets)
    assert p["refusals"] == []
    assert row(sheet(p, "RS-C-2026"), "EUR")["rate"] == Decimal("1.2")
    assert sorted(d["document"] for d in p["dropped"]) == sorted(
        r["name"] for r in rates if r["rate_set"] == "BUDA")


def test_actuals_and_budget_together():
    rates = live_rates() + _budget_rates("BUD26", 2026, {"EUR": [(1.1, 1)] * 12})
    p = run(rates, cycles=[_cycle("BC-2026", 2026, "BUD26")])
    assert p["refusals"] == []
    assert len(p["sheets"]) == 23
    assert sheet(p, "RS-C-2026")["docstatus"] == 1


# -- purity -----------------------------------------------------------------------

def test_the_model_imports_nothing_from_frappe_or_konsol():
    import ast
    tree = ast.parse(open(_PATH).read())
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not names & {"frappe", "konsol"}, names


# -- Closing-type periods (T25b, coordinator 22 Nov 2026) ----------------------------

CLOSING_REASON = ("Closing-type period: the warehouse reads the year's last Regular period; kept in the "
                  "migration audit CSV")


def test_closing_type_period_rows_are_dropped_and_named_not_a_sheet():
    """dbt never reads a Closing period's own rates (rate_period_map reads the
    year's last Regular period) and the controller refuses such a sheet, so
    every GER row of a Closing-type period is dropped with the reason."""
    rates = live_rates()
    extra = [ger("EUR", "Closing", 2019, 13, 1.1), ger("EUR", "Average", 2019, 13, 1.1),
             ger("GBP", "Closing", 2018, 13, 1.3, docstatus=0)]
    p = run(rates + extra)
    assert p["refusals"] == []
    assert not [s for s in p["sheets"] if s["fiscal_period"] == 13], [s["name"] for s in p["sheets"]]
    assert p["dropped"] == sorted(({"document": r["name"], "reason": CLOSING_REASON} for r in extra),
                                  key=lambda d: d["document"])
    assert not [w for w in p["warnings"] if "Closing period" in w], p["warnings"]
    assert len(p["sheets"]) == 22


def test_a_closing_type_period_missing_a_rate_type_is_dropped_not_refused():
    p = run(live_rates() + [ger("EUR", "Closing", 2019, 13, 1.1)])
    assert p["refusals"] == []
    assert [d["document"] for d in p["dropped"]] == ["GER-2019-13-EUR-USD-Closing-01"]


# -- PR #595 review 1: nothing the planner emits is refused by the controller ------

_RSM_SPEC = importlib.util.spec_from_file_location("rsm_for_planner", os.path.join(APP_DIR, "rate_sheet_model.py"))
RSM = importlib.util.module_from_spec(_RSM_SPEC)
_RSM_SPEC.loader.exec_module(RSM)


def _dated_year(fy, adjustment=13, closing=14):
    """Every period type with dates, the shape rate_sheet_model.window reads."""
    import datetime

    end = datetime.date(fy, 12, 31)
    rows = [{"fiscal_year": fy, "period": 0, "type": "Opening", "start_date": datetime.date(fy, 1, 1),
             "end_date": datetime.date(fy, 1, 1)}]
    for p in range(1, 13):
        nxt = datetime.date(fy + (p == 12), p % 12 + 1, 1)
        rows.append({"fiscal_year": fy, "period": p, "type": "Regular", "start_date": datetime.date(fy, p, 1),
                     "end_date": nxt - datetime.timedelta(days=1)})
    rows.append({"fiscal_year": fy, "period": adjustment, "type": "Adjustment", "start_date": end, "end_date": end})
    rows.append({"fiscal_year": fy, "period": closing, "type": "Closing", "start_date": end, "end_date": end})
    return rows


def _controller_sheet(s):
    """A planned sheet as the controller hands it to rate_sheet_model."""
    return {"name": s["name"], "rate_basis": s["rate_basis"], "fiscal_year": s["fiscal_year"],
            "fiscal_period": s["fiscal_period"], "docstatus": s["docstatus"],
            "rows": [{**r, "quoted_per": r["quoted_per"],
                      **{f: (None if r.get(f) is None else float(r[f]))
                         for f in ("average_quote", "closing_quote", "rate") if f in r}}
                     for r in s["rows"]]}


def test_every_planned_sheet_passes_the_controllers_rules():
    """Each sheet the planner emits is written through the controller, which
    applies rate_sheet_model.window and row_problems: one that they refuse
    stops the migrate half-way. Every period type is covered: Opening,
    Regular, Adjustment (now a sheet of its own) and Closing (dropped), and a
    uniform and a non-uniform Constant sheet. The non-uniform Constant draft
    carries a blank rate on purpose and is the patch's one named exemption
    from row_problems (``_prepare_blank_constant_draft``); its window must
    still pass."""
    fy = 2024
    calendar = {fy: _dated_year(fy), 2026: _dated_year(2026), 2027: _dated_year(2027)}
    rates = []
    for fp in (0, 6, 12, 13, 14):
        rates += _base(fy=fy, fp=fp)
    rates += _base(fy=fy, fp=11) + [ger("EUR", "Closing", fy, 11, 1.4, docstatus=2, seq=2,
                                        modified="2026-01-01 10:00:00")]
    rates += _budget_rates("BUD26", 2026, {"EUR": [(1.1, 1)] * 12})
    rates += _budget_rates("BUD27", 2027, {"EUR": [(1.1, 1)] * 6 + [(1.2, 1)] * 6})
    cycles = [_cycle("BC-2026", 2026, "BUD26"), _cycle("BC-2027", 2027, "BUD27")]
    p = M.plan(rates, ACT, cycles, calendar)
    assert p["refusals"] == []
    names = {s["name"] for s in p["sheets"]}
    assert {"RS-S-2024-00", "RS-S-2024-06", "RS-S-2024-12", "RS-S-2024-13", "RS-C-2026", "RS-C-2027"} <= names
    assert "RS-S-2024-14" not in names
    exempt = []
    for s in p["sheets"]:
        sheet = _controller_sheet(s)
        _dates, why = RSM.window(sheet, calendar[s["fiscal_year"]])
        assert why is None, (s["name"], why)
        problems = RSM.row_problems(sheet)
        if s["rate_basis"] == CONSTANT_BASIS and s["docstatus"] == 0 and any(
                r.get("rate") is None for r in s["rows"]):
            exempt.append(s["name"])
            continue
        assert problems == [], (s["name"], problems)
    assert exempt == ["RS-C-2027"]


CONSTANT_BASIS = "Constant"
