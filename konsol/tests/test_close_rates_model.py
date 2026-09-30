"""Rates model, pure: konsol/close/rates_model.py (konsol#305 E401).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "rates_model_under_test", os.path.join(APP_DIR, "close", "rates_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _doc(name, from_currency, to_currency, rate_type, rate, docstatus, owner,
         modified, quote=None, quoted_per=1, erp_rate=None, change_reason=None, source=None):
    return {
        "name": name,
        "from_currency": from_currency,
        "to_currency": to_currency,
        "rate_type": rate_type,
        "quote": quote if quote is not None else rate,
        "quoted_per": quoted_per,
        "rate": rate,
        "erp_rate": erp_rate,
        "docstatus": docstatus,
        "owner": owner,
        "modified": modified,
        "change_reason": change_reason,
        "source": source,
    }


def _earlier(name, from_currency, to_currency, rate_type, rate, fiscal_year, fiscal_period, quoted_per=1):
    return {
        "name": name,
        "from_currency": from_currency,
        "to_currency": to_currency,
        "rate_type": rate_type,
        "quote": rate,
        "quoted_per": quoted_per,
        "rate": rate,
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
    }


def _no_op_move_problem(rate, previous, erp_rate, unit="", threshold=None):
    return None


def test_required_pair_with_no_docs_is_missing_both_cells_and_flag_never_called():
    calls = []

    def move_problem(rate, previous, erp_rate, unit="", threshold=None):
        calls.append((rate, previous, erp_rate, unit, threshold))
        return "SHOULD NOT BE CALLED"

    result = M.grid(required={("EUR", "GBP")}, docs=[], earlier=[], threshold=0.5, move_problem=move_problem)
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["required"] is True
    for cell in (row["closing"], row["average"]):
        assert cell["status"] == "missing"
        assert cell["flag"] is None
        assert cell["name"] is None
    assert calls == []


def test_approved_closing_plus_draft_average_statuses_and_owner_carried():
    docs = [
        _doc("GER-1", "EUR", "GBP", "Closing", 0.86, 1, "alice@example.com", "2026-09-01 10:00:00"),
        _doc("GER-2", "EUR", "GBP", "Average", 0.85, 0, "bob@example.com", "2026-09-02 10:00:00"),
    ]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5, move_problem=_no_op_move_problem)
    row = result["rows"][0]
    assert row["closing"]["status"] == "approved"
    assert row["closing"]["owner"] == "alice@example.com"
    assert row["average"]["status"] == "awaiting_approval"
    assert row["average"]["owner"] == "bob@example.com"


def test_previous_is_latest_earlier_period_with_label_and_delta():
    docs = [_doc("GER-9", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00")]
    earlier = [
        _earlier("GER-P05", "EUR", "GBP", "Closing", 0.80, 2025, 5),
        _earlier("GER-P03", "EUR", "GBP", "Closing", 0.70, 2025, 3),
    ]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=earlier, threshold=0.5,
                     move_problem=_no_op_move_problem)
    cell = result["rows"][0]["closing"]
    assert cell["previous"]["label"] == "FY2025 P5 (GER-P05)"
    assert cell["previous"]["rate"] == 0.80
    assert abs(cell["delta"] - (0.90 / 0.80 - 1)) < 1e-12


def test_missing_previous_rate_gives_none_delta_and_previous_none_arg():
    calls = []

    def move_problem(rate, previous, erp_rate, unit="", threshold=None):
        calls.append(previous)
        return None

    docs = [_doc("GER-1", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00")]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5, move_problem=move_problem)
    cell = result["rows"][0]["closing"]
    assert cell["previous"] is None
    assert cell["delta"] is None
    assert calls == [None]


def test_undeclared_threshold_is_never_defaulted():
    seen_thresholds = []

    def move_problem(rate, previous, erp_rate, unit="", threshold=None):
        seen_thresholds.append(threshold)
        if threshold is None:
            return "DECLARE-SENTINEL"
        return None

    docs = [
        _doc("GER-1", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00"),
        _doc("GER-2", "EUR", "GBP", "Average", 0.91, 1, "alice@example.com", "2026-09-01 10:00:00"),
    ]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=None, move_problem=move_problem)
    assert result["rows"][0]["closing"]["flag"] == "DECLARE-SENTINEL"
    assert result["rows"][0]["average"]["flag"] == "DECLARE-SENTINEL"
    assert seen_thresholds == [None, None]


def test_threshold_passed_through_and_unit_is_to_per_from():
    calls = []

    def move_problem(rate, previous, erp_rate, unit="", threshold=None):
        calls.append((rate, previous, erp_rate, unit, threshold))
        return None

    docs = [_doc("GER-1", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00",
                  erp_rate=0.88)]
    M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.3, move_problem=move_problem)
    assert len(calls) == 1
    rate, previous, erp_rate, unit, threshold = calls[0]
    assert rate == 0.90
    assert erp_rate == 0.88
    assert unit == "GBP per EUR"
    assert threshold == 0.3


def test_doc_for_pair_with_no_required_pair_goes_to_unrequired():
    docs = [_doc("GER-1", "JPY", "GBP", "Closing", 0.0066, 1, "alice@example.com", "2026-09-01 10:00:00")]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5,
                     move_problem=_no_op_move_problem)
    row_pairs = [(r["from_currency"], r["to_currency"]) for r in result["rows"]]
    assert ("JPY", "GBP") not in row_pairs
    assert len(result["unrequired"]) == 1
    unrequired_row = result["unrequired"][0]
    assert (unrequired_row["from_currency"], unrequired_row["to_currency"]) == ("JPY", "GBP")
    assert unrequired_row["required"] is False


def test_required_none_puts_every_docs_pair_in_rows_and_unrequired_is_empty():
    docs = [
        _doc("GER-1", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00"),
        _doc("GER-2", "USD", "GBP", "Closing", 0.79, 1, "alice@example.com", "2026-09-01 10:00:00"),
    ]
    result = M.grid(required=None, docs=docs, earlier=[], threshold=0.5, move_problem=_no_op_move_problem)
    assert result["unrequired"] == []
    assert len(result["rows"]) == 2
    for row in result["rows"]:
        assert row["required"] is None


def test_two_drafts_shows_latest_modified_other_in_extra_drafts():
    docs = [
        _doc("GER-OLD", "EUR", "GBP", "Closing", 0.90, 0, "alice@example.com", "2026-09-01 09:00:00"),
        _doc("GER-NEW", "EUR", "GBP", "Closing", 0.91, 0, "bob@example.com", "2026-09-01 11:00:00"),
    ]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5,
                     move_problem=_no_op_move_problem)
    cell = result["rows"][0]["closing"]
    assert cell["name"] == "GER-NEW"
    assert cell["extra_drafts"] == ["GER-OLD"]


def test_approved_plus_draft_shows_approved_draft_in_extra_drafts():
    docs = [
        _doc("GER-APPROVED", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 09:00:00"),
        _doc("GER-DRAFT", "EUR", "GBP", "Closing", 0.91, 0, "bob@example.com", "2026-09-01 11:00:00"),
    ]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5,
                     move_problem=_no_op_move_problem)
    cell = result["rows"][0]["closing"]
    assert cell["name"] == "GER-APPROVED"
    assert cell["status"] == "approved"
    assert cell["extra_drafts"] == ["GER-DRAFT"]


def test_unknown_rate_type_raises_value_error_naming_it():
    docs = [_doc("GER-1", "EUR", "GBP", "Spot", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00")]
    try:
        M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5, move_problem=_no_op_move_problem)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Spot" in str(e)


def test_summary_counts_add_up_to_twice_row_count():
    docs = [
        _doc("GER-1", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00"),
        _doc("GER-2", "USD", "GBP", "Average", 0.79, 0, "alice@example.com", "2026-09-01 10:00:00"),
    ]
    result = M.grid(required={("EUR", "GBP"), ("USD", "GBP"), ("JPY", "GBP")}, docs=docs, earlier=[],
                     threshold=0.5, move_problem=_no_op_move_problem)
    summary = result["summary"]
    assert sum(summary.values()) == 2 * len(result["rows"])
    assert sum(summary.values()) == 6


def test_module_imports_no_frappe():
    """Same contract as fiscal_status_model, extended to refuse a ``konsol``
    import too (rates_model.py is loaded by path, like the rate rule it
    takes injected)."""
    with open(M.__file__) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("frappe") or a.name.startswith("konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("frappe")
            assert not module.startswith("konsol")
