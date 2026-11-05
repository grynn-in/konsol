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
- Commentary (Desk, 6 Nov 2026): a heading row carries the payload's own
  ``commentary`` for its heading (the Commentary column shows the text,
  truncated to ``COMMENTARY_SHOWN``; ``commentary_text`` and
  ``commentary_modified`` are the full text and the stale-edit token the
  Add commentary dialog prefills and sends back) and the payload's own
  ``can_comment`` (1/0): the rule is ``statement_api``'s, never re-decided
  here. Only heading rows take commentary (W4-E15). An ok payload without
  ``commentary`` or ``can_comment`` is refused, naming the key.
- Drill: a heading row carries ``drill_group`` (the Consolidation Group
  document name the Statement Drill filter takes) and
  ``consolidation_group`` (the group code commentary is keyed on).
- ``prior_year``: the prior year's value for the SAME line, from a second
  payload (``get_statement(fiscal_year - 1, fiscal_period, group)``): a
  P&L line takes the prior year's year to date, a Balance Sheet line the
  prior year's balance at the same period (its ``current``). Matched by
  heading code, by include kind, and by kind for the other lines. A line
  absent last year, a non-ok prior payload, a section header and a
  residual explanation row are blank, never 0. ``prior_year_change`` is
  this year's value on the same basis (YTD or ``current``) minus it.

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


def columns(minor_unit=None):
    """The report columns: line, code, the Numbers screen's four amount
    columns (``statement_export_model.COLUMNS``), and the currency.

    ``minor_unit`` is the statement's own (the reporting currency's ISO
    minor unit, konsol#354): the amount columns show that many places.
    ``None`` (no statement) sets no precision at all, never a guessed 2."""
    def amount(fieldname, label):
        column = {"fieldname": fieldname, "label": label, "fieldtype": "Currency",
                  "options": "currency", "width": 170}
        if minor_unit is not None:
            column["precision"] = minor_unit
        return column

    return [
        {"fieldname": "label", "label": "Line", "fieldtype": "Data", "width": 320},
        {"fieldname": "code", "label": "Code", "fieldtype": "Data", "width": 80},
        amount("current", "This period"),
        amount("comparison", "Comparison"),
        amount("variance", "Variance"),
        amount("ytd", "Year to date"),
        amount("prior_year", "Prior year"),
        amount("prior_year_change", "Change vs prior year"),
        {"fieldname": "currency", "label": "Currency", "fieldtype": "Link",
         "options": "Currency", "width": 80},
        {"fieldname": "commentary", "label": "Commentary", "fieldtype": "Data", "width": 280},
    ]


#: The Commentary column shows at most this many characters (the last an
#: ellipsis); the full text is on the row as ``commentary_text``.
COMMENTARY_SHOWN = 80


def _shown(text):
    if not text:
        return None
    if len(text) <= COMMENTARY_SHOWN:
        return text
    return text[:COMMENTARY_SHOWN - 1] + "…"


def _with_commentary(row, payload, heading, drill_group):
    """A heading row with its commentary, the payload's ``can_comment`` and
    the keys that open its drill (module docstring)."""
    entry = payload["commentary"].get(heading)
    row["commentary"] = _shown(entry.get("text")) if entry else None
    row["commentary_text"] = entry.get("text") if entry else None
    row["commentary_modified"] = entry.get("modified") if entry else None
    row["can_comment"] = 1 if payload["can_comment"] is True else 0
    row["drill_group"] = drill_group
    row["consolidation_group"] = payload.get("consolidation_group")
    return row


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
        row[field] = _zero(value)
    row["prior_year"] = row["prior_year_change"] = None
    row["commentary"] = None
    row.update(flags)
    return row


def _zero(value):
    """-0.0 (a rounded tiny negative, e.g. the residual) shows as "-0.00";
    it is zero (statement_export_model._amount's precedent)."""
    return 0.0 if value == 0 and value is not None else value


def _basis(line):
    """The value a line is compared with year on year: the year to date on
    a P&L line (the only lines that carry ``ytd``), else ``current``."""
    return line.get("ytd") if "ytd" in line else line.get("current")


def _line_keys(section, line):
    """``(key, item)`` for a line and each of its includes: a heading by its
    code, an include by its heading and kind, any other line by kind."""
    kind = line.get("kind")
    if kind == "heading":
        yield (section, kind, line.get("heading")), line
        for include in line.get("includes") or []:
            yield (section, "include", line.get("heading"), include.get("kind")), include
    else:
        yield (section, kind), line


def _prior_values(prior):
    """``{key: basis value}`` of an ok prior-year payload; ``None`` when
    there is no prior payload or it is not ok (the message says why)."""
    if not prior or prior.get("state") != "ok" or not prior.get("statement"):
        return None
    values = {}
    for section in prior["statement"]["sections"]:
        for line in section["lines"]:
            for key, item in _line_keys(section["section"], line):
                values[key] = _basis(item)
    return values


def prior_with_rows(prior, tb_rows):
    """``prior`` unchanged, unless it is ok but the warehouse holds no row
    for its year up to its period (``tb_rows``: ``statement_api``'s TB
    read). Such a statement is all zeros; it is refused as ``no_rows`` with
    the reason, so the Prior year column is blank, never a column of 0.00
    (measured live 5 Nov 2026: FY2022 P12 is ok with no rows)."""
    if not prior or prior.get("state") != "ok":
        return prior
    fiscal_year = int(prior["period"]["fiscal_year"])
    fiscal_period = int(prior["period"]["fiscal_period"])
    for row in tb_rows or []:
        if int(row["fiscal_year"]) == fiscal_year and int(row["fiscal_period"]) <= fiscal_period:
            return prior
    return {"state": "no_rows", "statement": None,
            "message": "No rows in the warehouse for FY%d up to P%02d." % (fiscal_year, fiscal_period)}


def _with_prior(row, section, line, prior_values, minor_unit, include=None):
    """``row`` with the prior year's value for the same line, and the
    change on the same basis, rounded to the statement's ``minor_unit``
    (konsol#354). Absent last year: left blank, never 0."""
    if prior_values is not None:
        key, item = ((section, "include", line["heading"], include.get("kind")), include) \
            if include is not None else next(_line_keys(section, line))
        prior = prior_values.get(key)
        if prior is not None:
            this = _basis(item)
            row["prior_year"] = _zero(prior)
            row["prior_year_change"] = _zero(round(this - prior, minor_unit)) if this is not None else None
    return row


def rows(payload, prior=None, drill_group=None):
    """The report rows for ``payload``, with the prior year's payload
    ``prior`` (or ``None``) on the prior-year columns and ``drill_group``
    on the heading rows (see the module docstring)."""
    _refuse_unless_ok(payload)
    for key in ("commentary", "can_comment"):
        if key not in payload:
            raise ValueError(f"The statement payload has no {key}.")
    currency = payload.get("reporting_currency")
    minor_unit = payload["statement"].get("minor_unit")
    if minor_unit is None:
        raise ValueError("The statement payload has no minor_unit.")
    prior_values = _prior_values(prior)
    out = []
    for section in payload["statement"]["sections"]:
        name = section["section"]
        out.append(_row(name, currency, is_section=1, bold=1))
        for line in section["lines"]:
            kind = line.get("kind")
            if kind == "heading":
                out.append(_with_commentary(_with_prior(
                    _row(line.get("heading_name") or line["heading"], currency, line,
                         code=line["heading"]),
                    name, line, prior_values, minor_unit), payload, line["heading"], drill_group))
                for include in line.get("includes") or []:
                    out.append(_with_prior(
                        _row(include["label"], currency, include,
                             fields=("current", "comparison"), indent=1),
                        name, line, prior_values, minor_unit, include=include))
            elif kind in ("no_heading", "net_result"):
                out.append(_with_prior(
                    _row(line["label"], currency, line, bold=int(kind == "net_result")),
                    name, line, prior_values, minor_unit))
            elif kind == "not_in_chart":
                codes = ", ".join(line.get("codes") or [])
                label = f"{line['label']} ({codes})" if codes else line["label"]
                out.append(_with_prior(_row(label, currency, line), name, line, prior_values, minor_unit))
            elif kind == "residual":
                out.append(_with_prior(
                    _row(line["label"], currency, line,
                         fields=("current", "comparison"), bold=1),
                    name, line, prior_values, minor_unit))
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


def _prior_year_text(period, prior):
    """The message's prior-year line: the prior period and its basis when
    the prior payload is ok, else why the column is blank (the prior
    payload's own message, or its state)."""
    if prior is None:
        return None
    name = period_name(int(period["fiscal_year"]) - 1, period["fiscal_period"])
    if _prior_values(prior) is not None:
        return ("Prior year: %s (Profit and Loss year to date; Balance Sheet at the "
                "period end)" % name)
    reason = prior.get("message") or "the statement is not available (state: %s)" % prior.get("state")
    return "Prior year (%s) is blank: %s" % (name, reason)


def message(payload, prior=None):
    """The report message: ``Numbers · FY2025 P07 · <group> · <currency>``,
    the legend, every note the Numbers screen shows (comparison note,
    declared-accounts gap, entities not included), and the prior-year line
    (``_prior_year_text``), each escaped, joined by ``<br>``: Desk renders
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
    lines.append(_prior_year_text(period, prior))
    return "<br>".join(_html.escape(line) for line in lines if line)
