"""konsol#338 (#338-1, Deepak Pai 6 Oct 2026): the numbers a close run checked
are fingerprinted, so a completed build voids a signature only where those
numbers changed.

``konsol/close/fingerprint_model.py`` is pure (no frappe, no ClickHouse):
loaded by path like the other ``*_model.py`` tests.

Coordinator's calls (#338 decision comment): a period's fingerprint covers
every period up to and including it (the balance sheet is cumulative); it is
built per consolidation group from ``gold_fully_consolidated_tb`` aggregated
to (entity, account, adjustment_type); amounts are rounded to cents; the
per-key hashes are summed, not XORed.
"""
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PY = os.path.join(APP_DIR, "close", "fingerprint_model.py")

_spec = importlib.util.spec_from_file_location("fingerprint_model_under_test", MODEL_PY)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _row(fp, amount, group="G1", fy=2025, entity="E1", account="1000", adj="entity"):
    return {"group": group, "fiscal_year": fy, "fiscal_period": fp, "entity": entity,
            "account": account, "adjustment_type": adj, "amount": amount}


PERIODS = [(2025, p) for p in range(1, 7)]


def _base():
    rows = []
    for p in range(1, 7):
        rows.append(_row(p, 100.0 * p))
        rows.append(_row(p, -100.0 * p, account="2000"))
        rows.append(_row(p, 12.34, entity="E2", adj="elimination"))
    rows.append(_row(3, 55.5, group="G2"))
    return rows


def test_identical_inputs_give_identical_fingerprints_in_any_row_order():
    a = M.group_fingerprints(_base(), PERIODS)
    b = M.group_fingerprints(list(reversed(_base())), PERIODS)
    assert a == b
    assert M.run_fingerprint(_base(), 2025, 4) == M.run_fingerprint(list(reversed(_base())), 2025, 4)
    assert M.run_fingerprint(_base(), 2025, 4).startswith(M.VERSION + ":")


def test_a_one_cent_change_in_an_earlier_period_changes_every_later_fingerprint():
    before = M.group_fingerprints(_base(), PERIODS)
    rows = _base()
    rows[3 * 2]["amount"] += 0.01   # P03's first row, group G1
    after = M.group_fingerprints(rows, PERIODS)
    for fy, fp in PERIODS:
        if fp < 3:
            assert after[("G1", fy, fp)] == before[("G1", fy, fp)], fp
        else:
            assert after[("G1", fy, fp)] != before[("G1", fy, fp)], fp
    # The other group's numbers did not change.
    assert after[("G2", 2025, 6)] == before[("G2", 2025, 6)]
    for fp in range(3, 7):
        assert M.run_fingerprint(rows, 2025, fp) != M.run_fingerprint(_base(), 2025, fp), fp
    for fp in range(1, 3):
        assert M.run_fingerprint(rows, 2025, fp) == M.run_fingerprint(_base(), 2025, fp), fp


def test_float_noise_below_half_a_cent_does_not_change_it():
    rows = _base()
    for r in rows:
        r["amount"] += 1e-7
    rows[0]["amount"] += 0.004
    assert M.group_fingerprints(rows, PERIODS) == M.group_fingerprints(_base(), PERIODS)


def test_row_splits_of_the_same_key_give_the_same_fingerprint():
    rows = _base()
    first = rows.pop(0)
    rows.append(dict(first, amount=first["amount"] * 0.3))
    rows.append(dict(first, amount=first["amount"] * 0.7))
    assert M.group_fingerprints(rows, PERIODS) == M.group_fingerprints(_base(), PERIODS)


def test_a_null_amount_raises():
    rows = _base()
    rows[5]["amount"] = None
    with pytest.raises(ValueError) as info:
        M.group_fingerprints(rows, PERIODS)
    assert "NULL" in str(info.value)


def test_a_key_that_rounds_to_zero_reads_as_absent():
    rows = _base() + [_row(2, 0.001, account="9999")]
    assert M.group_fingerprints(rows, PERIODS) == M.group_fingerprints(_base(), PERIODS)


def test_a_period_with_no_rows_of_its_own_carries_the_earlier_balance():
    rows = [r for r in _base() if r["fiscal_period"] != 6]
    fps = M.group_fingerprints(rows, PERIODS)
    assert fps[("G1", 2025, 6)] == fps[("G1", 2025, 5)]
    assert M.run_fingerprint(rows, 2025, 6) == M.run_fingerprint(rows, 2025, 5)


def test_later_periods_and_later_years_are_not_covered():
    rows = _base() + [_row(1, 999.0, fy=2026)]
    assert M.run_fingerprint(rows, 2025, 6) == M.run_fingerprint(_base(), 2025, 6)
    assert M.run_fingerprint(rows, 2026, 1) != M.run_fingerprint(_base(), 2026, 1)


def test_moving_an_amount_between_groups_changes_the_run_fingerprint():
    rows = _base()
    moved = [dict(r, group="G2") if r["group"] == "G1" and r["fiscal_period"] == 1
             and r["account"] == "1000" else r for r in rows]
    assert M.run_fingerprint(moved, 2025, 6) != M.run_fingerprint(rows, 2025, 6)


def test_swapping_amounts_between_keys_changes_it():
    """Sum of per-key hashes, keyed by the amount too: two keys trading their
    amounts is a change."""
    rows = [_row(1, 10.0), _row(1, 20.0, account="2000")]
    swapped = [_row(1, 20.0), _row(1, 10.0, account="2000")]
    assert M.run_fingerprint(rows, 2025, 1) != M.run_fingerprint(swapped, 2025, 1)


def test_numbers_never_read_as_no_numbers():
    """One key, or the same key and amount in two groups, never reads as an
    empty warehouse."""
    one = [_row(1, 10.0)]
    assert M.run_fingerprint(one, 2025, 1) != M.run_fingerprint([], 2025, 1)
    assert M.run_fingerprint(one + [_row(1, 10.0, group="G2")], 2025, 1) != (
        M.run_fingerprint([], 2025, 1))


def test_sums_not_xor_in_the_source():
    with open(MODEL_PY) as f:
        source = f.read()
    assert " ^ " not in source and "^=" not in source, (
        "fingerprint_model must sum the per-key hashes, not XOR them")
