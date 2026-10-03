"""Approvals model, pure: konsol/close/approvals_model.py (konsol#305 A08,
A09).

Loaded by path; the module imports nothing from frappe or konsol. It loads
``rates_model.py`` and ``close_policy_model.py`` as siblings (by path, like
the module under test does), so these tests exercise the real
``rates_model.pending_items`` / ``approve_mode`` and the real
``close_policy_model.self_approval_problem`` / ``APPROVER_ROLES`` — never a
copy of either rule.
"""
import ast
import datetime
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _load("test_close_approvals_model_under_test", os.path.join("close", "approvals_model.py"))
RM = _load("test_close_approvals_model_rates_model", os.path.join("close", "rates_model.py"))
CPM = _load("test_close_approvals_model_close_policy_model", os.path.join("close", "close_policy_model.py"))

SELF_APPROVAL_PROBLEM = CPM.self_approval_problem
APPROVER_ROLES = CPM.APPROVER_ROLES

#: Mirrors close_event.ENTITY_FIELDS (close_event.py:53-61), injected.
ENTITY_FIELDS = {
    "Ownership Period": "data_area_id",
    "Historical Equity Rate": "data_area_id",
    "Business Combination": "acquired_entity",
    "Business Disposal": "disposed_entity",
    "Group Exchange Rate": None,
    "Consolidation Journal": None,
    "IC Balance": None,
}


def _journal(name, fy, fp, adjustment_type, description, duration, owner, created,
             total_debit=18500.0, currency="USD", lines=None, effect=None):
    return {
        "name": name, "owner": owner, "created": created,
        "fiscal_year": fy, "fiscal_period": fp,
        "adjustment_type": adjustment_type, "description": description,
        "total_debit": total_debit, "currency": currency,
        "reverse_fiscal_year": 0, "reverse_fiscal_period": 0,
        "duration": duration,
        "lines": lines if lines is not None else [{"main_account": "6100", "debit_amount": total_debit}],
        "effect": effect if effect is not None else {"headings": [], "sections": [], "no_heading": 0},
    }


def _ger(name, from_currency, to_currency, rate_type, fy, fp, quote_label, owner, created,
          change_reason=None):
    return {
        "name": name, "owner": owner, "created": created,
        "from_currency": from_currency, "to_currency": to_currency, "rate_type": rate_type,
        "fiscal_year": fy, "fiscal_period": fp, "quote_label": quote_label,
        "change_reason": change_reason,
    }


def _ic_balance(name, selling, buying, fy, fp, ic_sales_amount, owner, created,
                  ending_inventory_from_ic=0):
    return {
        "name": name, "owner": owner, "created": created,
        "selling_entity": selling, "buying_entity": buying,
        "fiscal_year": fy, "fiscal_period": fp,
        "ic_sales_amount": ic_sales_amount,
        "ending_inventory_from_ic": ending_inventory_from_ic,
    }


def _bc(name, group, acquired_entity, date, pct, goodwill, owner, created):
    return {
        "name": name, "owner": owner, "created": created,
        "consolidation_group": group, "acquired_entity": acquired_entity,
        "acquisition_date": date, "share_acquired_pct": pct, "goodwill": goodwill,
    }


def _bd(name, group, disposed_entity, date, pct, total_proceeds, owner, created):
    return {
        "name": name, "owner": owner, "created": created,
        "consolidation_group": group, "disposed_entity": disposed_entity,
        "disposal_date": date, "share_disposed_pct": pct, "total_proceeds": total_proceeds,
    }


def _her(name, consolidation_group, data_area_id, main_account, rate_date, historical_rate,
         owner, created):
    return {
        "name": name, "consolidation_group": consolidation_group, "data_area_id": data_area_id,
        "main_account": main_account, "rate_date": rate_date, "historical_rate": historical_rate,
        "owner": owner, "created": created,
    }


def _op(name, consolidation_group, data_area_id, effective_date, end_date, ownership_pct,
        consolidation_method, owner, created):
    return {
        "name": name, "consolidation_group": consolidation_group, "data_area_id": data_area_id,
        "effective_date": effective_date, "end_date": end_date, "ownership_pct": ownership_pct,
        "consolidation_method": consolidation_method, "owner": owner, "created": created,
    }


def _all_seven_docs():
    return {
        "Ownership Period": [_op("OP-1", "G1", "DE02", "2026-01-01", None, 80, "Full",
                                  "alice", "2026-10-01T06:30:00")],
        "Historical Equity Rate": [_her("HER-1", "G1", "DE02", "4000 - Revenue", "2026-09-30",
                                         1.2, "alice", "2026-10-01T07:00:00")],
        "Business Combination": [_bc("BC-1", "G1", "DE02", "2026-01-15", 60, 1000,
                                      "alice", "2026-10-01T08:00:00")],
        "Consolidation Journal": [_journal("CJ-1", 2026, 7, "topside", "Accrue bonus\nmore detail",
                                            "This period only, no reversal", "alice",
                                            "2026-10-01T09:00:00")],
        "Group Exchange Rate": [_ger("GER-1", "USD", "EUR", "Closing", 2026, 7,
                                      "1.10 EUR per USD", "alice", "2026-10-01T10:00:00")],
        "IC Balance": [_ic_balance("ICB-1", "UK01", "DE02", 2026, 7, 1500.0,
                                    "alice", "2026-10-01T11:00:00")],
        "Business Disposal": [_bd("BD-1", "G1", "DE02", "2026-02-01", 40, 500,
                                   "alice", "2026-10-01T12:00:00")],
    }


def _preparers_for(docs, preparer_sets_by_doctype=None):
    """``{(doctype, name): frozenset}`` — owner-only unless overridden."""
    preparer_sets_by_doctype = preparer_sets_by_doctype or {}
    refs = {}
    for doctype, rows in docs.items():
        for doc in rows:
            refs[(doctype, doc["name"])] = preparer_sets_by_doctype.get(
                (doctype, doc["name"]), frozenset({doc["owner"]}))
    return refs


def _modified_for(docs, overrides=None):
    """``{(doctype, name): <modified>}`` (A09) — defaults to the doc's own
    ``created`` unless overridden. With no rejections this never matters
    (``sent_back`` short-circuits on a missing rejection), but every
    pending document still needs an entry: a missing ref raises KeyError,
    the same rule as ``preparers_by_ref``."""
    overrides = overrides or {}
    out = {}
    for doctype, rows in docs.items():
        for doc in rows:
            out[(doctype, doc["name"])] = overrides.get(
                (doctype, doc["name"]), doc["created"])
    return out


def test_seven_doctypes_give_seven_items_in_created_order_with_inline_and_desk():
    docs = _all_seven_docs()
    result = M.queue_items(docs, _preparers_for(docs), "alice", ("EPM Admin",),
                            "Allowed with reason", APPROVER_ROLES, SELF_APPROVAL_PROBLEM,
                            ENTITY_FIELDS, None, {}, _modified_for(docs))
    assert result["hidden"] == 0
    names = [item["name"] for item in result["items"]]
    assert names == ["OP-1", "HER-1", "BC-1", "CJ-1", "GER-1", "ICB-1", "BD-1"]

    by_name = {item["name"]: item for item in result["items"]}

    for bc_bd in ("BC-1", "BD-1"):
        assert by_name[bc_bd]["inline"] is False
    for other in ("OP-1", "HER-1", "CJ-1", "GER-1", "ICB-1"):
        assert by_name[other]["inline"] is True
        assert by_name[other]["desk"] is None
    assert by_name["BC-1"]["desk"] == "/app/business-combination/BC-1"
    assert by_name["BD-1"]["desk"] == "/app/business-disposal/BD-1"

    cj = by_name["CJ-1"]
    assert cj["kind_label"] == "Adjustment · CJ-1"
    assert cj["title"] == "Accrue bonus"
    assert cj["detail"] == "Topside · FY2026 P07 · This period only, no reversal"
    assert cj["lines"] == docs["Consolidation Journal"][0]["lines"]
    assert cj["effect"] == docs["Consolidation Journal"][0]["effect"]
    assert cj["total_debit"] == 18500.0
    assert cj["currency"] == "USD"

    ger = by_name["GER-1"]
    assert ger["kind_label"] == "Group rate · USD→EUR Closing"
    assert ger["title"] == "1.10 EUR per USD"
    assert ger["detail"] == "FY2026 P07"

    icb = by_name["ICB-1"]
    assert icb["kind_label"] == "IC balance · UK01 → DE02"
    assert icb["title"] == icb["kind_label"]
    assert icb["detail"] == "FY2026 P07 · IC sales 1500.00"

    bc = by_name["BC-1"]
    assert bc["kind_label"] == "Business combination · DE02"
    assert bc["title"] == bc["kind_label"]
    assert bc["detail"] == "G1 · 60% from 2026-01-15"

    bd = by_name["BD-1"]
    assert bd["kind_label"] == "Business disposal · DE02"
    assert bd["title"] == bd["kind_label"]
    assert bd["detail"] == "G1 · 40% from 2026-02-01"


def test_ger_change_reason_is_appended_to_detail():
    docs = {"Group Exchange Rate": [_ger("GER-2", "USD", "GBP", "Average", 2026, 7,
                                          "0.80 GBP per USD", "alice", "2026-10-01T10:00:00",
                                          change_reason="ERP quote moved")]}
    result = M.queue_items(docs, _preparers_for(docs), "alice", ("EPM Admin",),
                            "Allowed with reason", APPROVER_ROLES, SELF_APPROVAL_PROBLEM,
                            ENTITY_FIELDS, None, {}, _modified_for(docs))
    assert result["items"][0]["detail"] == "FY2026 P07 · ERP quote moved"


def test_journal_no_description_line_gives_no_description_title():
    docs = {"Consolidation Journal": [_journal("CJ-2", 2026, 7, "reclassification", "   \n  ",
                                                "Reverses in FY2026 P08", "alice",
                                                "2026-10-01T09:00:00")]}
    result = M.queue_items(docs, _preparers_for(docs), "alice", ("EPM Admin",),
                            "Allowed with reason", APPROVER_ROLES, SELF_APPROVAL_PROBLEM,
                            ENTITY_FIELDS, None, {}, _modified_for(docs))
    item = result["items"][0]
    assert item["title"] == "(no description)"
    assert item["detail"] == "Reclassification · FY2026 P07 · Reverses in FY2026 P08"


def test_r2_analyst_roles_give_not_approver_on_every_item():
    """Failure path, R2: only the Close Lead approves."""
    docs = _all_seven_docs()
    result = M.queue_items(docs, _preparers_for(docs), "alice", ("EPM Analyst",),
                            "Allowed with reason", APPROVER_ROLES, SELF_APPROVAL_PROBLEM,
                            ENTITY_FIELDS, None, {}, _modified_for(docs))
    for item in result["items"]:
        assert item["approve"]["mode"] == "not_approver", item


def test_r5_blocked_self_prepared_is_refused():
    """Failure path, R5."""
    docs = {"Consolidation Journal": [_journal("CJ-3", 2026, 7, "topside", "Accrue",
                                                "This period only, no reversal", "alice",
                                                "2026-10-01T09:00:00")]}
    result = M.queue_items(docs, _preparers_for(docs), "alice", ("EPM Admin",),
                            "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM,
                            ENTITY_FIELDS, None, {}, _modified_for(docs))
    approve = result["items"][0]["approve"]
    assert approve["mode"] == "refused"
    assert "blocks self-approval" in approve["message"]


def test_allowed_with_reason_self_prepared_is_reason_others_direct():
    docs = {"Consolidation Journal": [
        _journal("CJ-4", 2026, 7, "topside", "Accrue", "This period only, no reversal",
                 "alice", "2026-10-01T09:00:00"),
        _journal("CJ-5", 2026, 7, "topside", "Accrue more", "This period only, no reversal",
                 "bob", "2026-10-01T09:05:00"),
    ]}
    refs = _preparers_for(docs)
    result = M.queue_items(docs, refs, "alice", ("EPM Admin",), "Allowed with reason",
                            APPROVER_ROLES, SELF_APPROVAL_PROBLEM, ENTITY_FIELDS, None, {}, _modified_for(docs))
    by_name = {item["name"]: item for item in result["items"]}
    assert by_name["CJ-4"]["approve"]["mode"] == "reason"
    assert by_name["CJ-5"]["approve"]["mode"] == "direct"


def test_w2_14_editor_approver_is_self_prepared_under_allowed_with_reason():
    """#305-W2-14: a Close Lead who edited an Analyst's GER is self-prepared."""
    docs = {"Group Exchange Rate": [_ger("GER-3", "USD", "EUR", "Closing", 2026, 7,
                                          "1.10 EUR per USD", "bob", "2026-10-01T10:00:00")]}
    refs = _preparers_for(docs, {("Group Exchange Rate", "GER-3"): frozenset({"bob", "lead"})})
    result = M.queue_items(docs, refs, "lead", ("EPM Admin",), "Allowed with reason",
                            APPROVER_ROLES, SELF_APPROVAL_PROBLEM, ENTITY_FIELDS, None, {}, _modified_for(docs))
    assert result["items"][0]["approve"]["mode"] == "reason"


def test_scope_hides_entity_items_outside_allowed():
    """Failure path, scope (W2-9, W2-10)."""
    docs = {
        "Historical Equity Rate": [_her("HER-2", "G1", "DE02", "4000 - Revenue",
                                         "2026-09-30", 1.2, "alice", "2026-10-01T07:00:00")],
        "Business Combination": [_bc("BC-2", "G1", "DE02", "2026-01-15", 60, 1000,
                                      "alice", "2026-10-01T08:00:00")],
        "Group Exchange Rate": [_ger("GER-4", "USD", "EUR", "Closing", 2026, 7,
                                      "1.10 EUR per USD", "alice", "2026-10-01T10:00:00")],
        "Consolidation Journal": [_journal("CJ-6", 2026, 7, "topside", "Accrue",
                                            "This period only, no reversal", "alice",
                                            "2026-10-01T09:00:00")],
        "IC Balance": [_ic_balance("ICB-2", "UK01", "DE02", 2026, 7, 1500.0,
                                    "alice", "2026-10-01T11:00:00")],
    }
    result = M.queue_items(docs, _preparers_for(docs), "alice", ("EPM Admin",),
                            "Allowed with reason", APPROVER_ROLES, SELF_APPROVAL_PROBLEM,
                            ENTITY_FIELDS, {"UK01"}, {}, _modified_for(docs))
    assert result["hidden"] == 2
    names = {item["name"] for item in result["items"]}
    assert names == {"GER-4", "CJ-6", "ICB-2"}


def test_missing_preparers_ref_raises_key_error():
    """Failure path: never treated as owner-only."""
    docs = {"Consolidation Journal": [_journal("CJ-7", 2026, 7, "topside", "Accrue",
                                                "This period only, no reversal", "alice",
                                                "2026-10-01T09:00:00")]}
    try:
        M.queue_items(docs, {}, "alice", ("EPM Admin",), "Allowed with reason",
                      APPROVER_ROLES, SELF_APPROVAL_PROBLEM, ENTITY_FIELDS, None, {}, _modified_for(docs))
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_unknown_doctype_raises_value_error_naming_it():
    """Failure path: never shown as another kind."""
    docs = {"Budget Cycle": [{"name": "BUD-1", "owner": "alice", "created": "2026-10-01T09:00:00"}]}
    try:
        M.queue_items(docs, {}, "alice", ("EPM Admin",), "Allowed with reason",
                      APPROVER_ROLES, SELF_APPROVAL_PROBLEM, ENTITY_FIELDS, None, {}, _modified_for(docs))
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Budget Cycle" in str(exc)


def test_her_and_op_titles_equal_rates_model_pending_items_own():
    her = [_her("HER-3", "G1", "DE02", "4000 - Revenue", "2026-09-30", 1.2,
                "alice", "2026-10-01T07:00:00")]
    ops = [_op("OP-2", "G1", "DE02", "2026-01-01", None, 80, "Full",
               "alice", "2026-10-01T06:30:00")]
    docs = {"Historical Equity Rate": her, "Ownership Period": ops}
    refs = _preparers_for(docs)
    preparers_by_name = {"HER-3": frozenset({"alice"}), "OP-2": frozenset({"alice"})}
    expected = RM.pending_items(her, ops, preparers_by_name, "lead", ("EPM Admin",),
                                 "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    expected_titles = {item["name"]: item["title"] for item in expected}

    result = M.queue_items(docs, refs, "lead", ("EPM Admin",), "Blocked",
                            APPROVER_ROLES, SELF_APPROVAL_PROBLEM, ENTITY_FIELDS, None, {}, _modified_for(docs))
    actual_titles = {item["name"]: item["title"] for item in result["items"]}
    assert actual_titles == expected_titles


def test_journal_item_carries_lines_and_effect_unchanged():
    lines = [{"main_account": "6100", "debit_amount": 18500.0, "credit_amount": 0}]
    effect = {"headings": [{"section": "Profit and Loss", "heading": "6000",
                             "heading_name": "Operating expenses", "net_debit": 18500.0}],
              "sections": [{"section": "Profit and Loss", "net_debit": 18500.0}],
              "no_heading": 0}
    docs = {"Consolidation Journal": [_journal("CJ-8", 2026, 7, "topside", "Accrue",
                                                "This period only, no reversal", "alice",
                                                "2026-10-01T09:00:00", lines=lines, effect=effect)]}
    result = M.queue_items(docs, _preparers_for(docs), "alice", ("EPM Admin",),
                            "Allowed with reason", APPROVER_ROLES, SELF_APPROVAL_PROBLEM,
                            ENTITY_FIELDS, None, {}, _modified_for(docs))
    item = result["items"][0]
    assert item["lines"] == lines
    assert item["effect"] == effect


def test_rejection_after_modified_is_sent_back_and_excluded_from_waiting_for_me():
    """A09: an item whose newest rejection is later than its modified is
    sent_back and carries the rejection. Failure path: it is not counted
    by waiting_for_me."""
    docs = {"Group Exchange Rate": [_ger("GER-5", "USD", "EUR", "Closing", 2026, 7,
                                          "1.10 EUR per USD", "alice", "2026-10-01T10:00:00")]}
    refs = _preparers_for(docs)
    modified_by_ref = {("Group Exchange Rate", "GER-5"): datetime.datetime(2026, 10, 2, 8, 0, 0)}
    rejections = {("Group Exchange Rate", "GER-5"): {
        "reason": "wrong rate", "actor": "lead",
        "at": datetime.datetime(2026, 10, 2, 9, 0, 0)}}
    result = M.queue_items(docs, refs, "lead", ("EPM Admin",), "Allowed with reason",
                            APPROVER_ROLES, SELF_APPROVAL_PROBLEM, ENTITY_FIELDS, None,
                            rejections, modified_by_ref)
    item = result["items"][0]
    assert item["sent_back"] is True
    assert item["rejection"]["reason"] == "wrong rate"
    assert M.waiting_for_me(result["items"]) == {"count": 0, "oldest": None}


def test_modified_after_rejection_is_pending_again_and_counted():
    docs = {"Group Exchange Rate": [_ger("GER-6", "USD", "EUR", "Closing", 2026, 7,
                                          "1.10 EUR per USD", "alice", "2026-10-01T10:00:00")]}
    refs = _preparers_for(docs)
    modified_by_ref = {("Group Exchange Rate", "GER-6"): datetime.datetime(2026, 10, 2, 10, 0, 0)}
    rejections = {("Group Exchange Rate", "GER-6"): {
        "reason": "wrong rate", "actor": "lead",
        "at": datetime.datetime(2026, 10, 2, 9, 0, 0)}}
    result = M.queue_items(docs, refs, "lead", ("EPM Admin",), "Allowed with reason",
                            APPROVER_ROLES, SELF_APPROVAL_PROBLEM, ENTITY_FIELDS, None,
                            rejections, modified_by_ref)
    item = result["items"][0]
    assert item["sent_back"] is False
    assert item["rejection"] is None
    assert M.waiting_for_me(result["items"]) == {"count": 1, "oldest": item["created"]}


def test_no_rejection_item_is_sent_back_false_rejection_none():
    docs = {"Group Exchange Rate": [_ger("GER-7", "USD", "EUR", "Closing", 2026, 7,
                                          "1.10 EUR per USD", "alice", "2026-10-01T10:00:00")]}
    refs = _preparers_for(docs)
    result = M.queue_items(docs, refs, "lead", ("EPM Admin",), "Allowed with reason",
                            APPROVER_ROLES, SELF_APPROVAL_PROBLEM, ENTITY_FIELDS, None,
                            {}, _modified_for(docs))
    item = result["items"][0]
    assert item["sent_back"] is False
    assert item["rejection"] is None


def test_refused_and_not_approver_items_never_counted_by_waiting_for_me():
    """Failure path, E6-P11: a Blocked self-prepared item (refused) and an
    EPM Analyst's items (not_approver) are never counted."""
    docs = {"Consolidation Journal": [_journal("CJ-9", 2026, 7, "topside", "Accrue",
                                                "This period only, no reversal", "alice",
                                                "2026-10-01T09:00:00")]}
    refused = M.queue_items(docs, _preparers_for(docs), "alice", ("EPM Admin",),
                             "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM, ENTITY_FIELDS,
                             None, {}, _modified_for(docs))
    assert refused["items"][0]["approve"]["mode"] == "refused"
    assert M.waiting_for_me(refused["items"]) == {"count": 0, "oldest": None}

    not_approver = M.queue_items(docs, _preparers_for(docs), "alice", ("EPM Analyst",),
                                  "Allowed with reason", APPROVER_ROLES, SELF_APPROVAL_PROBLEM,
                                  ENTITY_FIELDS, None, {}, _modified_for(docs))
    assert not_approver["items"][0]["approve"]["mode"] == "not_approver"
    assert M.waiting_for_me(not_approver["items"]) == {"count": 0, "oldest": None}


def test_waiting_for_me_oldest_is_earliest_created_among_counted():
    docs = {
        "Group Exchange Rate": [_ger("GER-8", "USD", "EUR", "Closing", 2026, 7,
                                      "1.10 EUR per USD", "bob", "2026-10-01T10:00:00")],
        "Consolidation Journal": [_journal("CJ-10", 2026, 7, "topside", "Accrue",
                                            "This period only, no reversal", "bob",
                                            "2026-10-01T06:00:00")],
    }
    result = M.queue_items(docs, _preparers_for(docs), "lead", ("EPM Admin",),
                            "Allowed with reason", APPROVER_ROLES, SELF_APPROVAL_PROBLEM,
                            ENTITY_FIELDS, None, {}, _modified_for(docs))
    waiting = M.waiting_for_me(result["items"])
    assert waiting["count"] == 2
    assert waiting["oldest"] == "2026-10-01T06:00:00"


# --- is_sent_back (A21): the one rule for "sent back" -------------------------------


def test_is_sent_back_true_when_rejection_after_modified():
    rejection = {"reason": "wrong rate", "actor": "lead",
                 "at": datetime.datetime(2026, 10, 2, 9, 0, 0)}
    assert M.is_sent_back(rejection, datetime.datetime(2026, 10, 2, 8, 0, 0)) is True


def test_is_sent_back_false_when_rejection_is_none():
    """Failure path: a document with no rejection is never sent back."""
    assert M.is_sent_back(None, datetime.datetime(2026, 10, 2, 8, 0, 0)) is False


def test_is_sent_back_false_when_modified_after_rejection():
    """Failure path: the preparer's next save moves it past the rejection."""
    rejection = {"reason": "wrong rate", "actor": "lead",
                 "at": datetime.datetime(2026, 10, 2, 9, 0, 0)}
    assert M.is_sent_back(rejection, datetime.datetime(2026, 10, 2, 10, 0, 0)) is False


def test_is_sent_back_raises_when_modified_is_missing():
    """Failure path: never guessed."""
    rejection = {"reason": "wrong rate", "actor": "lead",
                 "at": datetime.datetime(2026, 10, 2, 9, 0, 0)}
    try:
        M.is_sent_back(rejection, None)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# --- sent_back_items (A21): the caller's own drafts that were sent back -------------


def _sb_ger(name, modified, from_currency="USD", to_currency="EUR", rate_type="Closing",
            fiscal_year=2026, fiscal_period=7, quote_label="1.10 EUR per USD", change_reason=None):
    return {"name": name, "modified": modified, "from_currency": from_currency,
            "to_currency": to_currency, "rate_type": rate_type, "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period, "quote_label": quote_label,
            "change_reason": change_reason}


def _sb_journal(name, modified, fiscal_year=2026, fiscal_period=7, adjustment_type="topside",
                 description="Accrue bonus", total_debit=100.0, currency="USD",
                 reverse_fiscal_year=0, reverse_fiscal_period=0):
    """A journal row shaped as ``sent_back_for`` fetches it (A21): no
    ``duration``, ``lines`` or ``effect`` — those are never read here."""
    return {"name": name, "modified": modified, "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period, "adjustment_type": adjustment_type,
            "description": description, "total_debit": total_debit, "currency": currency,
            "reverse_fiscal_year": reverse_fiscal_year, "reverse_fiscal_period": reverse_fiscal_period}


def _sb_her(name, modified, consolidation_group="G1", data_area_id="DE02",
            main_account="4000", rate_date="2026-09-30", historical_rate=1.2):
    return {"name": name, "modified": modified, "consolidation_group": consolidation_group,
            "data_area_id": data_area_id, "main_account": main_account, "rate_date": rate_date,
            "historical_rate": historical_rate}


def test_sent_back_items_ger_rejected_after_modified_carries_rejection():
    docs = {"Group Exchange Rate": [_sb_ger("GER-20", datetime.datetime(2026, 10, 2, 8, 0, 0))]}
    rejections = {("Group Exchange Rate", "GER-20"): {
        "reason": "wrong rate", "actor": "lead", "at": datetime.datetime(2026, 10, 2, 9, 0, 0)}}
    items = M.sent_back_items(docs, rejections)
    assert len(items) == 1
    item = items[0]
    assert item["doctype"] == "Group Exchange Rate"
    assert item["name"] == "GER-20"
    assert item["rejection"] == rejections[("Group Exchange Rate", "GER-20")]
    assert item["fiscal_year"] == 2026
    assert item["fiscal_period"] == 7
    assert item["kind_label"] == "Group rate · USD→EUR Closing"


def test_sent_back_items_modified_after_rejection_gives_no_item():
    """Failure path."""
    docs = {"Group Exchange Rate": [_sb_ger("GER-21", datetime.datetime(2026, 10, 2, 10, 0, 0))]}
    rejections = {("Group Exchange Rate", "GER-21"): {
        "reason": "wrong rate", "actor": "lead", "at": datetime.datetime(2026, 10, 2, 9, 0, 0)}}
    assert M.sent_back_items(docs, rejections) == []


def test_sent_back_items_no_rejection_gives_no_item():
    """Failure path."""
    docs = {"Group Exchange Rate": [_sb_ger("GER-22", datetime.datetime(2026, 10, 2, 10, 0, 0))]}
    assert M.sent_back_items(docs, {}) == []


def test_sent_back_items_journal_carries_period_her_has_none():
    docs = {
        "Consolidation Journal": [_sb_journal("CJ-20", datetime.datetime(2026, 10, 1, 9, 0, 0))],
        "Historical Equity Rate": [_sb_her("HER-20", datetime.datetime(2026, 10, 1, 7, 0, 0))],
    }
    rejections = {
        ("Consolidation Journal", "CJ-20"): {
            "reason": "fix", "actor": "lead", "at": datetime.datetime(2026, 10, 1, 10, 0, 0)},
        ("Historical Equity Rate", "HER-20"): {
            "reason": "fix2", "actor": "lead", "at": datetime.datetime(2026, 10, 1, 8, 0, 0)},
    }
    items = M.sent_back_items(docs, rejections)
    by_name = {item["name"]: item for item in items}
    assert by_name["CJ-20"]["fiscal_year"] == 2026
    assert by_name["CJ-20"]["fiscal_period"] == 7
    assert by_name["CJ-20"]["kind_label"] == "Adjustment · CJ-20"
    assert by_name["HER-20"]["fiscal_year"] is None
    assert by_name["HER-20"]["fiscal_period"] is None
    assert by_name["HER-20"]["kind_label"] == "Historical equity rate"
    assert by_name["HER-20"]["title"] == "DE02 · 4000 · 2026-09-30"


def test_sent_back_items_missing_modified_raises_value_error_naming_document():
    """Failure path: never guessed."""
    docs = {"Group Exchange Rate": [_sb_ger("GER-23", None)]}
    try:
        M.sent_back_items(docs, {})
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "GER-23" in str(exc)


def test_sent_back_items_unknown_doctype_raises_value_error_naming_it():
    """Failure path: never shown as another kind, the same contract as
    queue_items."""
    docs = {"Budget Cycle": [{"name": "BUD-1", "modified": None}]}
    try:
        M.sent_back_items(docs, {})
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Budget Cycle" in str(exc)


def test_sent_back_items_oldest_rejection_first():
    docs = {"Group Exchange Rate": [
        _sb_ger("GER-30", datetime.datetime(2026, 10, 1, 5, 0, 0), to_currency="EUR"),
        _sb_ger("GER-31", datetime.datetime(2026, 10, 1, 5, 0, 0), to_currency="GBP"),
    ]}
    rejections = {
        ("Group Exchange Rate", "GER-30"): {
            "reason": "a", "actor": "lead", "at": datetime.datetime(2026, 10, 1, 9, 0, 0)},
        ("Group Exchange Rate", "GER-31"): {
            "reason": "b", "actor": "lead", "at": datetime.datetime(2026, 10, 1, 6, 0, 0)},
    }
    items = M.sent_back_items(docs, rejections)
    assert [item["name"] for item in items] == ["GER-31", "GER-30"]


def test_module_imports_no_frappe():
    """Same contract as rates_model.py (test_close_rates_model.py), extended
    to refuse a ``konsol`` import too: this module is loaded by path, like
    the two siblings it loads itself."""
    with open(M.__file__) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("frappe") or a.name.startswith("konsol")
                            for a in node.names)
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("frappe")
            assert not module.startswith("konsol")
