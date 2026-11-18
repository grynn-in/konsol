"""Forecast Accuracy: every approved forecast round against the actuals of the
closed periods after its as-of date (konsol#348 S6 KC3; FC-4-2, 13 Nov 2026).

The rule is the warehouse's: konsolidat's ``gold_forecast_accuracy`` (S6 WC2)
holds one row per approved round x closed Regular period after its as-of
period within its horizon ("closed" is the declared period status, S6-Q8 a)
x entity x account x dimension, in entity currency, the round's layers summed,
against the current (restated) actuals. This report reads it once, sums the
dimensions away and lays out one row per round, period, entity and account:

- ``forecast_error`` is forecast - actual (signed, debit positive);
- ``forecast_abs_error`` is the sum of the model's absolute errors;
- the error % is forecast_error / |actual| x 100 of the summed row, the
  convention of ``variance_pct``; "n/a" where the actual is 0 (nothing to
  divide by), never 0.

Every round is shown side by side: the Excel ``forecast_accuracy`` read takes
the declared current round only.

Scope: an Entity Accountant (``allowed_entity_codes``) reads their entities
only. The read is bound to them, a filter naming another entity is refused
without naming it, so no entity outside the scope appears anywhere.

Read-only (GET): one ClickHouse read through ``ch_read``, nothing written.
"""
import frappe

from konsol.close import ch_read
from konsol.entity_permissions import allowed_entity_codes

_TABLE = "epm_gold.gold_forecast_accuracy"

#: One row per round, period, entity, account and currency, the dimensions
#: summed away. The aliases differ from the columns they sum: ClickHouse
#: refuses an alias that a sibling aggregate reads (CYCLIC_ALIASES).
_SQL = (
    "SELECT scenario_id, cycle, toString(as_of_date) AS as_of, fiscal_year, fiscal_period, lag, "
    "data_area_id, main_account, currency, "
    "sum(forecast_amount) AS fc, sum(actual_amount) AS act, "
    "sum(forecast_error) AS err, sum(forecast_abs_error) AS abs_err "
    f"FROM {_TABLE} "
    "WHERE 1 = 1{where} "
    "GROUP BY scenario_id, cycle, as_of_date, fiscal_year, fiscal_period, lag, "
    "data_area_id, main_account, currency "
    "ORDER BY scenario_id, as_of_date, cycle, fiscal_year, fiscal_period, data_area_id, main_account"
)


def columns():
    return [
        {"fieldname": "scenario_id", "label": "Scenario", "fieldtype": "Link", "options": "Scenario", "width": 120},
        {"fieldname": "cycle", "label": "Round", "fieldtype": "Link", "options": "Budget Cycle", "width": 160},
        {"fieldname": "as_of_date", "label": "As Of", "fieldtype": "Date", "width": 100},
        {"fieldname": "period", "label": "Period", "fieldtype": "Data", "width": 100},
        {"fieldname": "lag", "label": "Lag", "fieldtype": "Int", "width": 60},
        {"fieldname": "data_area_id", "label": "Entity", "fieldtype": "Link", "options": "Entity", "width": 100},
        {"fieldname": "main_account", "label": "Account", "fieldtype": "Data", "width": 110},
        {"fieldname": "currency", "label": "Currency", "fieldtype": "Data", "width": 80},
        {"fieldname": "forecast_amount", "label": "Forecast", "fieldtype": "Float", "precision": 2, "width": 120},
        {"fieldname": "actual_amount", "label": "Actual", "fieldtype": "Float", "precision": 2, "width": 120},
        {"fieldname": "forecast_error", "label": "Error (Forecast - Actual)", "fieldtype": "Float", "precision": 2,
         "width": 150},
        {"fieldname": "forecast_abs_error", "label": "Absolute Error", "fieldtype": "Float", "precision": 2,
         "width": 120},
        {"fieldname": "forecast_error_pct", "label": "Error %", "fieldtype": "Data", "width": 90},
    ]


def _sql_array(codes):
    """An ``Array(String)`` query parameter as ClickHouse reads it: a literal
    array string (as ``statement_api._sql_array``)."""
    return "[" + ",".join("'%s'" % c.replace("\\", "\\\\").replace("'", "\\'") for c in codes) + "]"


def query(filters, allowed):
    """(sql, params) for ``filters`` within the scope ``allowed`` (None =
    unrestricted). Every value is bound, never interpolated."""
    where, params = [], {}
    if filters.get("scenario_id"):
        where.append("scenario_id = {scenario:String}")
        params["scenario"] = filters.get("scenario_id")
    if filters.get("cycle"):
        where.append("cycle = {cycle:String}")
        params["cycle"] = filters.get("cycle")
    if filters.get("entity"):
        where.append("data_area_id = {entity:String}")
        params["entity"] = filters.get("entity")
    if filters.get("lag") not in (None, ""):
        where.append("lag = {lag:UInt16}")
        params["lag"] = int(filters.get("lag"))
    if allowed is not None:
        where.append("data_area_id IN {entities:Array(String)}")
        params["entities"] = _sql_array(sorted(allowed))
    return _SQL.format(where="".join(" AND " + w for w in where)), params


def error_pct(error, actual):
    """forecast_error / |actual| x 100 as text, or "n/a" when the actual is 0."""
    if not actual:
        return "n/a"
    return "%.2f%%" % (error / abs(actual) * 100)


def rows(raw):
    out = []
    for r in raw:
        fc, act, err, abs_err = (float(r[k] or 0) for k in ("fc", "act", "err", "abs_err"))
        out.append({
            "scenario_id": r["scenario_id"],
            "cycle": r["cycle"],
            "as_of_date": r["as_of"],
            "period": "FY%s P%s" % (r["fiscal_year"], int(r["fiscal_period"])),
            "lag": int(r["lag"]),
            "data_area_id": r["data_area_id"],
            "main_account": r["main_account"],
            "currency": r["currency"],
            "forecast_amount": fc,
            "actual_amount": act,
            "forecast_error": err,
            "forecast_abs_error": abs_err,
            "forecast_error_pct": error_pct(err, act),
        })
    return out


def execute(filters=None):
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "Entity Accountant", "System Manager"))
    filters = frappe._dict(filters or {})
    allowed = allowed_entity_codes()
    if allowed is not None:
        if not allowed:
            return columns(), [], ("No entity is assigned to you, so there is no forecast accuracy "
                                   "to show. Ask an EPM Admin to assign your entities.")
        if filters.get("entity") and filters.get("entity") not in allowed:
            frappe.throw("You may not see the entity in the Entity filter. Clear it, or choose "
                         "one of your entities.", frappe.PermissionError)
    sql, params = query(filters, allowed)
    try:
        raw = ch_read.rows(sql, params)
    except Exception as e:  # noqa: BLE001 — a failure is never "no rows"
        if ch_read.not_built(e):
            return columns(), [], ("The forecast accuracy model has not been built yet: run the "
                                   "warehouse build, then open this report again.")
        frappe.throw("Forecast accuracy could not be read from the warehouse: %s" % e)
    data = rows(raw)
    message = None
    if not data:
        message = ("No forecast accuracy to show for these filters. A row needs an approved forecast "
                   "round and a closed period after its as-of date, within its horizon.")
    return columns(), data, message
