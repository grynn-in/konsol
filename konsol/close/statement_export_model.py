"""Statement export, pure: konsol/close/statement_export_model.py (konsol#305
story 8.5; decision #305-W5-3, Deepak Pai, 6 Oct 2026: a downloaded .xlsx
carrying the legend, the Provisional/Signed label, the commentary and the
drill rows; rejected: an Excel add-in function).

``workbook(payload, drills)`` turns ``statement_api.get_statement``'s ``ok``
payload and one ``drill_model.drill`` result per statement heading into the
bytes of an .xlsx with three sheets (a fourth, "Budget drill", when a budget
drill is given):

- **Statement**: the header, the sign-off label, the legend and the notes
  (declared-accounts gap, comparison note, entities not included), then
  each section with the Numbers screen's own columns (``COLUMNS``),
  heading/"of which"/net result/not-in-chart/residual rows, and each
  heading's commentary text and byline.
- **Drill**: every heading's drill rows in statement order, one line per
  account (a row with no accounts — CTA, outside scope, current-year
  result, rounding — is one line; a row whose rounded account amounts do
  not sum to it gets an ``ACCOUNT_ROUNDING`` line), then the heading's
  total. The column foots to each heading's total.
- **Journals**: the top-side journals behind each heading's "Top-side
  journals" drill row, with their basis (posted in this period).

**Budget (konsol#348 S5 K11).** ``workbook(payload, drills, budget=None,
budget_drills=None)``. With a budget payload, the three ``BUDGET_COLUMNS``
("Budget (before eliminations)", "Variance to budget", "Budget year to date")
follow the actuals columns in each section's header. S5-Q6 a: they are a
SEPARATE constant, so ``COLUMNS`` and its numbers.js pin do not change. Lines
are matched to the budget exactly as ``statement_report_model`` matches them
(a heading by its code, an include by its heading and kind, any other line by
kind). Budget is the budget line's ``current``; Variance to budget is this
period's ``current`` minus it on a Profit and Loss line only (S5 F1: on the
Balance Sheet a budgeted movement set against an actual balance is not a
variance, so the cell is blank); Budget year to date is the budget's ``ytd``
(Profit and Loss only). A cell is blank, never 0, where the budget is
absent, not ok, or lacks the line; the residual takes no variance. The
budget's own "Unbalanced budget (no balancing entry)" line is written before
the residual, its actuals blank, when the actuals carry no such line. One
note line follows the other notes: for an ok budget its own sentence plus the
limits and the Balance Sheet basis; otherwise "Budget columns are blank: ...".
The "Budget drill" sheet is the Drill sheet's layout plus "Declared method
and rate" and "Rate Set" columns, from ``drill_model.with_rates``: the
method is the chart's declared Translation Method as recorded at build time,
not the one the core applied (PR #554 review m6; the core can apply closing
before a historical account's first tranche). With ``budget=None`` nothing
changes: no columns, no note, no sheet.

Amounts are written as numbers with a bracket format, the payload's own
sign — never re-derived. A missing amount raises (never written as 0); a
comparison the statement declared not loaded is the text ``NOT_LOADED``,
as on the screen. Every string cell is written as text, never a formula,
whatever its first character. A non-ok payload raises with its own message: the caller
refuses, it never sends an empty file.

The label texts, the section columns and ``NOT_LOADED`` are shared with
close-ui's ``numbers.js``; ``test_close_statement_export_model.py`` reads
that file and fails when the two drift.

Pure: no frappe and no konsol imports (openpyxl only).
"""
import importlib.util as _importlib_util
import os as _os
import io
from datetime import datetime
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font


def _load_period_name():
    """konsol/close/period_name.py loaded by path (konsol#305 review-w5): the
    one "FY2025 P07" format, reachable even under the host tests' stub
    ``konsol.close`` package."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()


PL = "Profit and Loss"
BS = "Balance Sheet"

#: numbers.js ``COLUMNS``.
COLUMNS = {
    PL: ["This period", "Comparison", "Variance", "Year to date"],
    BS: ["This period", "Comparison", "Variance"],
}
_FIELD = {"This period": "current", "Comparison": "comparison",
          "Variance": "variance", "Year to date": "ytd"}
_COMPARISON_FIELDS = ("comparison", "variance")

#: The budget columns (konsol#348 S5 K11; S5-Q6 a: a separate constant, never
#: part of ``COLUMNS``, which numbers.js pins). Same three on each section;
#: statement_report_model.columns() carries the same labels.
BUDGET_COLUMNS = {
    PL: ["Budget (before eliminations)", "Variance to budget", "Budget year to date"],
    BS: ["Budget (before eliminations)", "Variance to budget", "Budget year to date"],
}
#: The note's texts: the same words as statement_report_model.BUDGET_LIMITS and
#: BUDGET_BS_BASIS (a test pins the equality; this module imports no konsol code).
BUDGET_LIMITS = "before intercompany eliminations and without group-level adjustments"
BUDGET_BS_BASIS = ("the balance sheet budget column shows the budgeted movements year to date, not balances, "
                   "so there is no variance to budget on the balance sheet")
UNBALANCED = "unbalanced"

#: numbers.js ``SIGNOFF_LABEL`` texts.
SIGNOFF_LABEL = {
    "signed": "Signed",
    "provisional": "Provisional",
    "resign_needed": "Re-sign needed — numbers changed after signing",
}

#: numbers.js ``comparisonCell``'s text for a comparison the statement
#: declared not loaded.
NOT_LOADED = "not loaded"

def amount_format(minor_unit):
    """The Excel number format for amounts with ``minor_unit`` decimal
    places, negatives in brackets as numbers.js ``amountText`` shows them:
    ``#,##0.000;(#,##0.000);0.000`` for 3, ``#,##0;(#,##0);0`` for 0
    (PR #386 review: the statement's own minor unit, never cents)."""
    zero = "0" if minor_unit == 0 else "0." + "0" * minor_unit
    return "#,##0%s;(#,##0%s);%s" % (zero[1:], zero[1:], zero)

#: The Drill sheet line that closes a row's account lines to the row amount.
ACCOUNT_ROUNDING = "Rounding (accounts to row)"

_INDENT = "    "
_BOLD = Font(bold=True)
_TITLE = Font(bold=True, size=14)

DRILL_HEADER = ["Section", "Heading", "Heading name", "Layer", "Entity",
                "Account", "Account name", "Amount"]
#: The same label as statement_drill_report_model.RATE_LABEL (review m6).
BUDGET_DRILL_HEADER = DRILL_HEADER + ["Declared method and rate", "Rate Set"]
JOURNAL_HEADER = ["Heading", "Heading name", "Journal", "Description", "Amount",
                  "Posted by", "Approved by", "Basis"]
_BUDGET_DRILL_NOTE = ("Budget " + BUDGET_LIMITS + ". Each heading's amount is its budget column "
                      "figure. Declared method and rate: the chart's Translation Method declared for the "
                      "account when the budget was built (closing where undeclared), and the translation "
                      "rate of the entity's accounts.")
_BASIS_NOTE = ("Each heading's amount is its This period figure: cumulative "
               "for the Balance Sheet, the period alone for Profit and Loss.")


def _amount(value, what):
    if value is None:
        raise ValueError(f"{what}: missing amount, never written as 0")
    value = float(value)
    return 0.0 if value == 0 else value


def _minor_unit(statement):
    """The statement's ``minor_unit`` (statement_model.statement), checked:
    a whole number >= 0. Missing or anything else raises; never read as 2."""
    minor_unit = statement.get("minor_unit")
    if isinstance(minor_unit, bool) or not isinstance(minor_unit, int) or minor_unit < 0:
        raise ValueError(f"The statement carries no minor unit ({minor_unit!r}): the export "
                         "rounds to the reporting currency's ISO minor unit, never to cents.")
    return minor_unit


def _quantize(value, minor_unit):
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-minor_unit))


def _cell(line, field, comparison_note, what):
    value = line.get(field)
    if field in _COMPARISON_FIELDS and comparison_note and value is None:
        return NOT_LOADED
    return _amount(value, f"{what} {field}")


def _header_text(payload):
    """``Numbers · FY2025 P07 · G1 · USD``: the period as its fiscal year and
    period, never the bare ``period_code`` (live it is "P07" alone, measured
    6 Oct; konsol#305 U1 / review-w5 S4), the same as the screen's header."""
    period = payload["period"]
    if payload.get("basis") == "Entity":
        # Entity basis (konsol#495): ``Numbers · FY2025 P07 · Basis: Entity ·
        # ZZA Zz Entity · EUR``; a missing basis is the group statement.
        who = " ".join(p for p in (payload.get("entity"), payload.get("entity_name")) if p)
        parts = [period_name(period["fiscal_year"], period["fiscal_period"]),
                 "Basis: Entity", who, payload.get("reporting_currency")]
    else:
        parts = [period_name(period["fiscal_year"], period["fiscal_period"]),
                 payload.get("consolidation_group"),
                 payload.get("reporting_currency")]
    return " · ".join(["Numbers"] + [p for p in parts if p not in (None, "")])


def _label(signoff):
    state = (signoff or {}).get("state")
    if state not in SIGNOFF_LABEL:
        raise ValueError(f"unknown sign-off state: {state}")
    return SIGNOFF_LABEL[state]


def _not_included_text(not_included):
    """numbers.js ``notIncludedView``'s sentence with every code (a file has
    no "show all" toggle). ``None`` when nothing is missing."""
    if not not_included or not not_included.get("count"):
        return None
    count = not_included["count"]
    text = "%d %s in scope %s not in these numbers" % (
        count, "entity" if count == 1 else "entities", "is" if count == 1 else "are")
    codes = not_included.get("entities") or []
    if codes:
        text += ": " + ", ".join(codes)
    if not_included.get("hidden"):
        text += ", and %d outside your scope" % not_included["hidden"]
    return text


def _byline(entry):
    """``<by> · <YYYY-MM-DD HH:MM ±HH:MM>``: the commentary's author and its
    zoned time (``statement_api._commentary``'s ``at``, already in the site's
    zone), written out in full — a file has no "now" to be relative to."""
    at = datetime.fromisoformat(entry["at"])
    stamp = at.strftime("%Y-%m-%d %H:%M")
    offset = at.strftime("%z")
    if offset:
        stamp += " %s:%s" % (offset[:3], offset[3:])
    return "%s · %s" % (entry.get("by"), stamp)


class _Sheet:
    def __init__(self, ws, minor_unit):
        self.ws = ws
        self.row = 1
        self.amount_format = amount_format(minor_unit)

    def write(self, values, font=None, amount_cols=()):
        for col, value in enumerate(values, start=1):
            if value is None:
                continue
            cell = self.ws.cell(row=self.row, column=col, value=value)
            if isinstance(value, str):
                # openpyxl stores a string starting with "=" as a formula;
                # free text (commentary, descriptions, names) is always
                # text, never a live formula (review-w5 S1).
                cell.data_type = "s"
            if font is not None:
                cell.font = font
            if col in amount_cols and isinstance(value, float):
                cell.number_format = self.amount_format
                cell.alignment = Alignment(horizontal="right")
        self.row += 1

    def blank(self):
        self.row += 1


def _amount_cols(start, count):
    return set(range(start, start + count))


def _line_keys(section, line):
    """``(key, item)`` for a line and each of its includes, as
    statement_report_model._line_keys: a heading by its code, an include by
    its heading and kind, any other line by kind."""
    kind = line.get("kind")
    if kind == "heading":
        yield (section, kind, line.get("heading")), line
        for include in line.get("includes") or []:
            yield (section, "include", line.get("heading"), include.get("kind")), include
    else:
        yield (section, kind), line


def _budget_items(budget):
    """``{key: line or include}`` of an ok budget payload; ``None`` when
    there is no budget or it is not ok (statement_report_model._budget_items)."""
    if not budget or budget.get("state") != "ok" or not budget.get("statement"):
        return None
    items = {}
    for section in budget["statement"]["sections"]:
        for line in section["lines"]:
            for key, item in _line_keys(section["section"], line):
                items[key] = item
    return items


def _budget_cells(section, line, budget_items, minor_unit, include=None, variance=True):
    """``[budget, variance to budget, budget year to date]`` for a line (or
    an include of it), blank (``None``, never 0) where the budget is absent,
    not ok or lacks the line (statement_report_model._with_budget). The
    variance is Profit and Loss only (S5 F1) and rounded once to the minor
    unit; ``variance`` False (the residual) leaves it blank."""
    blank = [None, None, None]
    if budget_items is None:
        return blank
    key, item = ((section, "include", line["heading"], include.get("kind")), include) \
        if include is not None else next(_line_keys(section, line))
    budget_item = budget_items.get(key)
    if budget_item is None or budget_item.get("current") is None:
        return blank
    budget_value = budget_item["current"]
    this = item.get("current")
    cells = [_amount(budget_value, "budget"), None, None]
    if variance and section == PL and this is not None:
        cells[1] = _amount(round(this - budget_value, minor_unit), "variance to budget")
    if budget_item.get("ytd") is not None:
        cells[2] = _amount(budget_item["ytd"], "budget year to date")
    return cells


def _budget_note(budget):
    """The Statement sheet's budget line: for an ok budget its own sentence
    with the columns' limits and the Balance Sheet basis; otherwise why the
    columns are blank; ``None`` when no budget was asked for."""
    if budget is None:
        return None
    if _budget_items(budget) is not None:
        own = (budget.get("message") or "").rstrip(".")
        return "%s. The budget columns are %s; %s." % (own, BUDGET_LIMITS, BUDGET_BS_BASIS)
    reason = budget.get("message") or "the budget is not available (state: %s)" % budget.get("state")
    return "Budget columns are blank: %s" % reason


def _section_rows(section, commentary, comparison_note, budget=None, minor_unit=None):
    """``[(values, font)]`` for one section's table: header row, then each
    line (and a heading's "of which" rows, and a residual's explanation).
    With a ``budget`` payload each row carries the three budget cells after
    the actuals."""
    name = section["section"]
    if name not in COLUMNS:
        raise ValueError(f"unknown statement section: {name}")
    columns = COLUMNS[name]
    with_budget = budget is not None
    budget_items = _budget_items(budget)
    out = [(["Line", "Code"] + columns + (BUDGET_COLUMNS[name] if with_budget else [])
            + ["Commentary", "By"], _BOLD)]

    def budgeted(line, include=None, variance=True):
        if not with_budget:
            return []
        return _budget_cells(name, line, budget_items, minor_unit, include, variance)

    def amounts(line, what, fields=None):
        return [
            (_cell(line, _FIELD[c], comparison_note, what)
             if fields is None or _FIELD[c] in fields else None)
            for c in columns
        ]

    for line in section["lines"]:
        kind = line.get("kind")
        if kind == "heading":
            code = line["heading"]
            entry = commentary.get(code)
            note = [entry["text"], _byline(entry)] if entry and entry.get("text") else [None, None]
            out.append(([line.get("heading_name"), code] + amounts(line, code)
                         + budgeted(line) + note, None))
            for include in line.get("includes") or []:
                out.append(([_INDENT + include["label"], None]
                            + amounts(include, include["label"], ("current", "comparison"))
                            + budgeted(line, include), None))
        elif kind in ("no_heading", "net_result", UNBALANCED):
            font = _BOLD if kind == "net_result" else None
            out.append(([line["label"], None] + amounts(line, line["label"]) + budgeted(line), font))
        elif kind == "not_in_chart":
            label = "%s (%s)" % (line["label"], ", ".join(line.get("codes") or []))
            out.append(([label, None] + amounts(line, line["label"]) + budgeted(line), None))
        elif kind == "residual":
            item = (budget_items or {}).get((name, UNBALANCED))
            if item is not None and not any(ln.get("kind") == UNBALANCED for ln in section["lines"]):
                # The budget's plug, actuals blank (statement_report_model.
                # _budget_only_unbalanced): the budget balance sheet foots.
                out.append(([item["label"], None] + [None] * len(columns)
                            + [_amount(item.get("current"), item["label"]), None, None], None))
            out.append(([line["label"], None] + amounts(line, line["label"], ("current",))
                        + budgeted(line, variance=False), _BOLD))
            if line.get("current"):
                for item in line.get("explained") or []:
                    out.append(([_INDENT + item["label"], None,
                                 _amount(item["amount"], item["label"])], None))
                out.append(([_INDENT + "Unexplained", None,
                             _amount(line.get("unexplained"), "Unexplained")], None))
        else:
            raise ValueError(f"unknown statement line kind: {kind}")
    return out


def _statement_sheet(ws, payload, budget=None):
    statement = payload["statement"]
    sheet = _Sheet(ws, _minor_unit(statement))
    sheet.write([_header_text(payload)], _TITLE)
    sheet.write([_label(payload.get("signoff"))], _BOLD)
    sheet.write([statement["legend"]])
    gap = payload.get("gap")
    comparison_note = statement["periods"].get("comparison_note")
    for note in ((gap or {}).get("message"), comparison_note,
                 _not_included_text(payload.get("not_included")), _budget_note(budget)):
        if note:
            sheet.write([note])
    sheet.blank()

    commentary = payload.get("commentary") or {}
    for section in statement["sections"]:
        sheet.write([section["section"]], _BOLD)
        cols = _amount_cols(3, len(COLUMNS.get(section["section"], ()))
                            + (len(BUDGET_COLUMNS.get(section["section"], ())) if budget is not None else 0))
        for values, font in _section_rows(section, commentary, comparison_note, budget,
                                          _minor_unit(statement)):
            sheet.write(values, font, cols)
        sheet.blank()

    ws.column_dimensions["A"].width = 44
    amount_columns = 4 + (3 if budget is not None else 0)
    for col in range(3, 3 + amount_columns):
        ws.column_dimensions[chr(ord("A") + col - 1)].width = 16
    ws.column_dimensions[chr(ord("A") + 2 + amount_columns)].width = 48
    ws.column_dimensions[chr(ord("A") + 3 + amount_columns)].width = 36


def _headings_in_order(statement):
    return [line["heading"] for section in statement["sections"]
            for line in section["lines"] if line.get("kind") == "heading"]


def _rate_cells(rates):
    """``[Rate, Rate Set]`` text for the Budget drill: "<fx_method> <rate>"
    per distinct rate and the distinct Rate Sets, each joined with "; ";
    blank (``None``) when the row carries no rates."""
    if not rates:
        return [None, None]
    texts, sets = [], []
    for rate in rates:
        text = "%s %s" % (rate.get("fx_method"), rate.get("rate"))
        if text not in texts:
            texts.append(text)
        if rate.get("rate_set") not in (None, "") and rate["rate_set"] not in sets:
            sets.append(rate["rate_set"])
    return ["; ".join(texts), "; ".join(sets) or None]


def _drill_sheets(drill_ws, journal_ws, statement, drills):
    minor_unit = _minor_unit(statement)
    drill_sheet, journal_sheet = _Sheet(drill_ws, minor_unit), _Sheet(journal_ws, minor_unit)
    notes = {d.get("dimensions_note") for d in drills.values() if d.get("dimensions_note")}
    drill_sheet.write([" ".join(sorted(notes) + [_BASIS_NOTE])])
    drill_sheet.write(DRILL_HEADER, _BOLD)
    journal_sheet.write(["Top-side journals posted in this period, by heading."])
    journal_sheet.write(JOURNAL_HEADER, _BOLD)
    _write_drills(drill_sheet, journal_sheet, _headings_in_order(statement), drills, minor_unit)
    for ws, widths in ((drill_ws, (16, 10, 28, 30, 12, 12, 30, 16)),
                       (journal_ws, (10, 28, 16, 40, 16, 28, 28, 40))):
        for i, width in enumerate(widths):
            ws.column_dimensions[chr(ord("A") + i)].width = width


def _budget_drill_sheet(ws, budget, budget_drills):
    """The "Budget drill" sheet: the Drill sheet's layout plus Rate and Rate
    Set (``drill_model.with_rates``). Headings in the budget statement's
    order, then any other drill (the UNBALANCED line) by name."""
    minor_unit = _minor_unit(budget["statement"])
    sheet = _Sheet(ws, minor_unit)
    notes = {d.get("dimensions_note") for d in budget_drills.values() if d.get("dimensions_note")}
    sheet.write([" ".join(sorted(notes) + [_BUDGET_DRILL_NOTE])])
    sheet.write(BUDGET_DRILL_HEADER, _BOLD)
    ordered = [h for h in _headings_in_order(budget["statement"]) if h in budget_drills]
    ordered += sorted(h for h in budget_drills if h not in ordered)
    _write_drills(sheet, None, ordered, budget_drills, minor_unit, rates=True)
    for i, width in enumerate((16, 10, 28, 30, 12, 12, 30, 16, 28, 20)):
        ws.column_dimensions[chr(ord("A") + i)].width = width


def _write_drills(drill_sheet, journal_sheet, headings, drills, minor_unit, rates=False):
    """The drill rows of each heading (and its journals, when a journal sheet
    is given); ``rates`` adds the Rate and Rate Set cells."""
    amount_col = {DRILL_HEADER.index("Amount") + 1}
    journal_amount_col = {JOURNAL_HEADER.index("Amount") + 1}

    def tail(cells=None):
        return (cells or [None, None]) if rates else []

    for code in headings:
        drill = drills.get(code)
        if drill is None:
            raise ValueError(f"{code} has no drill")
        name, section = drill.get("heading_name"), drill.get("section")
        for row in drill["rows"]:
            layer = row.get("label") or "Entity"
            entity = row.get("entity")
            what = f"{code} {layer}"
            if row.get("accounts"):
                for account in row["accounts"]:
                    drill_sheet.write(
                        [section, code, name, layer, entity, account["main_account"],
                         account.get("account_name"), _amount(account["amount"], what)]
                        + tail(_rate_cells(account.get("rates"))),
                        amount_cols=amount_col)
                # Each account is rounded to the minor unit on its own, so
                # the lines can drift from their row (measured live 6 Oct:
                # up to 0.14). Write the difference, in the statement's
                # minor unit (PR #386 review), so the column foots.
                drift = _quantize(_amount(row["amount"], what), minor_unit) - sum(
                    (_quantize(a["amount"], minor_unit) for a in row["accounts"]), Decimal("0"))
                if drift:
                    drill_sheet.write(
                        [section, code, name, layer, entity, None, ACCOUNT_ROUNDING,
                         float(drift)] + tail(), amount_cols=amount_col)
            else:
                drill_sheet.write(
                    [section, code, name, layer, entity, None, None,
                     _amount(row["amount"], what)] + tail(_rate_cells(row.get("rates"))),
                    amount_cols=amount_col)
            for journal in (row.get("journals") or []) if journal_sheet is not None else []:
                journal_sheet.write(
                    [code, name, journal["journal_id"], journal.get("description"),
                     _amount(journal["amount"], f"{code} {journal['journal_id']}"),
                     journal.get("posted_by"), journal.get("approved_by"),
                     row.get("journals_basis")], amount_cols=journal_amount_col)
        drill_sheet.write([section, code, name, "Total", None, None, None,
                           _amount(drill["total"], f"{code} total")] + tail(),
                          _BOLD, amount_col)


def workbook(payload, drills, budget=None, budget_drills=None):
    """The .xlsx bytes for an ``ok`` statement payload and its drills
    (``{heading: drill_model.drill(...)}``, one per statement heading).
    ``budget`` (the budget payload: ``state``, ``message``, ``statement``,
    ``meta``) adds the budget columns and note; ``budget_drills`` (headings
    and "UNBALANCED", through ``drill_model.with_rates``) adds the "Budget
    drill" sheet. Raises ``ValueError`` on a non-ok payload (its own message), a missing
    drill, a missing amount, or a kind/state this module does not know."""
    if payload.get("state") != "ok" or not payload.get("statement"):
        raise ValueError(payload.get("message") or "The statement is not ready to export.")
    _minor_unit(payload["statement"])
    book = Workbook()
    statement_ws = book.active
    statement_ws.title = "Statement"
    _statement_sheet(statement_ws, payload, budget)
    _drill_sheets(book.create_sheet("Drill"), book.create_sheet("Journals"),
                  payload["statement"], drills)
    if budget_drills is not None:
        if _budget_items(budget) is None:
            raise ValueError("A budget drill needs an ok budget payload.")
        _budget_drill_sheet(book.create_sheet("Budget drill"), budget, budget_drills)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
