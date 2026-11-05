"""Statement Drill Desk report, pure: one statement heading broken into layers.

``rows(payload, currency, submissions)`` maps ``statement_api.get_drill``'s
payload (``drill_model.drill``) to the rows of the Desk Script Report
"Statement Drill" (konsol/consolidation/report/statement_drill), the Desk
counterpart of the Close app's Numbers drill panel (close-ui numbers.js
``drillView``). Every amount, label and order is the payload's own, already
display-signed by ``drill_model``: nothing here recomputes, re-signs or
rounds a number, so the layer rows sum to the payload's ``total``, which
``drill_model`` has already checked against the statement line.

The rows form a report tree (``indent``):

- indent 0, one row per drill row in the payload's order: each entity (its
  code as the label), "N entities outside your scope", intercompany
  eliminations, CTA, top-side journals, equity method, acquisitions and
  disposals, any unrecognised layer, the current-year result, and the
  rounding row when the payload has one.
- indent 1 under a layer: its accounts (``"<code> <name>"``, lft order as
  the payload gives them); under the top-side row, a "Journals <basis>"
  row, then each journal at indent 2. A journal's amount is what it posted
  in this period, so the journals are listed, not summed into the layer.
- a bold "Total" row (``is_total``): the payload's ``total``.

Sources (the Source column, a Dynamic Link): an entity row whose source is
a TB links its submitted Trial Balance Submission for the source's entity
and period, from ``submissions`` (the caller's read). None found: no link.
More than one: no link, and the message names them; never a guess. A
journal row links its Consolidation Journal (``journal_id`` is the
journal's name, journal_model.py). The CTA row's TB source names no entity,
so it links nothing. An unknown ``source.kind`` raises, naming it.

A non-ok payload raises ``ValueError`` with its own message: the report
refuses, it never shows an empty breakdown.

Pure: no frappe and no konsol imports. Loaded by path in
konsol/tests/test_statement_drill_report_model.py.
"""
import html as _html
import importlib.util as _importlib_util
import os as _os


def _load_period_name():
    """konsol/close/period_name.py loaded by path (statement_report_model's
    precedent): the one "FY2025 P07" format."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name_drill", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()

TB_DOCTYPE = "Trial Balance Submission"
JOURNAL_DOCTYPE = "Consolidation Journal"
_SOURCE_KINDS = ("tb", "journals")


def columns(minor_unit=None):
    """``minor_unit`` is the drill's own (the reporting currency's ISO minor
    unit, konsol#354): the Amount column shows that many places. ``None``
    (no drill) sets no precision, never a guessed 2."""
    amount = {"fieldname": "amount", "label": "Amount", "fieldtype": "Currency",
              "options": "currency", "width": 170}
    if minor_unit is not None:
        amount["precision"] = minor_unit
    return [
        {"fieldname": "label", "label": "Layer / account", "fieldtype": "Data", "width": 380},
        {"fieldname": "entity", "label": "Entity", "fieldtype": "Link", "options": "Entity",
         "width": 110},
        {"fieldname": "main_account", "label": "Account", "fieldtype": "Link",
         "options": "Main Account", "width": 110},
        amount,
        {"fieldname": "source_doctype", "label": "Source type", "fieldtype": "Data",
         "hidden": 1},
        {"fieldname": "source", "label": "Source", "fieldtype": "Dynamic Link",
         "options": "source_doctype", "width": 200},
        {"fieldname": "currency", "label": "Currency", "fieldtype": "Link",
         "options": "Currency", "width": 80},
    ]


def _refuse_unless_ok(payload):
    state = payload.get("state")
    if state != "ok":
        raise ValueError(payload.get("message") or f"The drill is not available (state: {state}).")
    if not payload.get("drill"):
        raise ValueError("The drill is not available (state: ok, but no drill).")


def _submissions_by_key(submissions):
    """``{(entity, fiscal_year, fiscal_period): [names]}``."""
    out = {}
    for row in submissions or ():
        key = (row["data_area_id"], int(row["fiscal_year"]), int(row["fiscal_period"]))
        out.setdefault(key, []).append(row["name"])
    return out


def _tb_key(source):
    return (source.get("entity"), int(source["fiscal_year"]), int(source["fiscal_period"]))


def _source(source, by_key):
    """``(doctype, name)`` for a layer row's source, or ``(None, None)``."""
    if not source:
        return None, None
    kind = source.get("kind")
    if kind not in _SOURCE_KINDS:
        raise ValueError(f"unknown drill source kind: {kind}")
    if kind == "tb" and source.get("entity"):
        names = by_key.get(_tb_key(source)) or []
        if len(names) == 1:
            return TB_DOCTYPE, names[0]
    # "journals": the journals are their own rows below the layer.
    return None, None


def _row(label, currency, amount=None, indent=0, entity=None, main_account=None,
         source=(None, None), **flags):
    row = {"label": label, "entity": entity, "main_account": main_account,
           "amount": amount, "source_doctype": source[0], "source": source[1],
           "currency": currency, "indent": indent}
    row.update(flags)
    return row


def _journal_label(journal):
    label = journal["journal_id"]
    if journal.get("description"):
        label += " · " + journal["description"]
    if journal.get("auto_reversal"):
        # #305 story 6.5: the reversal posting of an earlier period's journal.
        label += " (auto-reversal of an earlier period's journal)"
    return label


def rows(payload, currency, submissions=()):
    """The report rows for ``payload`` (see the module docstring).
    ``submissions`` are the submitted Trial Balance Submissions
    (``{"name", "data_area_id", "fiscal_year", "fiscal_period"}``) for the
    entity rows' sources."""
    _refuse_unless_ok(payload)
    drill = payload["drill"]
    by_key = _submissions_by_key(submissions)
    out = []
    for item in drill["rows"]:
        out.append(_row(item.get("label") or item.get("entity"), currency, item["amount"],
                        entity=item.get("entity"), source=_source(item.get("source"), by_key),
                        layer=item["layer"], bold=1))
        for account in item.get("accounts") or []:
            name = account.get("account_name")
            label = f"{account['main_account']} {name}" if name else account["main_account"]
            out.append(_row(label, currency, account["amount"], indent=1,
                            main_account=account["main_account"]))
        if "journals" in item:
            out.append(_row(f"Journals {item.get('journals_basis') or ''}".strip(), currency,
                            indent=1, is_journals=1))
            for journal in item["journals"]:
                out.append(_row(_journal_label(journal), currency, journal["amount"], indent=2,
                                source=(JOURNAL_DOCTYPE, journal["journal_id"])))
    out.append(_row("Total", currency, drill["total"], bold=1, is_total=1))
    return out


def _ambiguous_text(payload, submissions):
    """One sentence per entity row with more than one submitted trial
    balance for its period: no link is shown for it."""
    by_key = _submissions_by_key(submissions)
    lines = []
    for item in payload["drill"]["rows"]:
        source = item.get("source") or {}
        if source.get("kind") == "tb" and source.get("entity"):
            names = by_key.get(_tb_key(source)) or []
            if len(names) > 1:
                lines.append("%s has %d submitted trial balances for %s (%s): no source link." % (
                    source["entity"], len(names),
                    period_name(source["fiscal_year"], source["fiscal_period"]),
                    ", ".join(sorted(names))))
    return lines


def message(payload, submissions=()):
    """The report message: ``Statement drill · FY2025 P07 · <group> ·
    <heading> <name> (<section>)``, the dimensions note, and any ambiguous
    trial balance, each escaped, joined by ``<br>`` (Desk renders the
    message as HTML)."""
    _refuse_unless_ok(payload)
    drill = payload["drill"]
    period = payload["period"]
    heading = " ".join(p for p in (str(drill.get("heading") or payload.get("heading")),
                                   drill.get("heading_name")) if p)
    if drill.get("section"):
        heading += f" ({drill['section']})"
    header = " · ".join(
        ["Statement drill", period_name(period["fiscal_year"], period["fiscal_period"])]
        + [p for p in (payload.get("consolidation_group"), heading) if p])
    lines = [header, drill.get("dimensions_note")] + _ambiguous_text(payload, submissions)
    return "<br>".join(_html.escape(line) for line in lines if line)
