"""Konsol home charts, pure (konsol#407 phase 2).

Lays out the seven Custom charts on the Konsol home workspace (and the
"Konsol Overview" dashboard) in Frappe's chart format, ``{"labels": [...],
"datasets": [{"name", "values"}]}``, from what the close app's own readers
return. No rule is decided here; charts_api.py names the reader of each.

- Every fixed category is listed even when its count is 0, so a bar never
  silently disappears; a value outside the expected ones gets its own label
  rather than being dropped.
- Where two readers must agree (the sign-off gate's missing trial balances
  and the caller's own lists; one approved rate per key), a disagreement
  raises ``ValueError`` naming it, never a guessed count. charts_api turns
  it into the chart's note.

Imports nothing from frappe; ``period_name`` is loaded by path, so the host
tests need no site.
"""
import datetime
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_period_name():
    spec = importlib.util.spec_from_file_location(
        "konsol_close_charts_model_period_name", os.path.join(_HERE, "period_name.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()

NOT_SET = "Not set"
TB_SERIES = ("Submitted", "Declared exception", "Missing")
RATE_TYPES = ("Closing", "Average")
NET_RESULT = "Net result"
#: Shown as the series name while Close Settings declares no heading: the
#: chart says what is missing instead of picking a heading.
NET_SALES_UNDECLARED = "Net sales: declare the Net Sales Heading in Close Settings"


def chart(labels, datasets):
    """Frappe's chart format from ``labels`` and ``[(name, values)]``."""
    return {"labels": list(labels),
            "datasets": [{"name": name, "values": list(values)} for name, values in datasets]}


def _amount(value):
    """``value`` as a float, with -0.0 (the P&L sign flip of an empty
    period) made 0.0."""
    return float(value) + 0.0


def _key(value):
    return int(value[0]), int(value[1])


def _labels(keys):
    return [period_name(*k) for k in keys]


def _date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def tb_progress(keys, progress, submitted, exceptions, allowed):
    """Chart 1: per period, the entities that owe a trial balance (the sign-off
    gate's ``trial_balance_progress``), stacked Submitted / Declared exception /
    Missing, cut to ``allowed`` (None: unscoped).

    ``progress`` is ``{key: {"expected", "missing"}}``; ``submitted`` and
    ``exceptions`` are ``{key: {entity}}`` from the caller's own reads. An
    expected entity the gate does not call missing is Submitted when it has a
    submitted TB, else a Declared exception; with neither, the gate and the
    reads disagree and it raises."""
    keys = [_key(k) for k in keys]
    counts = {s: [] for s in TB_SERIES}
    for key in keys:
        row = progress[key]
        missing = set(row["missing"])
        tbs = submitted.get(key, set())
        excs = exceptions.get(key, set())
        tally = dict.fromkeys(TB_SERIES, 0)
        for entity in row["expected"]:
            if allowed is not None and entity not in allowed:
                continue
            if entity in missing:
                tally["Missing"] += 1
            elif entity in tbs:
                tally["Submitted"] += 1
            elif entity in excs:
                tally["Declared exception"] += 1
            else:
                raise ValueError(
                    "the sign-off gate counts %s in for %s, but no submitted trial balance or "
                    "TB Exception of it can be read" % (entity, period_name(*key)))
        for series in TB_SERIES:
            counts[series].append(tally[series])
    return chart(_labels(keys), [(s, counts[s]) for s in TB_SERIES])


def _pl_lines(statement):
    for section in statement["sections"]:
        if section["section"] == "Profit and Loss":
            return section["lines"]
    raise ValueError("the statement has no Profit and Loss section")


def pl_trend(keys, statements, heading, currency):
    """Chart 2: net sales and net result per period, each the ``current``
    amount of ``statement_model.statement``'s Profit and Loss line (the
    statement the Numbers screen shows). Net sales is the line of ``heading``,
    the Close Settings Net Sales Heading; undeclared, its series is named
    ``NET_SALES_UNDECLARED`` with zeros. A declared heading that is not a line
    of the statement raises."""
    keys = [_key(k) for k in keys]
    sales, result = [], []
    for key in keys:
        lines = _pl_lines(statements[key])
        net = [line for line in lines if line.get("kind") == "net_result"]
        if len(net) != 1:
            raise ValueError("the statement for %s has %d net result lines"
                             % (period_name(*key), len(net)))
        result.append(_amount(net[0]["current"]))
        if heading:
            line = next((line for line in lines if line.get("kind") == "heading"
                         and line.get("heading") == heading), None)
            if line is None:
                raise ValueError(
                    "the Net Sales Heading %s is not a Profit and Loss heading of the published "
                    "chart; correct it in Close Settings" % heading)
            sales.append(_amount(line["current"]))
        else:
            sales.append(0)
    unit = " (%s)" % currency if currency else ""
    sales_name = "Net sales%s" % unit if heading else NET_SALES_UNDECLARED
    return chart(_labels(keys), [(sales_name, sales), (NET_RESULT + unit, result)])


def ic_top(groups, limit=10):
    """Chart 3: the ``limit`` intercompany pairs with the largest absolute
    difference, from ``ic_api.get_ic``'s groups (already masked to the
    caller). A pair is labelled by its entities and accounts, and by its
    group when there are several. A pair with no difference raises."""
    several = len(groups) > 1
    pairs = []
    for group in groups:
        for pair in group["pairs"]:
            difference = pair.get("difference")
            if difference is None:
                raise ValueError("the pair %s / %s has no difference"
                                 % (pair.get("entity_a"), pair.get("entity_b")))
            label = "%s ↔ %s · %s/%s" % (pair.get("entity_a"), pair.get("entity_b"),
                                         pair.get("account_a"), pair.get("account_b"))
            if several:
                label += " (%s)" % group.get("consolidation_group")
            pairs.append((abs(float(difference)), label))
    pairs.sort(key=lambda p: (-p[0], p[1]))
    top = pairs[:limit]
    return chart([label for _v, label in top],
                 [("Difference (absolute)", [v for v, _label in top])])


def approvals(items, doctypes):
    """Chart 4: the approvals queue's pending items (``approvals_api.queue_for``
    ``items``) counted by document type, every one of ``doctypes`` listed."""
    counts = dict.fromkeys(doctypes, 0)
    for item in items:
        counts[item["doctype"]] = counts.get(item["doctype"], 0) + 1
    return chart(list(counts), [("Waiting for approval", list(counts.values()))])


def entities_by_status(statuses, options):
    """Chart 6: entities counted by ``status``, every Select option listed."""
    counts = dict.fromkeys(options, 0)
    for status in statuses:
        key = status or NOT_SET
        counts[key] = counts.get(key, 0) + 1
    return chart(list(counts), [("Entities", list(counts.values()))])


def top_currencies(functional_currencies, reporting_currency, limit=3):
    """The ``limit`` functional currencies held by the most entities, other
    than the group's reporting currency (ties by code)."""
    counts = {}
    for code in functional_currencies:
        if code and code != reporting_currency:
            counts[code] = counts.get(code, 0) + 1
    return [code for code, _n in sorted(counts.items(), key=lambda c: (-c[1], c[0]))][:limit]


def rates(keys, currencies, rows):
    """Chart 5: per currency, its Closing and Average rate into the group
    currency per period. ``rows`` carry ``from_currency``, ``rate_type``,
    ``fiscal_year``, ``fiscal_period`` and the true ``rate``. Two approved
    rates for one key raise. A period with no approved rate for any of
    ``currencies`` is left out (nothing to compare); a period missing only
    some of them plots those as 0 and names each in its label ("FY2025 P03
    (no EUR average)"), so a gap is never read as a rate (review of #411)."""
    keys = [_key(k) for k in keys]
    found = {}
    for row in rows:
        k = (row["from_currency"], row["rate_type"], int(row["fiscal_year"]),
             int(row["fiscal_period"]))
        if k in found:
            raise ValueError("two approved %s rates for %s in %s"
                             % (k[1], k[0], period_name(k[2], k[3])))
        found[k] = float(row["rate"])
    series = [(code, rate_type) for code in currencies for rate_type in RATE_TYPES]
    shown, labels = [], []
    for key in keys:
        gaps = [s for s in series if s + key not in found]
        if len(gaps) == len(series):
            continue
        shown.append(key)
        label = period_name(*key)
        if gaps:
            label += " (no %s)" % ", ".join("%s %s" % (c, t.lower()) for c, t in gaps)
        labels.append(label)
    datasets = [("%s %s" % (code, rate_type.lower()),
                 [found.get((code, rate_type) + key, 0) for key in shown])
                for code, rate_type in series]
    return chart(labels, datasets)


def days_to_close(states, signed):
    """Chart 7: per period, the days from its end date to its sign-off.
    ``states`` are ``period_model.period_states`` entries (``key``,
    ``end_date``); ``signed`` is ``signoff_gate.latest_signed_runs`` (the
    periods whose latest run is signed, with ``signed_off_at``). A period not
    signed off is 0 and says so in its label, never left out."""
    labels, values = [], []
    for state in states:
        key = _key(state["key"])
        name = period_name(*key)
        run = signed.get(key)
        if run is None:
            labels.append("%s (not signed off)" % name)
            values.append(0)
        elif not run.get("signed_off_at"):
            labels.append("%s (sign-off date not recorded)" % name)
            values.append(0)
        else:
            labels.append(name)
            values.append((_date(run["signed_off_at"]) - _date(state["end_date"])).days)
    return chart(labels, [("Days to close", values)])
