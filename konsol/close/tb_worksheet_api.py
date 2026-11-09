"""Consolidation worksheet endpoint (konsol#494 row 6, no dimension).

``get_worksheet(fiscal_year, fiscal_period, consolidation_group=None,
entities=None)`` (GET) returns one root consolidation group's trial balance
as a worksheet: one line per account, one column per shown entity (entity
layer plus its CTA), Other entities, Outside your scope, Intercompany
eliminations, one column per adjustment group, Rounding, Consolidated
(``tb_worksheet_model.worksheet``). Amounts are net debit in the group's
reporting currency; Balance Sheet cumulative, Profit and Loss year to date.

States, never a silent empty worksheet:

- ``choose_group``: several root groups and none named, or none declared
  (``statement_api.choose_group``); no ClickHouse read;
- ``no_chart``: nothing Published in Main Account; no ClickHouse read;
- ``setup_gap``: the group's reporting currency has no ISO Currency minor
  unit (konsol#354; never a guessed 2); no ClickHouse read;
- ``not_built`` / ``error``: the warehouse read failed, a NULL amount, or a
  row whose non-empty ``reporting_currency`` is not the group's (refused by
  account, layer and currency; the SQL never filters on it because
  eliminations, top-side and deal layers carry ``''``);
- ``ok``: ``worksheet`` is the model's output with its tuple keys turned
  into string ids (``"entity:ZZA"``, ``"layer:topside"``,
  ``"bucket:other_entities"``, ``"rounding"``, ``"consolidated"``; line keys
  ``"account:1110"``, ``"not_in_chart:9999"``, ``"cta_undeclared"``), so the
  payload is JSON-safe.

``entities`` (a list, or its JSON text) narrows the entity columns; an
entity the caller may not see is refused before any read. ClickHouse is
read exactly once, and only on the way to ``ok``.
"""
import json

import frappe

from konsol import fiscal_calendar
from konsol.close import ch_read, signoff_gate, statement_model, tb_worksheet_model
from konsol.close.statement_api import (
    _chart, _error_message, _find_period, _minor_unit, _period_key, choose_group, root_groups)
from konsol.entity_permissions import allowed_entity_codes

#: The one warehouse read. ``reporting_currency`` is read and checked in
#: Python, never filtered on. The sum is aliased ``amt`` (N53: ``AS
#: amount`` beside ``countIf(amount IS NULL)`` raises ILLEGAL_AGGREGATION).
_WORKSHEET_SQL = (
    "SELECT fiscal_year, fiscal_period, data_area_id, main_account, adjustment_type, "
    "reporting_currency, sum(amount) AS amt, countIf(amount IS NULL) AS null_rows "
    "FROM epm_gold.gold_fully_consolidated_tb "
    "WHERE consolidation_group = {group:String} AND fiscal_year <= {fy:UInt32} "
    "GROUP BY fiscal_year, fiscal_period, data_area_id, main_account, adjustment_type, "
    "reporting_currency"
)

_NO_CHART_MESSAGE = "Publish the group chart (Main Account) first."


@frappe.whitelist(methods=["GET"])
def get_worksheet(fiscal_year, fiscal_period, consolidation_group=None, entities=None):
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key = _period_key(fiscal_year, fiscal_period)
    period_rows = fiscal_calendar.fiscal_period_rows()
    period_row = _find_period(key, period_rows)
    shown = _entities(entities)
    allowed = allowed_entity_codes()
    if shown is not None and allowed is not None:
        hidden = [code for code in shown if code not in allowed]
        if hidden:
            frappe.throw("You may not see entity %s." % ", ".join(hidden),
                         frappe.PermissionError)

    groups = root_groups()
    chosen, group_note, state, message = choose_group(groups, consolidation_group)
    currency = ({g["consolidation_group"]: g for g in groups}[chosen].get("reporting_currency")
                if chosen else None)
    result = {
        "period": {"fiscal_year": key[0], "fiscal_period": key[1],
                   "code": period_row.get("period_code"), "status": period_row.get("status"),
                   "period_type": period_row.get("period_type")},
        "groups": groups,
        "consolidation_group": chosen,
        "group_note": group_note,
        "reporting_currency": currency,
        "minor_unit": None,
        "entities": sorted(shown) if shown is not None else None,
        "state": state,
        "message": message,
        "gap": None,
        "worksheet": None,
    }
    if state != "ok":
        return result

    accounts = _chart()
    if not accounts:
        result.update(state="no_chart", message=_NO_CHART_MESSAGE)
        return result
    minor_unit = _minor_unit(currency)
    if minor_unit is None:
        result.update(state="setup_gap", message=(
            f"{statement_model.STATEMENT_CURRENCY_PRECISION_UNDECLARED}: the reporting currency "
            f"{currency or '(none)'} of {chosen} has no ISO Currency, so its minor unit is "
            "undeclared. Add it to ISO Currency, or set the group's reporting currency."))
        return result
    result["minor_unit"] = minor_unit
    declared = signoff_gate.statement_accounts()
    result["gap"] = declared.get("gap")

    try:
        rows = [_row(r) for r in ch_read.rows(_WORKSHEET_SQL, {"group": chosen, "fy": key[0]})]
    except Exception as e:  # noqa: BLE001 — any failure means "can't say", never 0 rows
        result.update(state="not_built" if ch_read.not_built(e) else "error",
                      message=_error_message(e))
        return result

    wrong = _wrong_currency(rows, currency)
    if wrong:
        result.update(state="error", message=wrong)
        return result

    try:
        ws = tb_worksheet_model.worksheet(rows, accounts, period_rows, key, declared,
                                          minor_unit, shown, allowed)
    except ValueError as e:
        message = str(e)
        gap = message.startswith(statement_model.STATEMENT_CURRENCY_PRECISION_UNDECLARED)
        result.update(state="setup_gap" if gap else "error", message=message)
        return result

    result["worksheet"] = tb_worksheet_model.to_ids(ws)
    return result


def _entities(value):
    """The requested entity codes as a set, or None for every entity.
    Accepts a list or its JSON text; anything else is refused."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, (list, tuple)) or not all(isinstance(c, str) for c in value):
        frappe.throw("Entities must be a list of entity codes.")
    codes = {c for c in value if c}
    return codes or None


def _row(row):
    """The read's row with ``amt`` renamed to the model's ``amount`` and
    numerics typed (ClickHouse JSON may send them as strings, E5-P15)."""
    out = dict(row)
    amount = out.pop("amt", None)
    out["amount"] = None if amount is None else float(amount)
    out["fiscal_year"] = int(out["fiscal_year"])
    out["fiscal_period"] = int(out["fiscal_period"])
    out["null_rows"] = int(out.get("null_rows") or 0)
    out["reporting_currency"] = out.get("reporting_currency") or ""
    return out


def _wrong_currency(rows, currency):
    """The refusal sentence for rows whose non-empty currency is not the
    group's, or None. Names account, layer and currency; never the entity
    (it may be outside the caller's scope)."""
    bad = sorted({(r["main_account"], r.get("adjustment_type") or "", r["reporting_currency"])
                  for r in rows if r["reporting_currency"] and r["reporting_currency"] != currency})
    if not bad:
        return None
    listed = "; ".join(f"{acc} ({layer}) in {cur}" for acc, layer, cur in bad)
    return (f"The warehouse holds rows not in the group's reporting currency {currency}: "
            f"{listed}. Rebuild the consolidation, or check the group's reporting currency.")

