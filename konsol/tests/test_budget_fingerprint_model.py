"""S4 K7 (konsol#348, BUD-11-3): the fingerprint of a Budget Cycle's approved
INPUTS, so an input changed after Approve reads as a mismatch (K8, S4-Q5 a).

``konsol/epm/budget_fingerprint_model.py`` is pure (no frappe, no ClickHouse):
loaded by path like ``konsol/close/fingerprint_model.py``, whose style it
follows. It hashes what the Cycle approved, never the translated numbers (a
Cycle on actual rates would otherwise void every month):

- the Cycle's Rate Set;
- its Budget Annual Input rows (grain, layer, currency, amount, profile);
- its Budget Entry Lines (grain, debit, credit, basis);
- the Spread Profile weights the annual rows use (an unused profile is not
  an input);
- the approved Group Exchange Rates and Historical Equity Rates of its set.

Order-independent (the per-row hashes are summed mod 2**256, never XORed);
changing one amount, one rate or one weight changes it; amounts are
normalised to their stored scale, so 100, 100.0 and "100.00" are one number.
"""
import importlib.util
import os
from decimal import Decimal

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PY = os.path.join(APP_DIR, "epm", "budget_fingerprint_model.py")

_spec = importlib.util.spec_from_file_location("budget_fingerprint_model_under_test", MODEL_PY)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _annual(account="4000", amount=1200, profile="EVEN", entity="ZZNL", layer="base", currency="EUR", **dims):
    return {"data_area_id": entity, "main_account": account, "layer": layer, "currency": currency,
            "annual_amount": amount, "spread_profile_id": profile, **dims}


def _line(account="5000", debit=100, credit=0, fp=1, entity="ZZNL", layer="base", currency="EUR",
          basis="movement", **dims):
    return {"data_area_id": entity, "fiscal_year": 2027, "fiscal_period": fp, "main_account": account,
            "layer": layer, "currency": currency, "amount_basis": basis, "debit": debit, "credit": credit,
            **dims}


def _weight(fp, weight, profile="EVEN"):
    return {"profile_id": profile, "fiscal_period": fp, "weight": weight}


def _rate(fp=1, quote="1.08", per=1, kind="Average", frm="EUR", to="USD"):
    return {"from_currency": frm, "to_currency": to, "fiscal_year": 2027, "fiscal_period": fp,
            "rate_type": kind, "quote": quote, "quoted_per": per}


def _her(rate="1.05", date="2024-01-01", account="3000", entity="ZZNL", group="ZZGRP", to="USD"):
    return {"consolidation_group": group, "data_area_id": entity, "main_account": account,
            "rate_date": date, "to_currency": to, "historical_rate": rate}


def _link(entity="ZZNL", owner_entity="", owner_group="ZZGRP", group="ZZGRP", pct=80, method="full",
          start="2024-01-01", end="9999-12-31", disposal=0, disposal_date="9999-12-31", **over):
    return {"consolidation_group": group, "data_area_id": entity, "owner_group": owner_group,
            "owner_data_area_id": owner_entity, "ownership_pct": pct, "consolidation_method": method,
            "effective_date": start, "end_date": end, "acquisition_date": "2024-01-01",
            "is_first_acquisition": 1, "is_disposal": disposal, "disposal_date": disposal_date,
            "supersedes": "", "superseded_end_date": None, **over}


def _inputs(**over):
    base = dict(
        rate_set="BUDGET_2027",
        annual_inputs=[_annual(), _annual(account="4100", amount=600, profile="SEASONAL")],
        entry_lines=[_line(), _line(fp=2, debit=0, credit=50), _line(account="5100", debit=20)],
        weights=[_weight(p, 1 / 12) for p in range(1, 13)] + [_weight(1, 0.5, "SEASONAL"), _weight(7, 0.5, "SEASONAL")]
        + [_weight(1, 1.0, "UNUSED")],
        exchange_rates=[_rate(), _rate(kind="Closing", quote="1.10"), _rate(fp=2, quote="1.09")],
        historical_rates=[_her(), _her(date="2025-06-30", rate="1.07")],
        ownership_links=[_link(), _link(entity="ZZUS", pct=100)],
        dimensions=(),
    )
    base.update(over)
    return base


def _fp(**over):
    return M.input_fingerprint(**_inputs(**over))


def test_identical_inputs_give_the_same_fingerprint_in_any_order():
    a = _fp()
    rev = {k: (list(reversed(v)) if isinstance(v, list) else v) for k, v in _inputs().items()}
    assert a == M.input_fingerprint(**rev)
    assert a.startswith(M.VERSION + ":") and len(a) == len(M.VERSION) + 1 + 64


def test_one_annual_amount_changes_it():
    changed = _inputs()["annual_inputs"]
    changed[0] = dict(changed[0], annual_amount=1200.01)
    assert _fp(annual_inputs=changed) != _fp()


def test_one_entry_line_amount_changes_it():
    lines = _inputs()["entry_lines"]
    lines[2] = dict(lines[2], debit=20.0001)
    assert _fp(entry_lines=lines) != _fp()
    lines = _inputs()["entry_lines"]
    lines[1] = dict(lines[1], credit=51)
    assert _fp(entry_lines=lines) != _fp()


def test_one_exchange_rate_changes_it_and_so_does_its_unit():
    rates = _inputs()["exchange_rates"]
    rates[0] = dict(rates[0], quote="1.080001")
    assert _fp(exchange_rates=rates) != _fp()
    rates = _inputs()["exchange_rates"]
    rates[0] = dict(rates[0], quoted_per=100)
    assert _fp(exchange_rates=rates) != _fp()


def test_one_historical_rate_changes_it():
    hers = _inputs()["historical_rates"]
    hers[1] = dict(hers[1], historical_rate="1.070001")
    assert _fp(historical_rates=hers) != _fp()
    hers = _inputs()["historical_rates"] + [_her(date="2026-01-01", rate="1.02")]
    assert _fp(historical_rates=hers) != _fp()


def test_one_used_weight_changes_it_and_an_unused_profiles_weight_does_not():
    weights = _inputs()["weights"]
    weights[0] = dict(weights[0], weight=0.09)
    assert _fp(weights=weights) != _fp()
    weights = _inputs()["weights"]
    weights[-1] = dict(weights[-1], weight=0.25)  # UNUSED: no annual row names it
    assert _fp(weights=weights) == _fp()
    assert _fp(weights=weights[:-1]) == _fp()


def test_the_rate_set_itself_is_an_input():
    assert _fp(rate_set="ACTUAL") != _fp()


def test_adding_or_removing_a_row_changes_it():
    assert _fp(annual_inputs=_inputs()["annual_inputs"][:1]) != _fp()
    assert _fp(entry_lines=_inputs()["entry_lines"] + [_line(account="5200", debit=1)]) != _fp()
    assert _fp(exchange_rates=_inputs()["exchange_rates"][:2]) != _fp()


def test_a_grain_change_changes_it_even_with_the_same_amounts():
    lines = _inputs()["entry_lines"]
    lines[0] = dict(lines[0], fiscal_period=3)
    assert _fp(entry_lines=lines) != _fp()
    annual = _inputs()["annual_inputs"]
    annual[0] = dict(annual[0], spread_profile_id="SEASONAL")
    assert _fp(annual_inputs=annual) != _fp()
    annual = _inputs()["annual_inputs"]
    annual[0] = dict(annual[0], layer="adjustment")
    assert _fp(annual_inputs=annual) != _fp()


def test_a_declared_dimension_value_is_part_of_the_grain():
    with_dim = _inputs(dimensions=("dim_cost_center",))
    with_dim["annual_inputs"] = [dict(r, dim_cost_center="CC1") for r in with_dim["annual_inputs"]]
    with_dim["entry_lines"] = [dict(r, dim_cost_center="CC1") for r in with_dim["entry_lines"]]
    a = M.input_fingerprint(**with_dim)
    moved = dict(with_dim)
    moved["entry_lines"] = [dict(with_dim["entry_lines"][0], dim_cost_center="CC2")] + with_dim["entry_lines"][1:]
    assert M.input_fingerprint(**moved) != a
    # an undeclared dim_* key on a row is not read: the site's declaration decides
    undeclared = dict(with_dim, dimensions=())
    assert M.input_fingerprint(**undeclared) == M.input_fingerprint(**dict(_inputs()))


def test_numbers_are_compared_at_their_stored_scale():
    annual = _inputs()["annual_inputs"]
    annual[0] = dict(annual[0], annual_amount="1200.00")
    assert _fp(annual_inputs=annual) == _fp()
    annual[0] = dict(annual[0], annual_amount=Decimal("1200.004"))  # Decimal(18,2): rounds to 1200.00
    assert _fp(annual_inputs=annual) == _fp()
    lines = _inputs()["entry_lines"]
    lines[0] = dict(lines[0], debit=Decimal("100.0000"))
    assert _fp(entry_lines=lines) == _fp()
    rates = _inputs()["exchange_rates"]
    rates[0] = dict(rates[0], quote=Decimal("1.080000000"))
    assert _fp(exchange_rates=rates) == _fp()


def test_two_lines_of_one_grain_are_the_one_number_they_sum_to():
    """Entry lines aggregate per grain, as the warehouse sums them: a line split
    in two is the same budget, two lines trading amounts is not."""
    lines = _inputs()["entry_lines"]
    first = lines.pop(0)
    lines += [dict(first, debit=60), dict(first, debit=40)]
    assert _fp(entry_lines=lines) == _fp()
    a = [_line(account="5000", debit=10), _line(account="5100", debit=20)]
    b = [_line(account="5000", debit=20), _line(account="5100", debit=10)]
    assert _fp(entry_lines=a) != _fp(entry_lines=b)


def test_a_null_amount_raises_naming_the_row():
    annual = _inputs()["annual_inputs"]
    annual[0] = dict(annual[0], annual_amount=None)
    with pytest.raises(ValueError) as info:
        _fp(annual_inputs=annual)
    assert "NULL" in str(info.value) and "4000" in str(info.value)
    rates = _inputs()["exchange_rates"]
    rates[0] = dict(rates[0], quote=None)
    with pytest.raises(ValueError):
        _fp(exchange_rates=rates)


def test_no_inputs_is_still_a_fingerprint_of_the_set():
    empty = M.input_fingerprint("BUDGET_2027", [], [], [], [], [])
    assert empty.startswith(M.VERSION + ":")
    assert empty != M.input_fingerprint("ACTUAL", [], [], [], [], [])
    assert empty != _fp()


def test_the_model_is_pure_and_sums_not_xors():
    with open(MODEL_PY) as f:
        source = f.read()
    assert "import frappe" not in source and "clickhouse" not in source.lower()
    assert " ^ " not in source and "^=" not in source, "sum the per-row hashes, never XOR them"


# ---- ownership (konsol#552, Deepak Pai, 18 Nov 2026) ---------------------------------

def test_an_added_ownership_link_of_a_cycle_entity_changes_it():
    links = _inputs()["ownership_links"] + [_link(entity="ZZNL", pct=10, start="2026-01-01")]
    assert _fp(ownership_links=links) != _fp()
    assert _fp(ownership_links=[]) != _fp()


def test_a_changed_end_date_changes_it():
    links = _inputs()["ownership_links"]
    links[0] = dict(links[0], end_date="2027-06-30")
    assert _fp(ownership_links=links) != _fp()


def test_a_disposal_percentage_or_method_change_changes_it():
    for change in ({"is_disposal": 1, "disposal_date": "2027-03-31"}, {"ownership_pct": 79.99},
                   {"consolidation_method": "equity"}):
        links = _inputs()["ownership_links"]
        links[0] = dict(links[0], **change)
        assert _fp(ownership_links=links) != _fp(), change


def test_link_order_does_not_change_it_and_pct_scale_is_normalised():
    links = _inputs()["ownership_links"]
    assert _fp(ownership_links=list(reversed(links))) == _fp()
    links[0] = dict(links[0], ownership_pct="80.00")
    assert _fp(ownership_links=links) == _fp()


def test_a_link_of_an_entity_outside_the_cycle_does_not_change_it():
    links = _inputs()["ownership_links"] + [_link(entity="ZZXX", pct=5)]
    assert _fp(ownership_links=links) == _fp()


def test_a_link_whose_owner_is_a_cycle_entity_counts():
    links = _inputs()["ownership_links"] + [_link(entity="ZZXX", owner_entity="ZZNL", owner_group="")]
    assert _fp(ownership_links=links) != _fp()
