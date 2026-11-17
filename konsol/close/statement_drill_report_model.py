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

The Budget basis (``payload["basis"] == "Budget"``, konsol#348 S5 K10a;
``statement_api.get_budget_drill``): the drill is ``drill_model.with_rates``
over a heading, or ``unbalanced_drill``. The source ``kind`` ``cycle`` is
accepted: an entity row links its Budget Cycle, and carries
``budget_entries_filters`` (``{"cycle", "data_area_id"}``, the Budget Entry
list the Desk JS opens); every other row carries ``None``. Every row gains
``rate`` and ``rate_set`` text: each ``{fx_method, rate}`` of the row's
``rates`` as ``"<fx_method> <rate>"`` joined by "; ", the rate as the payload
gives it, never rounded. ``fx_method`` is the chart's DECLARED Translation
Method as recorded when the budget was built (PR #554 review m6), not the
method the core applied: the core can apply closing before a historical
account's first tranche, so the column is labelled "Declared method and
rate". A blank ``fx_method`` reads ``"closing (undeclared) <rate>"``: the
Main Account left the Translation Method blank, and the consolidation core
translates an undeclared account at the closing rate.
``rate_set`` is the distinct Rate Sets in order. Rows without ``rates``
(CTA, NCI, outside-scope aggregate, Total, the unbalanced entity rows) show
both as None; an entity row with accounts but no ``rates`` raises
``ValueError`` naming the entity: the rate is never shown blank. The other
bases gain none of these keys.

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
CYCLE_DOCTYPE = "Budget Cycle"
BUDGET = "Budget"
_SOURCE_KINDS = ("tb", "journals", "cycle")
#: PR #554 review m6: the chart's declared method, not the applied one
#: (statement_export_model.BUDGET_DRILL_HEADER carries the same label).
RATE_LABEL = "Declared method and rate"
#: What a blank declared method reads as: the core translates an undeclared
#: account at the closing rate (consolidation_core: join_use_nulls=0 gives '').
UNDECLARED_METHOD = "closing (undeclared)"


def columns(minor_unit=None, basis=None):
    """``minor_unit`` is the drill's own (the reporting currency's ISO minor
    unit, konsol#354): the Amount column shows that many places. ``None``
    (no drill) sets no precision, never a guessed 2. ``basis`` ``Budget``
    adds the "Declared method and rate" and Rate Set columns after Amount
    (module docstring: declared, not applied)."""
    amount = {"fieldname": "amount", "label": "Amount", "fieldtype": "Currency",
              "options": "currency", "width": 170}
    if minor_unit is not None:
        amount["precision"] = minor_unit
    budget = []
    if basis == BUDGET:
        budget = [
            {"fieldname": "rate", "label": RATE_LABEL, "fieldtype": "Data", "width": 200},
            {"fieldname": "rate_set", "label": "Rate Set", "fieldtype": "Link",
             "options": "Rate Set", "width": 110},
        ]
    return [
        {"fieldname": "label", "label": "Layer / account", "fieldtype": "Data", "width": 380},
        {"fieldname": "entity", "label": "Entity", "fieldtype": "Link", "options": "Entity",
         "width": 110},
        {"fieldname": "main_account", "label": "Account", "fieldtype": "Link",
         "options": "Main Account", "width": 110},
        amount,
    ] + budget + [
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
    if kind == "cycle" and source.get("cycle"):
        return CYCLE_DOCTYPE, source["cycle"]
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


def _rate_texts(item):
    """``(rate, rate_set)`` text for a drill row or account, or two Nones."""
    rates = item.get("rates")
    if not rates:
        return None, None
    rate = "; ".join(
        f"{r.get('fx_method') or UNDECLARED_METHOD} {r.get('rate')}" for r in rates)
    sets = []
    for r in rates:
        if r.get("rate_set") and r["rate_set"] not in sets:
            sets.append(r["rate_set"])
    return rate, ("; ".join(sets) or None)


def rows(payload, currency, submissions=()):
    """The report rows for ``payload`` (see the module docstring).
    ``submissions`` are the submitted Trial Balance Submissions
    (``{"name", "data_area_id", "fiscal_year", "fiscal_period"}``) for the
    entity rows' sources."""
    _refuse_unless_ok(payload)
    drill = payload["drill"]
    by_key = _submissions_by_key(submissions)
    budget = payload.get("basis") == BUDGET
    out = []

    def extra(item=None, filters=None):
        if not budget:
            return {}
        rate, rate_set = _rate_texts(item or {})
        return {"rate": rate, "rate_set": rate_set, "budget_entries_filters": filters}

    for item in drill["rows"]:
        source = _source(item.get("source"), by_key)
        filters = None
        if budget:
            if item.get("accounts") and not item.get("rates"):
                raise ValueError(
                    f"{item.get('entity')}: the budget drill row has accounts but no translation "
                    "rate; the rate is never shown blank")
            if source[0] == CYCLE_DOCTYPE:
                filters = {"cycle": source[1], "data_area_id": item.get("entity")}
        out.append(_row(item.get("label") or item.get("entity"), currency, item["amount"],
                        entity=item.get("entity"), source=source,
                        layer=item["layer"], bold=1, **extra(item, filters)))
        for account in item.get("accounts") or []:
            name = account.get("account_name")
            label = f"{account['main_account']} {name}" if name else account["main_account"]
            out.append(_row(label, currency, account["amount"], indent=1,
                            main_account=account["main_account"], **extra(account)))
        if "journals" in item:
            out.append(_row(f"Journals {item.get('journals_basis') or ''}".strip(), currency,
                            indent=1, is_journals=1, **extra()))
            for journal in item["journals"]:
                out.append(_row(_journal_label(journal), currency, journal["amount"], indent=2,
                                source=(JOURNAL_DOCTYPE, journal["journal_id"]), **extra()))
    out.append(_row("Total", currency, drill["total"], bold=1, is_total=1, **extra()))
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
    <heading> <name> (<section>)`` (Budget basis: ``Budget`` before the
    group, then a Budget Cycle / layers / Rate Set line; entity basis: ``Basis: Entity · <code>
    <name>`` in place of the group), the dimensions note, and any ambiguous
    trial balance, each escaped, joined by ``<br>`` (Desk renders the
    message as HTML)."""
    _refuse_unless_ok(payload)
    drill = payload["drill"]
    period = payload["period"]
    heading = " ".join(p for p in (str(drill.get("heading") or payload.get("heading")),
                                   drill.get("heading_name")) if p)
    if drill.get("section"):
        heading += f" ({drill['section']})"
    if payload.get("basis") == BUDGET:
        scope = [BUDGET, payload.get("consolidation_group")]
    elif payload.get("basis") == "Entity":
        # #495: the entity basis names the entity, never a group.
        who = " ".join(p for p in (payload.get("entity"), payload.get("entity_name")) if p)
        scope = ["Basis: Entity", who]
    else:
        scope = [payload.get("consolidation_group")]
    header = " · ".join(
        ["Statement drill", period_name(period["fiscal_year"], period["fiscal_period"])]
        + [p for p in scope + [heading] if p])
    lines = [header]
    if payload.get("basis") == BUDGET:
        meta = payload.get("meta") or {}
        lines.append(
            f"Budget Cycle {meta.get('cycle')}, {', '.join(meta.get('layers') or [])}, "
            f"Rate Set {meta.get('rate_set')}; before intercompany eliminations and without "
            "group-level adjustments")
    lines += [drill.get("dimensions_note")] + _ambiguous_text(payload, submissions)
    return "<br>".join(_html.escape(line) for line in lines if line)
