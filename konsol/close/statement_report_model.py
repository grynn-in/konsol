"""Consolidated Statement Desk report, pure: the Numbers statement as report rows.

``rows(payload)`` maps ``statement_api.get_statement``'s payload to the rows
of the Desk Script Report "Consolidated Statement"
(konsol/consolidation/report/consolidated_statement). Every amount is the
payload's own, already display-signed by ``statement_model.statement``:
nothing here recomputes, re-signs or rounds a number, so the report shows
exactly what the Close app's Numbers screen shows.

- One section header row per statement section, in the statement's order.
- One row per line: a heading (its name and code), "No heading", "Net
  result", "Not in the group chart" (with its codes), "Unmatched residual".
- A heading's ``includes`` (the CTA and the current-year result on the
  equity line) follow it at ``indent`` 1. A non-zero residual's
  explanation and its "Unexplained" remainder follow it at ``indent`` 1.
- ``ytd`` is set on Profit and Loss lines only; the Balance Sheet has none.
- Every row carries the reporting currency (the Currency columns' options).

A non-ok payload raises ``ValueError`` with the payload's own message: the
report refuses, it never shows an empty statement. An unknown line kind
raises, naming it.

Pure: no frappe and no konsol imports. Loaded by path in
konsol/tests/test_statement_report_model.py.
"""
import html as _html
import importlib.util as _importlib_util
import os as _os


def _load_period_name():
    """konsol/close/period_name.py loaded by path (statement_model's
    precedent): the one "FY2025 P07" format."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()

_AMOUNT_FIELDS = ("current", "comparison", "variance", "ytd")
_UNEXPLAINED_LABEL = "Unexplained"


def columns():
    """The report columns: line, code, the Numbers screen's four amount
    columns (``statement_export_model.COLUMNS``), and the currency."""
    def amount(fieldname, label):
        return {"fieldname": fieldname, "label": label, "fieldtype": "Currency",
                "options": "currency", "width": 170}

    return [
        {"fieldname": "label", "label": "Line", "fieldtype": "Data", "width": 320},
        {"fieldname": "code", "label": "Code", "fieldtype": "Data", "width": 80},
        amount("current", "This period"),
        amount("comparison", "Comparison"),
        amount("variance", "Variance"),
        amount("ytd", "Year to date"),
        {"fieldname": "currency", "label": "Currency", "fieldtype": "Link",
         "options": "Currency", "width": 80},
    ]


def _refuse_unless_ok(payload):
    state = payload.get("state")
    if state != "ok":
        raise ValueError(payload.get("message") or f"The statement is not available (state: {state}).")
    if not payload.get("statement"):
        raise ValueError("The statement is not available (state: ok, but no statement).")


def _row(label, currency, line=None, fields=_AMOUNT_FIELDS, code=None, indent=0, **flags):
    row = {"label": label, "code": code, "indent": indent, "currency": currency}
    for field in _AMOUNT_FIELDS:
        value = line.get(field) if (line is not None and field in fields) else None
        # -0.0 (a rounded tiny negative, e.g. the residual) shows as "-0.00";
        # it is zero (statement_export_model._amount's precedent).
        row[field] = 0.0 if value == 0 and value is not None else value
    row.update(flags)
    return row


def rows(payload):
    """The report rows for ``payload`` (see the module docstring)."""
    _refuse_unless_ok(payload)
    currency = payload.get("reporting_currency")
    out = []
    for section in payload["statement"]["sections"]:
        out.append(_row(section["section"], currency, is_section=1, bold=1))
        for line in section["lines"]:
            kind = line.get("kind")
            if kind == "heading":
                out.append(_row(line.get("heading_name") or line["heading"], currency, line,
                                code=line["heading"]))
                for include in line.get("includes") or []:
                    out.append(_row(include["label"], currency, include,
                                    fields=("current", "comparison"), indent=1))
            elif kind in ("no_heading", "net_result"):
                out.append(_row(line["label"], currency, line, bold=int(kind == "net_result")))
            elif kind == "not_in_chart":
                codes = ", ".join(line.get("codes") or [])
                label = f"{line['label']} ({codes})" if codes else line["label"]
                out.append(_row(label, currency, line))
            elif kind == "residual":
                out.append(_row(line["label"], currency, line,
                                fields=("current", "comparison"), bold=1))
                if line.get("current"):
                    for item in line.get("explained") or []:
                        out.append(_row(item["label"], currency, {"current": item["amount"]},
                                        indent=1))
                    out.append(_row(_UNEXPLAINED_LABEL, currency,
                                    {"current": line.get("unexplained")}, indent=1))
            else:
                raise ValueError(f"unknown statement line kind: {kind}")
    return out


#: The report message names this many missing entities, then counts the rest
#: (the count itself is always the full one).
NOT_INCLUDED_SHOWN = 10


def _not_included_text(not_included):
    """``statement_export_model._not_included_text``'s sentence, naming the
    first ``NOT_INCLUDED_SHOWN`` codes and counting the rest."""
    if not not_included or not not_included.get("count"):
        return None
    count = not_included["count"]
    text = "%d %s in scope %s not in these numbers" % (
        count, "entity" if count == 1 else "entities", "is" if count == 1 else "are")
    codes = not_included.get("entities") or []
    if codes:
        text += ": " + ", ".join(codes[:NOT_INCLUDED_SHOWN])
        if len(codes) > NOT_INCLUDED_SHOWN:
            text += " and %d more" % (len(codes) - NOT_INCLUDED_SHOWN)
    if not_included.get("hidden"):
        text += ", and %d outside your scope" % not_included["hidden"]
    return text


def message(payload):
    """The report message: ``Numbers · FY2025 P07 · <group> · <currency>``,
    the legend, and every note the Numbers screen shows (comparison note,
    declared-accounts gap, entities not included), each escaped, joined by ``<br>``: Desk renders
    the report message as HTML."""
    _refuse_unless_ok(payload)
    period = payload["period"]
    stmt = payload["statement"]
    header = " · ".join(
        ["Numbers", period_name(period["fiscal_year"], period["fiscal_period"])]
        + [p for p in (payload.get("consolidation_group"), payload.get("reporting_currency"))
           if p not in (None, "")]
    )
    lines = [header, stmt.get("legend")]
    comparison_note = (stmt.get("periods") or {}).get("comparison_note")
    if comparison_note:
        lines.append("Comparison: " + comparison_note)
    # numbers.js ``gapText``: the declared-accounts gap's own message.
    gap = payload.get("gap") or stmt.get("gap")
    if gap:
        lines.append(gap["message"])
    not_included = _not_included_text(payload.get("not_included"))
    if not_included:
        lines.append(not_included)
    return "<br>".join(_html.escape(line) for line in lines if line)
