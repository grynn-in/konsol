"""Consolidation Worksheet Desk layout, pure: the worksheet as report
columns, rows and message (konsol#494 row 5).

Maps ``tb_worksheet_model.to_ids(worksheet(...))`` (string ids, the API's
payload form) to the Desk Script Report
"Consolidation Worksheet". Nothing here recomputes, re-signs or rounds a
number: every cell is the worksheet's own displayed value.

- ``fieldnames(ws)`` gives every column key one unique, Frappe-safe
  fieldname. Entity: ``e_`` + the code lower-cased with every character
  outside ``a-z0-9`` turned into ``_`` (so ``A-1`` and ``A_1`` both want
  ``e_a_1``). Layer ``l_<key>``, bucket ``b_<key>``; Rounding is
  ``rounding`` and Consolidated ``consolidated``. A wanted name already
  taken gets ``_2``, ``_3`` ... in column order, so names are unique and
  stable for a given worksheet. ``account``, ``name``, ``dimension`` and
  ``currency`` are reserved for the fixed columns.
- ``columns(ws, currency, minor_unit, dimension)``: Account, Account name,
  the dimension (only when given), one Currency column per worksheet
  column (options ``currency``, precision the currency's minor unit, none
  when ``minor_unit`` is None), then a Currency link. Amount labels carry
  the currency: ``E-ALPHA (USD)``.
- ``rows(ws, currency, dimension)``: one row per line, then a bold Total.
  A line's dimension value is its ``dimension_value`` key (row 4).
- ``message(ws, payload)``: period, group, currency, sign, window, any
  column whose exact total is non-zero at the minor unit (Rounding never), and ``N entities outside your scope`` as a count.
  It is built from the worksheet's column labels only, so an out-of-scope
  entity is never named.

Pure: no frappe, no konsol imports; ``period_name`` is a sibling loaded by path.
"""
import html as _html
import importlib.util
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name):
    spec = importlib.util.spec_from_file_location(
        "konsol_close_tb_worksheet_report_model_" + name, os.path.join(_HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


period_name = _load_sibling("period_name").period_name

TOTAL_LABEL = "Total"
_RESERVED = ("account", "name", "dimension", "currency")
_PREFIX = {"entity": "e_", "layer": "l_", "bucket": "b_"}
_FIXED = ("rounding", "consolidated")


def _slug(text):
    return re.sub(r"[^a-z0-9]", "_", str(text).lower()) or "x"


def fieldnames(ws):
    """``{column key: fieldname}``: unique and Frappe-safe (module docstring)."""
    used = set(_RESERVED)
    out = {}
    for column in ws["columns"]:
        key = column["key"]
        if key in _FIXED:
            wanted = key
        else:
            tag, _, rest = key.partition(":")  # split at the first ":" only
            wanted = _PREFIX[tag] + _slug(rest)
        name, n = wanted, 1
        while name in used:
            n += 1
            name = "%s_%d" % (wanted, n)
        used.add(name)
        out[key] = name
    return out


def _amount_label(label, currency):
    return "%s (%s)" % (label, currency) if currency else label


def columns(ws, currency, minor_unit, dimension=None):
    """The report columns (module docstring)."""
    names = fieldnames(ws)
    out = [{"fieldname": "account", "label": "Account", "fieldtype": "Data", "width": 100},
           {"fieldname": "name", "label": "Account name", "fieldtype": "Data", "width": 240}]
    if dimension:
        out.append({"fieldname": "dimension", "label": dimension, "fieldtype": "Data", "width": 140})
    for column in ws["columns"]:
        entry = {"fieldname": names[column["key"]],
                 "label": _amount_label(column["label"], currency),
                 "fieldtype": "Currency", "options": "currency", "width": 150}
        if minor_unit is not None:
            entry["precision"] = minor_unit
        out.append(entry)
    out.append({"fieldname": "currency", "label": "Currency", "fieldtype": "Link",
                "options": "Currency", "width": 80})
    return out


def rows(ws, currency, dimension=None):
    """One row per line, then the bold Total."""
    names = fieldnames(ws)
    keys = [c["key"] for c in ws["columns"] if c["key"] != "consolidated"]

    def build(head, cells, consolidated, **flags):
        row = dict(head)
        for key in keys:
            row[names[key]] = cells.get(key, 0.0)
        row["consolidated"] = consolidated
        row["currency"] = currency
        row.update(flags)
        return row

    out = []
    for line in ws["lines"]:
        head = {"account": line["account"], "name": line["name"]}
        if dimension:
            head["dimension"] = line.get("dimension_value")
        out.append(build(head, line["cells"], line["consolidated"]))
    head = {"account": TOTAL_LABEL, "name": None}
    if dimension:
        head["dimension"] = None
    out.append(build(head, ws["total"]["cells"], ws["total"]["consolidated"], bold=1))
    return out


def _outside_text(count):
    if not count:
        return None
    return "%d %s outside your scope: shown in one column, not named" % (
        count, "entity" if count == 1 else "entities")


def _nonzero_totals_text(ws):
    minor_unit = ws.get("minor_unit")
    places = minor_unit if isinstance(minor_unit, int) else 2
    labels = {c["key"]: c["label"] for c in ws["columns"]}
    found = []
    for key, label in labels.items():
        if key == "rounding":
            continue  # the plug is never an unbalanced column
        value = ws["total_exact"][key]
        if round(value, places):
            found.append("%s %.*f" % (label, places, value))
    if not found:
        return None
    return "Column totals that are not zero (a trial balance should sum to zero): " + "; ".join(found)


def message(ws, payload):
    """The report message: period, group, currency, sign, window, non-zero
    totals and the outside-scope count, each escaped, joined by ``<br>``
    (Desk renders the message as HTML)."""
    period = payload["period"]
    header = " · ".join(
        ["Consolidation worksheet", period_name(period["fiscal_year"], period["fiscal_period"])]
        + [p for p in (payload.get("consolidation_group"), payload.get("reporting_currency"))
           if p not in (None, "")])
    lines = [
        header,
        "Amounts are net debit: debit positive, credit negative.",
        "Balance Sheet accounts are cumulative to the period end; "
        "Profit and Loss accounts are year to date.",
        _nonzero_totals_text(ws),
        _outside_text(ws.get("outside_count")),
    ]
    return "<br>".join(_html.escape(line) for line in lines if line)
