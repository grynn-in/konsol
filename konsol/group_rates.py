"""Governed group exchange rates (konsol#103): the rules around Rate Sheet.

Decided by the user on 13 Sep 2026. Group finance owns the group's rates in
konsol, held on Rate Sheets since konsol#592 (Deepak Pai, 22 Nov 2026): one
Statutory sheet per period for the actuals, one Constant sheet per year for
budgets. The ERP feed is demoted to a proposal: ``prefill_from_erp`` reads
what the ERP quotes for a period and fills a DRAFT Statutory sheet, and a
person approves (submits) it. Rates lock when their period closes, and a
period cannot close while a currency its ledgers translate lacks an approved
Closing or Average rate into the group reporting currency
(``assert_rates_complete``, called from Period Status).

A rate is entered as a quote per 1, 10, 100, 1,000 or 10,000 units of the
from-currency (0.6607 USD per 100 JPY) and published as its true rate
(0.006607), computed once by konsol: one source of truth for FX rates, and the
warehouse never scales or inverts one. Every check below works on the true rate.

Two plausibility checks guard entry (the #138 review):

* **Hard:** a rate more than 10x from what the ISO Currency references imply
  is refused. Each ISO Currency carries ``usd_log10``, roughly log10 of its
  units per 1 USD, so the expected log10 of a rate "to per 1 from" is
  ``usd_log10(to) - usd_log10(from)``. One rule for every pair, IDR and VND
  included, and a 100x error on JPY trips it. A currency with no reference is
  refused until one is set.
* **Soft:** a rate that moves more than the declared Rate Move threshold
  (Close Settings, konsol#305-D2-9) from the previous approved rate for its
  key, or from the ERP quote it was proposed from, needs a Reason for Change.
  Real moves that size happen (ARS fell 55% in Dec 2023), so a reason lets it
  through. The threshold is never guessed: while it is undeclared, any such
  move needs the declaration or a reason.
* **Magnitude warning (konsol#179):** a rate more than the declared
  Magnitude Warning Factor (Close Settings) from what the references imply,
  but within the hard 10x, needs a Reason for Change, and the message names
  the Quoted Per that brings it in line. It catches a first rate entered per
  the wrong unit, which the hard guard misses for JPY, IDR and EUR (just
  under 10x off). While the factor is undeclared, every save shows a warning
  and nothing is refused.
"""
import decimal
import json
import math
import re

import frappe

from konsol import rate_sheet_model
from konsol.close import close_policy_model
from konsol.fx_reference import REFERENCE_CURRENCY, usd_reference  # noqa: F401 — the one rule
from konsol.period_status import PeriodNotDeclared, assert_open, period_dates  # noqa: F401 — surfaced for callers

RATE_SET_DOCTYPE = "Rate Set"
RATE_TYPES = ("Closing", "Average")
PREFILL_ROLES = ("EPM Analyst", "EPM Admin", "System Manager")
PREFILL_SOURCE = "ERP pre-fill"

#: The ERP adapter's rate-type names, for the pre-fill only: which ERP rate
#: types may propose each governed rate type, in order of preference. The
#: translation never reads ERP rate types (konsolidat#93); these strings are
#: what the demo D365 calls them, and a customer with other names maps them here.
ERP_RATE_TYPES = {"Closing": ("Closing", "Default"), "Average": ("Average", "Default")}

#: The magnitude guard's reference: ISO Currency.usd_log10, roughly log10 of
#: the currency's units per 1 USD (seeded by konsol.currency_references). USD is
#: the anchor at 0. A Frappe Float cannot be NULL (unset reads as 0), so for
#: every other currency 0 means "not set"; so does NaN in the warehouse.
REFERENCE_FIELD = "usd_log10"
#: A rate more than this many powers of ten from the references is refused.
MAGNITUDE_TOLERANCE_DECADES = 1.0
#: "Quoted per" (decided 13 Sep 2026: one source of truth, published by konsol).
#: A quote is units of the group currency per this many units of the
#: from-currency, so its direction never flips: 0.6607 USD per 100 JPY. The
#: TRUE rate is quote / quoted_per; konsol computes it once, when it publishes,
#: and the warehouse never scales or inverts a rate.
QUOTED_PER = (1, 10, 100, 1000, 10000)
#: MariaDB keeps 9 decimal places. A quote must keep at least this many
#: significant digits there (0.0001 or more); a bigger Quoted per fixes it.
MIN_SIGNIFICANT_DIGITS = 6
#: The pre-fill aims higher: the smallest Quoted per that puts
#: the quote at 0.1 or more (9 significant digits). JPY -> USD is per 100.
PREFERRED_MIN_QUOTE = 0.1


# -- the actuals set (konsol#348) -------------------------------------------------

#: Why an actuals reader stops while no Rate Set is flagged Is Actuals.
NO_ACTUALS_SET = (
    "No Rate Set is flagged Is Actuals: the actuals translation, the close gate and Pre-fill "
    "from ERP read exactly one set and assume none. Flag one Rate Set as Is Actuals (konsol#348).")


def find_actuals_rate_set():
    """(name, None) of the one Rate Set flagged Is Actuals, else (None, why):
    none flagged, or more than one (a patch or a race got past the
    controller's refusal). Never guesses a set."""
    names = frappe.get_all(RATE_SET_DOCTYPE, filters={"is_actuals": 1}, pluck="name",
                           order_by="name asc", limit_page_length=0)
    if len(names) == 1:
        return names[0], None
    if not names:
        return None, NO_ACTUALS_SET
    return None, (f"{', '.join(names)} are all flagged Is Actuals: exactly one Rate Set may be. "
                  "Clear the flag on all but one (konsol#348).")


def actuals_rate_set():
    """The Rate Set the actuals readers select by (S4-Q1 c). Refuses by name
    while none, or more than one, is flagged."""
    name, problem = find_actuals_rate_set()
    if problem:
        frappe.throw(problem, frappe.ValidationError)
    return name


# -- pure rules -----------------------------------------------------------------

def true_rate(quote, quoted_per):
    """Units of the group currency per 1 unit of the from-currency.

    Divided in decimal: Quoted Per is a power of ten, so the quotient is exact
    and the Float64 published is the nearest double to it (0.6607 per 100 is
    0.006607, where float division gives 0.006606999999999999)."""
    return float(decimal.Decimal(str(quote or 0)) / int(quoted_per or 1))


def significant_digits(quote):
    """How many significant digits ``quote`` keeps at 9 decimal places:
    0.0066 keeps 7 (0.006600000), 0.66 keeps 9, 0.0000394 only 5."""
    quote = float(quote or 0)
    if quote <= 0:
        return 0
    return 9 + math.floor(math.log10(quote)) + 1


def choose_quoted_per(rate):
    """(quote, quoted_per) for a true ``rate``: the smallest Quoted per that
    puts the quote at 0.1 or more (the largest, if none does). Quote at 9 dp."""
    rate = float(rate)
    per = next((p for p in QUOTED_PER if rate * p >= PREFERRED_MIN_QUOTE), QUOTED_PER[-1])
    return round(rate * per, 9), per


def quote_label(quote, quoted_per, from_currency, to_currency):
    """The quote as a person reads it: "0.6607 USD per 100 JPY"."""
    per = int(quoted_per or 1)
    return f"{float(quote or 0):.9g} {to_currency} per {f'{per:,} ' if per > 1 else ''}{from_currency}"


def usd_references(codes):
    """{code: usd_log10 or None} from ISO Currency, for the codes given."""
    codes = sorted({c for c in codes if c})
    rows = frappe.get_all("ISO Currency", filters={"name": ["in", codes]},
                          fields=["name", REFERENCE_FIELD], limit_page_length=0) if codes else []
    found = {r.name: r.get(REFERENCE_FIELD) for r in rows}
    return {c: usd_reference(c, found.get(c)) for c in codes}


#: What the magnitude rule says about a rate: the same four outcomes, in the
#: same order, as the warehouse's fx_magnitude_problem (konsolidat #176).
VERDICTS = ("ok", "invalid", "no_reference", "implausible")


def magnitude_verdict(from_currency, to_currency, rate, refs=None):
    """(verdict, why) for ``rate``, units of ``to_currency`` per 1
    ``from_currency``: "ok" (why is None), "invalid" (not a positive, finite
    number), "no_reference" (a currency has no usd_log10) or "implausible"
    (more than 10x from what the references imply: abs(log10(rate) -
    (usd_log10(to) - usd_log10(from))) > 1). Checked in that order, as dbt does."""
    rate = float(rate)
    if not math.isfinite(rate) or rate <= 0:
        return "invalid", "Rate must be a positive, finite number."
    refs = usd_references((from_currency, to_currency)) if refs is None else refs
    unset = [c for c in dict.fromkeys((from_currency, to_currency)) if refs.get(c) is None]
    if unset:
        return "no_reference", (
            f"No magnitude reference for {' and '.join(unset)}: set USD Reference (log10) "
            f"({REFERENCE_FIELD}) on ISO Currency {', '.join(unset)}, roughly log10 of its units "
            "per 1 USD, so a scaling error can be told from a real rate (#138).")
    expected = refs[to_currency] - refs[from_currency]
    off = math.log10(rate) - expected
    if abs(off) <= MAGNITUDE_TOLERANCE_DECADES:
        return "ok", None
    return "implausible", (
        f"{rate:.9g} {to_currency} per {from_currency} is about {10 ** abs(off):,.0f}x "
        f"{'above' if off > 0 else 'below'} the roughly {10 ** expected:.3g} the ISO Currency "
        f"references imply (usd_log10 {to_currency} {refs[to_currency]:g}, {from_currency} "
        f"{refs[from_currency]:g}); more than 10x off is a scaling error, not a market move (#138). "
        f"Enter the true rate: units of {to_currency} per 1 {from_currency}, with no multiplier, "
        f"or quote it per 10, 100, 1,000 or 10,000 {from_currency} (Quoted Per).")


def magnitude_problem(from_currency, to_currency, rate, refs=None):
    """None, or why ``rate`` is refused (#138): ``magnitude_verdict``'s reason."""
    return magnitude_verdict(from_currency, to_currency, rate, refs)[1]


def magnitude_warning(from_currency, to_currency, quote, quoted_per, factor, refs=None):
    """None, or why a rate entered as ``quote`` per ``quoted_per`` units of
    ``from_currency`` needs a Reason for Change (konsol#179): it is more than
    ``factor`` times above or below what the references imply, but within the
    hard 10x (``magnitude_verdict`` refuses beyond that, and owns "invalid"
    and "no_reference" too). A wrong Quoted Per is the usual cause, and it is
    just under 10x off for JPY, IDR and EUR, so the message names the listed
    Quoted Per that brings the quote closest to the references.

    ``factor`` None (undeclared) gives None: the caller reports the gap."""
    per = int(quoted_per or 1)
    rate = true_rate(quote, per)
    if factor is None or magnitude_verdict(from_currency, to_currency, rate, refs)[0] != "ok":
        return None
    refs = usd_references((from_currency, to_currency)) if refs is None else refs
    expected = refs[to_currency] - refs[from_currency]
    off = math.log10(rate) - expected
    if abs(off) <= math.log10(factor):
        return None
    # A unit explains the gap only if moving by it leaves the rate within the factor.
    decades = round(off)
    entered = quote_label(quote, per, from_currency, to_currency)
    if decades == 0 or abs(off - decades) > math.log10(factor):
        hint = "No Quoted Per brings it in line; check the quote itself."
    elif per * 10 ** decades in QUOTED_PER:
        hint = (f"A wrong Quoted Per is the likely cause: did you mean "
                f"{quote_label(quote, per * 10 ** decades, from_currency, to_currency)}?")
    else:
        hint = (f"No listed Quoted Per fits; the quote may be {10 ** abs(decades):,}x too "
                f"{'high' if decades > 0 else 'low'}.")
    implied, implied_per = choose_quoted_per(10 ** expected)
    implied = quote_label(float(f"{implied:.3g}"), implied_per, from_currency, to_currency)
    return (f"{entered} is about {10 ** abs(off):.1f}x {'above' if off > 0 else 'below'} the roughly "
            f"{implied} the ISO Currency references imply: "
            f"more than the Magnitude Warning Factor ({factor:g}x, Close Settings). {hint} "
            "If the rate is right, say why (Reason for Change).")


#: Shown on every rate save while the Magnitude Warning Factor is undeclared.
MAGNITUDE_WARNING_UNDECLARED = close_policy_model.MAGNITUDE_WARNING_MESSAGE


def magnitude_warning_factor():
    """The declared Magnitude Warning Factor (Close Settings, konsol#179), or
    None while it is undeclared. Never guessed."""
    return close_policy_model.magnitude_factor(
        frappe.db.get_single_value("Close Settings", "magnitude_warning_factor"))


def move_threshold():
    """The declared Rate Move threshold (Close Settings, konsol#305-D2-9), as a
    fraction, or None while it is undeclared. Never guessed."""
    return close_policy_model.move_fraction(frappe.db.get_single_value("Close Settings", "rate_move_threshold"))


def move_problem(rate, previous=None, erp_rate=None, unit="", threshold=None):
    """None, or why ``rate`` needs a Reason for Change: it moves more than
    ``threshold`` (a fraction, e.g. 0.5 for 50%) from ``previous`` ((rate,
    label) of the last approved rate for its key) or from ``erp_rate`` (the
    ERP quote it was proposed from). Rates are "to per 1 from", whatever unit
    they are quoted per; ``unit`` labels them ("USD per JPY").

    With nothing to compare against, this is always None: a first rate for a
    key needs no reason regardless of the threshold. With a reference and no
    declared ``threshold``, the gap names Close Settings rather than guessing
    a value (konsol#305-D2-9)."""
    rate = float(rate)
    unit = f" {unit}" if unit else ""
    refs = [(ref, what) for ref, what in (
        (previous[0], f"the previous approved rate {previous[1]}") if previous else (None, None),
        (erp_rate, "the ERP quote"),
    ) if ref and float(ref) > 0]
    if not refs:
        return None
    if threshold is None:
        return close_policy_model.RATE_MOVE_MESSAGE
    moves = []
    for ref, what in refs:
        change = rate / float(ref) - 1.0
        if abs(change) > threshold:
            moves.append(f"{change:+.0%} from {what} ({float(ref):.9g}{unit})")
    if not moves:
        return None
    return (f"This rate ({rate:.9g}{unit}) moves " + " and ".join(moves) + f". A move over {threshold * 100:g}% can be "
            "real, but say why (Reason for Change) before it is saved.")


def period_end(fiscal_year, fiscal_period):
    """The last day of the declared period (konsol#189: ``period_status.
    period_dates``, never invented by month arithmetic): where a Closing
    rate is struck. Raises PeriodNotDeclared for an undeclared period."""
    return period_dates(fiscal_year, fiscal_period)[1]


def period_start(fiscal_year, fiscal_period):
    """The first day of the declared period (konsol#189: ``period_status.
    period_dates``, never invented by month arithmetic): an Average rate
    spans it, and the ERP is asked for its quote there. Raises
    PeriodNotDeclared for an undeclared period."""
    return period_dates(fiscal_year, fiscal_period)[0]


def _leg(table, a, b):
    """(rate, valid_from, how) for a -> b from one ERP rate table, or None."""
    if (a, b) in table:
        rate, valid_from = table[(a, b)]
        return rate, valid_from, "direct"
    if (b, a) in table:
        rate, valid_from = table[(b, a)]
        return 1.0 / rate, valid_from, f"inverse of {b}→{a}"
    return None


def _lookup(table, a, b):
    """A quote for a -> b: direct, the inverse of b -> a, or DERIVED as a cross
    through a third currency p (a -> p divided by b -> p). Decision 6: only
    rates into a group currency are governed, and crosses are derived."""
    leg = _leg(table, a, b)
    if leg:
        return leg
    for p in sorted({c for pair in table for c in pair} - {a, b}):
        x, y = _leg(table, a, p), _leg(table, b, p)
        if x and y:
            return x[0] / y[0], max(x[1], y[1]), f"cross via {p}"
    return None


def resolve_quotes(rows, from_currency, to_currency, rate_type):
    """What the ERP feed quotes for one governed key: one quote per ERP source,
    from the first of ERP_RATE_TYPES[rate_type] that yields a rate.

    ``rows`` are (erp_source, erp_rate_type, from, to, rate, valid_from), each
    the latest quote on or before the period start."""
    tables = {}
    for source, erp_type, f, t, rate, valid_from in rows:
        if float(rate) > 0 and f != t:
            tables.setdefault(source, {}).setdefault(erp_type, {})[(f, t)] = (float(rate), str(valid_from))
    quotes = []
    for source in sorted(tables):
        for erp_type in ERP_RATE_TYPES[rate_type]:
            hit = _lookup(tables[source].get(erp_type, {}), from_currency, to_currency)
            if hit:
                quotes.append({"source": source, "erp_type": erp_type, "rate": hit[0],
                               "valid_from": hit[1], "how": hit[2]})
                break
    return quotes


def _sig(rate):
    """A rate to 12 significant digits: equal quotes compare equal whatever
    their magnitude (a fixed 9 decimal places would merge distinct IDR rates)."""
    return float(f"{float(rate):.12g}")


def describe_quotes(quotes, fiscal_year, fiscal_period, from_currency=None, to_currency=None):
    """The source note: every ERP source's quote, as units of the group
    currency per 1 unit of the from-currency, whatever unit the draft is
    quoted per."""
    unit = f" {to_currency} per {from_currency}" if from_currency and to_currency else ""
    parts = [f"{q['source']} {q['erp_type']} {q['rate']:.9g}{unit}, valid from {q['valid_from']} ({q['how']})"
             for q in quotes]
    note = f"Pre-filled from the ERP feed for FY{fiscal_year} P{fiscal_period}: " + "; ".join(parts) + "."
    if len({_sig(q["rate"]) for q in quotes}) > 1:
        note = (f"ERP SOURCES DISAGREE: the proposal takes {quotes[0]['source']}'s quote. Check "
                "before approving. " + note)
    return note


# -- the warehouse ------------------------------------------------------------------

def _ch_rows(sql, params=None):
    """Rows of a ClickHouse SELECT. Values are bound as HTTP query parameters
    (``{name:Type}`` in the SQL, ``param_<name>`` on the wire), never interpolated."""
    from konsol.clickhouse import execute

    raw = execute(sql + " FORMAT JSONCompact",
                  {f"param_{k}": v for k, v in (params or {}).items()})
    return json.loads(raw).get("data", []) if raw else []


#: ClickHouse error names meaning "nothing has been built yet": the relation or
#: its database does not exist. Matched as the "(NAME)" token ClickHouse puts in
#: every error, never as a "Code: NN" substring ("Code: 60" also matches 600-609).
NOT_BUILT_ERRORS = frozenset({"UNKNOWN_TABLE", "UNKNOWN_DATABASE"})
_ERROR_NAME = re.compile(r"\(([A-Z][A-Z0-9_]+)\)")


def ch_error_names(exc):
    """The ClickHouse error names in an exception's text, e.g. {"UNKNOWN_TABLE"}."""
    text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
    return set(_ERROR_NAME.findall(text))


def _not_built(exc):
    return bool(ch_error_names(exc) & NOT_BUILT_ERRORS)


def ledgers_built():
    """True when the warehouse holds a trial balance table (it has built)."""
    rows = _ch_rows("EXISTS TABLE epm_gold.gold_trial_balance")
    return bool(rows) and int(rows[0][0]) == 1


#: Every group node and its currency ('' when unset): the node with no entity.
#: LEFT-JOINed, as the warehouse guard does, so a group with no reporting
#: currency comes back as '' rather than vanishing (join_use_nulls=0).
_GROUP_NODES = "(SELECT consolidation_group, reporting_currency FROM epm_gold.consolidation_groups WHERE data_area_id = '')"

#: The translation's own filter on gold_entity_ownership (and the guard's).
_TRANSLATED = "consolidation_method NOT IN ('equity', 'none') AND has_complete_chain = 1"


def translation_gaps(fiscal_year, fiscal_period):
    """(pairs, groups without a currency, entities without a currency) for exactly the rows
    gold_consolidated_trial_balance translates in the period: an entity with
    ledger rows, into each group gold_entity_ownership places it under for the
    period, with the translation's own filter (method not 'equity' or 'none',
    a complete chain, so nothing outside the ownership window). An equity
    associate's currency owes no rate. A group node with no reporting
    currency cannot be translated into at all; the warehouse guard refuses
    it, so the gate names it. An entity in that set whose functional currency
    is blank in silver_entity_currencies (or which has no row there) cannot be
    translated either: konsol#311, the gate names it rather than skipping it.
    konsol#252 refuses such a TB at intake, but Entity.validate still lets the
    currency be cleared after a TB is submitted.

    The currencies are LEFT-JOINed (join_use_nulls=0, so an entity with no row
    reads ''), and the entity code is selected only for a blank currency, so
    DISTINCT stays per currency pair for every other entity.

    Read from the last build: ownership changed since then is seen at the next."""
    rows = _ch_rows(
        "SELECT DISTINCT ec.accounting_currency, g.reporting_currency, eo.consolidation_group, "
        "       if(ec.accounting_currency = '', tb.data_area_id, '') "
        "FROM (SELECT DISTINCT data_area_id FROM epm_gold.gold_trial_balance "
        "      WHERE fiscal_year = {fy:UInt16} AND fiscal_period = {fp:UInt16}) AS tb "
        "LEFT JOIN (SELECT data_area_id, accounting_currency FROM epm_silver.silver_entity_currencies) AS ec "
        "    ON ec.data_area_id = tb.data_area_id "
        "INNER JOIN (SELECT consolidation_group, data_area_id FROM epm_gold.gold_entity_ownership "
        f"            WHERE fiscal_year = {{fy:UInt16}} AND fiscal_period = {{fp:UInt16}} AND {_TRANSLATED}) AS eo "
        "    ON eo.data_area_id = tb.data_area_id "
        f"LEFT JOIN {_GROUP_NODES} AS g ON g.consolidation_group = eo.consolidation_group "
        "WHERE ec.accounting_currency = '' OR ec.accounting_currency != g.reporting_currency",
        {"fy": int(fiscal_year), "fp": int(fiscal_period)},
    )
    return split_needs(rows)


def split_needs(rows):
    """(from, to, group, entity) rows -> ({(from, to)}, [groups with no
    currency], [entities with no currency]). ``entity`` is set only on a row
    whose ``from`` is blank; such a row makes no pair. Pure."""
    return ({(f, t) for f, t, _, _ in rows if f and t},
            sorted({g for _, t, g, _ in rows if not t}),
            sorted({e for f, _, _, e in rows if not f}))


def required_pairs(fiscal_year, fiscal_period):
    """The (entity currency, group reporting currency) pairs the period's
    translation needs (``translation_gaps``, without the blockers)."""
    return translation_gaps(fiscal_year, fiscal_period)[0]


def tree_pairs(fiscal_year, fiscal_period):
    """The pairs a period will need before its trial balances arrive (the
    pre-fill's fallback; the gate never reads this). gold_entity_ownership has
    rows only for periods with ledgers, so each (group, entity) takes its
    latest built period at or before this one, with the translation's filter:
    an equity-accounted or 'none' entity, or an incomplete chain, owes no rate."""
    rows = _ch_rows(
        "SELECT DISTINCT ec.accounting_currency, g.reporting_currency "
        "FROM (SELECT consolidation_group, data_area_id, "
        "             argMax(consolidation_method, (fiscal_year, fiscal_period)) AS consolidation_method, "
        "             argMax(has_complete_chain, (fiscal_year, fiscal_period)) AS has_complete_chain "
        "      FROM epm_gold.gold_entity_ownership "
        "      WHERE (fiscal_year, fiscal_period) <= ({fy:UInt16}, {fp:UInt16}) "
        "      GROUP BY consolidation_group, data_area_id) AS eo "
        "INNER JOIN (SELECT data_area_id, accounting_currency FROM epm_silver.silver_entity_currencies "
        "            WHERE accounting_currency != '') AS ec ON ec.data_area_id = eo.data_area_id "
        f"INNER JOIN {_GROUP_NODES} AS g ON g.consolidation_group = eo.consolidation_group "
        f"WHERE eo.{_TRANSLATED.replace(' AND ', ' AND eo.')} AND g.reporting_currency != '' "
        "AND ec.accounting_currency != g.reporting_currency",
        {"fy": int(fiscal_year), "fp": int(fiscal_period)})
    return {(f, t) for f, t in rows}


def erp_quote_rows(as_of):
    """The latest ERP quote on or before ``as_of`` per source, rate type and
    pair, from the canonical staging union of every ERP adapter."""
    types = ", ".join(f"'{t}'" for t in sorted({t for ts in ERP_RATE_TYPES.values() for t in ts}))
    return _ch_rows(
        "SELECT erp_source, rate_type, from_currency, to_currency, "
        "argMax(exchange_rate, valid_from), toString(max(valid_from)) "
        "FROM epm_staging.stg_exchange_rates "
        f"WHERE valid_from <= {{asof:Date}} AND exchange_rate > 0 AND rate_type IN ({types}) "
        "AND from_currency != to_currency "
        "GROUP BY erp_source, rate_type, from_currency, to_currency",
        {"asof": str(as_of)},
    )


# -- publishing ----------------------------------------------------------------------

#: Where konsol publishes the approved rates: the frozen contract, one row per
#: approved rate, `rate` the TRUE rate (units of to per 1 from).
PUBLISH_TABLE = "epm_staging.group_exchange_rates"
PUBLISH_COLUMNS = ("to_currency", "from_currency", "fiscal_year", "fiscal_period", "rate_type", "rate",
                   "document", "rate_set")
#: Seconds a publish waits for another one to finish before it gives up and
#: logs (the next approval, or the reconcile after a migrate, republishes).
PUBLISH_LOCK_WAIT = 120


def _ch_literal(value):
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


#: konsol#592 T10: the approved Rate Sheets, their rows and the declared
#: calendar (a Constant sheet publishes every published period of its year).
_SHEETS_SQL = ("SELECT name, rate_basis, fiscal_year, fiscal_period, supersedes, docstatus, modified "
               "FROM `tabRate Sheet` WHERE docstatus = 1 ORDER BY name")
_SHEET_ROWS_SQL = ("SELECT r.parent, r.from_currency, r.to_currency, r.quoted_per, r.average_quote, "
                   "r.closing_quote, r.rate FROM `tabRate Sheet Row` r JOIN `tabRate Sheet` s "
                   "ON s.name = r.parent WHERE s.docstatus = 1 AND r.parenttype = 'Rate Sheet' "
                   "AND r.parentfield = 'rates' ORDER BY r.parent, r.idx")
_CALENDAR_SQL = ("SELECT y.fiscal_year AS fiscal_year, p.fiscal_period AS period, p.period_type AS type, "
                 "p.start_date AS start_date, p.end_date AS end_date FROM `tabEPM Fiscal Year` y "
                 "JOIN `tabEPM Fiscal Year Period` p ON p.parent = y.name AND p.parenttype = 'EPM Fiscal Year' "
                 "AND p.parentfield = 'periods' ORDER BY y.fiscal_year, p.fiscal_period")
#: The Rate Set code a Constant sheet publishes under (pair 1: Rate Set stays,
#: and the patch adds a CONSTANT row; the plan's warehouse mapping).
CONSTANT_RATE_SET = "CONSTANT"


def _fresh_db():
    """A second connection to the site's database, built exactly as
    frappe.connect builds frappe.db (Frappe reads the site config)."""
    from frappe.database import get_db

    conf = frappe.local.conf
    db = get_db(socket=conf.db_socket, host=conf.db_host, port=conf.db_port, user=conf.db_name,
                password=conf.db_password, cur_db_name=conf.db_name)
    db.connect()
    return db


def _read_approved():
    """What the publish reads, as committed right now: the approved Rate
    Sheets with their rows, and the declared calendar (the last two only when
    a sheet is approved). Read on a connection of
    its own and closed at once: the snapshot starts at the first read, so the
    reads agree with each other (REPEATABLE READ), and no lock outlives it.
    The caller's transaction is untouched, so a snapshot it already took
    cannot hide a rate, and a lock it would have held cannot block an approval.
    Returns (sheets, sheet_rows, calendar_rows)."""
    db = _fresh_db()
    try:
        sheets = db.sql(_SHEETS_SQL, as_dict=True)
        if not sheets:
            return [], [], []
        rows = db.sql(_SHEET_ROWS_SQL, as_dict=True)
        calendar = db.sql(_CALENDAR_SQL, as_dict=True)
        return sheets, rows, calendar
    finally:
        db.close()


def _get(obj, field):
    return obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)


def _row_key(row):
    """(from, to, fiscal year, fiscal period, rate type, rate set) of a
    published row ([to, from, fy, fp, rate_type, rate, document, rate_set])."""
    return (row[1], row[0], int(row[2]), int(row[3]), row[4], row[7])


def sheet_published_rows(sheets, sheet_rows, calendar_rows, actuals_code):
    """epm_staging.group_exchange_rates rows for the APPROVED sheets
    (docstatus 1; anything else is ignored), through
    rate_sheet_model.published_rows: Statutory under ``actuals_code``,
    Constant under CONSTANT, ``document`` the sheet name.

    A sheet named in another approved sheet's ``supersedes`` gives way to it
    (#312-3). Raises ValueError, naming the sheets, when two approved sheets
    still publish one key, or when the rules refuse an approved sheet (its
    period left the calendar): never a silent pick."""
    approved = [s for s in sheets if int(_get(s, "docstatus") or 0) == 1]
    superseded = {_get(s, "supersedes") for s in approved if _get(s, "supersedes")}
    by_parent = {}
    for r in sheet_rows:
        by_parent.setdefault(_get(r, "parent"), []).append(
            {f: _get(r, f) for f in ("from_currency", "to_currency", "quoted_per", "average_quote",
                                     "closing_quote", "rate")})
    calendar = [{f: _get(c, f) for f in ("fiscal_year", "period", "type", "start_date", "end_date")}
                for c in calendar_rows]
    out, owner = [], {}
    for s in sorted(approved, key=lambda s: _get(s, "name")):
        name = _get(s, "name")
        if name in superseded:
            continue
        sheet = {"name": name, "rate_basis": _get(s, "rate_basis"), "fiscal_year": _get(s, "fiscal_year"),
                 "fiscal_period": (None if _get(s, "rate_basis") == rate_sheet_model.CONSTANT
                                   else _get(s, "fiscal_period")),
                 "rows": by_parent.get(name, [])}
        try:
            rows = rate_sheet_model.published_rows(sheet, calendar, actuals_code, CONSTANT_RATE_SET)
        except ValueError as e:
            raise ValueError(f"Rate Sheet {name} is approved but cannot be published: {e}") from e
        for row in rows:
            key = _row_key(row)
            if key in owner and owner[key] != name:
                raise ValueError(f"Rate Sheets {owner[key]} and {name} are both approved for {key}: "
                                 "exactly one may be. Supersede one with the other (#312-3).")
            owner[key] = name
        out.extend(rows)
    return out


def publish_rates(force=False):
    """Publish every approved rate as its TRUE rate, all at once and one publish
    at a time (review of #174: TRUNCATE + batched INSERTs could interleave, so
    two publishes duplicated every key and a reader mid-way saw part of the set).

    * Serialised by a MariaDB named lock (GET_LOCK, per site): the approval's
      after-commit publish, a second approval's, and the reconcile after a
      migrate take turns.
    * The rows are read AFTER the lock is held, on a connection of their own
      (``_read_approved``): a plain consistent read whose snapshot starts
      there, so each publish sees every rate committed before it. It takes no
      row or gap lock, and leaves none behind in the caller's transaction,
      whether that is an approval's after-commit hook, reconcile_all's job or
      after_migrate: an approval or a cancel during a reconcile never waits on
      it (joint re-review of #174: a LOCK IN SHARE MODE scan there held its
      next-key locks until the job committed). The only lock is the named
      one, held for the length of the publish.
    * The full set is built in a shadow table and swapped in with EXCHANGE
      TABLES (epm_staging is an Atomic database): a reader sees the old set or
      the new one, never part of either.

    Skipped during install, import, migrate and patches unless ``force`` (the
    reconcile), like every write-through. Best-effort: a failure is logged and
    recorded for check_health, never raised into the save it follows.
    Returns the row count published, or None."""
    flags = frappe.flags
    if flags.in_install or flags.in_import:
        return None
    if not force and (flags.in_migrate or flags.in_patch):
        return None
    from konsol.clickhouse import _record_sync_failure, _stamp_watermark, execute

    lock = f"konsol_group_rates_publish:{frappe.conf.db_name}"
    got = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (lock, PUBLISH_LOCK_WAIT))
    if not got or got[0][0] != 1:
        message = f"GET_LOCK('{lock}') returned {got!r} after {PUBLISH_LOCK_WAIT}s: another publish holds it"
        _record_sync_failure(PUBLISH_TABLE, "publish_lock_timeout", message)
        frappe.logger().error(f"group exchange rates NOT published: {message}")
        return None
    shadow = PUBLISH_TABLE + "__publishing"
    try:
        sheets, sheet_rows, calendar = _read_approved()
        # The actuals set is read only when a Statutory sheet needs it: a site
        # with only Constant sheets publishes whatever its Rate Set flags.
        actuals = (actuals_rate_set() if any(_get(s, "rate_basis") == rate_sheet_model.STATUTORY
                                             for s in sheets) else None)
        rows = sheet_published_rows(sheets, sheet_rows, calendar, actuals)
        execute(f"DROP TABLE IF EXISTS {shadow}")
        execute(f"CREATE TABLE {shadow} AS {PUBLISH_TABLE}")
        columns = ", ".join(PUBLISH_COLUMNS)
        for i in range(0, len(rows), 1000):
            execute(f"INSERT INTO {shadow} ({columns}) VALUES "
                    + ", ".join("(" + ", ".join(_ch_literal(v) for v in row) + ")" for row in rows[i:i + 1000]))
        execute(f"EXCHANGE TABLES {PUBLISH_TABLE} AND {shadow}")
        execute(f"DROP TABLE IF EXISTS {shadow}")
        modified = [m for m in (_get(x, "modified") for x in sheets) if m]
        _stamp_watermark(PUBLISH_TABLE, len(rows),
                         max(modified).strftime("%Y-%m-%d %H:%M:%S") if modified else None)
        return len(rows)
    except Exception as e:  # noqa: BLE001 — never break the save this follows
        _record_sync_failure(PUBLISH_TABLE, "publish_failed", str(e))
        frappe.logger().exception(f"group exchange rates NOT published to {PUBLISH_TABLE}")
        return None
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock,))


# -- the close gate -------------------------------------------------------------------

_SHEET_ROWS_OF_PERIOD_SQL = (
    "SELECT s.name, s.supersedes, r.from_currency, r.to_currency, r.quoted_per, r.average_quote, "
    "r.closing_quote "
    "FROM `tabRate Sheet Row` r JOIN `tabRate Sheet` s ON s.name = r.parent "
    "WHERE s.docstatus = %s AND s.rate_basis = 'Statutory' AND s.fiscal_year = %s AND s.fiscal_period = %s "
    "AND r.parenttype = 'Rate Sheet' AND r.parentfield = 'rates' ORDER BY s.name, r.idx")


def _statutory_rows(fiscal_year, fiscal_period, docstatus=1, share=""):
    """(from, to, quoted_per, average_quote, closing_quote) of every row of the
    period's Statutory sheets in ``docstatus``, the one read the close gate
    (_approved_keys) and approved_quotes share. An approved sheet named in
    another approved sheet's ``supersedes`` gives way to it (#312-3), as in the
    publish (sheet_published_rows)."""
    rows = frappe.db.sql(_SHEET_ROWS_OF_PERIOD_SQL + share, (docstatus, int(fiscal_year), int(fiscal_period)))
    superseded = {sup for _n, sup, *_ in rows if sup} if docstatus == 1 else set()
    return [tuple(r[2:]) for r in rows if r[0] not in superseded]


def _wants(quote):
    return bool(quote) and float(quote) > 0


def approved_quotes(fiscal_year, fiscal_period, rate_type, lock=False):
    """``{(from, to): true_rate}`` of the period's approved ACTUALS rates for
    one rate type (konsol#592 T19): the one reader every consumer of a period's
    rate goes through (deals, charts, the close grid).

    From the approved Statutory Rate Sheet of the period: the row's Average or
    Closing quote over its Quoted Per, so the rate is the same true rate the
    publish writes. A row without the asked quote is left out, as the gate
    counts it.

    Plain read by default; ``lock=True`` reads LOCK IN SHARE MODE, as
    _approved_keys does. A Rate Type other than Closing or Average raises."""
    if rate_type not in RATE_TYPES:
        raise ValueError(f"Rate type must be one of {', '.join(RATE_TYPES)}, not {rate_type!r}.")
    share = " LOCK IN SHARE MODE" if lock else ""
    out = {}
    for f, t, per, average, closing in _statutory_rows(fiscal_year, fiscal_period, 1, share):
        quote = average if rate_type == "Average" else closing
        if _wants(quote):
            out[(f, t)] = true_rate(quote, per)
    return out


def draft_pairs(fiscal_year, fiscal_period, rate_type):
    """The ``(from, to)`` pairs the period has a DRAFT actuals rate for
    (konsol#592 T19), so the close grid can say "Awaiting approval": a draft
    Statutory sheet row carrying the quote."""
    if rate_type not in RATE_TYPES:
        raise ValueError(f"Rate type must be one of {', '.join(RATE_TYPES)}, not {rate_type!r}.")
    out = set()
    for f, t, _per, average, closing in _statutory_rows(fiscal_year, fiscal_period, 0):
        if _wants(average if rate_type == "Average" else closing):
            out.add((f, t))
    return out


def _approved_keys(fiscal_year, fiscal_period, lock=False, rate_set=None):
    """The (from, to, rate_type) of every approved rate for the period in
    ``rate_set`` (the set flagged Is Actuals when not given, konsol#348; a
    budget set's rates do not satisfy the actuals' close gate).

    From the approved Statutory Rate Sheet of the period (konsol#592 T11):
    one sheet, both quotes on a row, so a row with an Average quote covers
    Average and a row with a Closing quote covers Closing. A sheet is an
    actuals rate: it counts only for the actuals set, so any other set has
    no approved key, and a Constant sheet never counts (it is a year's
    planning rate, not the period's).

    Plain read by default: this is also called on every read of the close
    app's My work screen (close.mywork_api), where LOCK IN SHARE MODE
    would take share locks on rate rows and can block rate saves for the
    request's duration.

    ``lock=True`` reads LOCK IN SHARE MODE instead: a plain read
    (frappe.get_all) can return this transaction's REPEATABLE READ snapshot,
    which a rate cancelled and committed while a close waited on the year
    lock would still show as approved (PR #191 re-review finding 4; mirrors
    period_status.period_row and fiscal_calendar.periods_in_use(lock=True)).
    Only the close gate (assert_rates_complete) passes it."""
    actuals, _problem = find_actuals_rate_set()
    rate_set = actuals_rate_set() if rate_set is None else rate_set
    share = " LOCK IN SHARE MODE" if lock else ""
    keys = set()
    if rate_set == actuals:
        for f, t, _per, average, closing in _statutory_rows(fiscal_year, fiscal_period, 1, share):
            if _wants(average):
                keys.add((f, t, "Average"))
            if _wants(closing):
                keys.add((f, t, "Closing"))
    return keys


def missing_rates(fiscal_year, fiscal_period, pairs=None, lock=False, rate_set=None):
    pairs = required_pairs(fiscal_year, fiscal_period) if pairs is None else pairs
    have = _approved_keys(fiscal_year, fiscal_period, lock=lock, rate_set=rate_set)
    return sorted((f, t, rt) for f, t in pairs for rt in RATE_TYPES if (f, t, rt) not in have)


def rate_gate(fiscal_year, fiscal_period, lock=False):
    """What the close gate says: (missing keys, None, blockers), or (None, why
    the warehouse can't answer, []). ``blockers`` names each group the period
    translates into that has no reporting currency, then each entity it
    translates that has no functional currency (konsol#311). The close app's My work
    screen shows the same answer.

    ``lock`` is opt-in and passed through to ``_approved_keys``: plain by
    default, which is how close.mywork_api calls this on every read of the
    close app's My work screen. Only assert_rates_complete passes ``lock=True``,
    to see a rate committed while the close waited on the year lock (PR #191
    re-review finding 4) — a share lock there is fine, since a close is rare
    and already holds the year lock.

    Fails closed. Only a warehouse that has never built a trial balance owes
    nothing: the read fails with UNKNOWN_TABLE or UNKNOWN_DATABASE AND
    ``EXISTS TABLE epm_gold.gold_trial_balance`` is false. Any other failure,
    a missing gold_entity_ownership beside a built trial balance included,
    means the rates cannot be checked. So does a site with no Rate Set
    flagged Is Actuals (konsol#348): the error names the flag."""
    rate_set, problem = find_actuals_rate_set()
    if problem:
        return None, problem, []
    try:
        pairs, groups, entities = translation_gaps(fiscal_year, fiscal_period)
    except Exception as e:  # noqa: BLE001 — any failure to read means "can't verify"
        names = sorted(ch_error_names(e))
        if _not_built(e):
            try:
                if not ledgers_built():
                    return [], None, []
            except Exception:  # noqa: BLE001 — can't tell, so fail closed
                pass
        return None, type(e).__name__ + (f" {', '.join(names)}" if names else ""), []
    return missing_rates(fiscal_year, fiscal_period, pairs, lock=lock, rate_set=rate_set), None, (
        [f"Consolidation Group {g} has no reporting currency" for g in groups]
        + [f"Entity {e} has no functional currency" for e in entities])


def assert_rates_complete(fiscal_year, fiscal_period):
    """Refuse to close a period while a translated currency lacks an approved
    rate, a group it translates into has no reporting currency, or an entity
    it translates has no functional currency.

    The pairs come from the last build (``translation_gaps``): the gate checks
    what that build translates, not ownership edited since."""
    _set, problem = find_actuals_rate_set()
    if problem:
        frappe.throw(f"Cannot close fiscal period {fiscal_period} of FY{fiscal_year}: {problem}",
                     frappe.ValidationError)
    missing, error, blockers = rate_gate(fiscal_year, fiscal_period, lock=True)
    if error:
        frappe.throw(
            f"Cannot close fiscal period {fiscal_period} of FY{fiscal_year}: the warehouse could not "
            f"say which currencies its ledgers translate ({error}), so its group exchange "
            "rates cannot be checked.", frappe.ValidationError)
    problems = list(blockers)
    if missing:
        problems.append("no approved group exchange rate for "
                        + ", ".join(f"{f} → {t} {rt}" for f, t, rt in missing))
    if problems:
        frappe.throw(
            f"Cannot close fiscal period {fiscal_period} of FY{fiscal_year}: " + "; ".join(problems)
            + ". Set each entity's Functional Currency and each group's Reporting Currency, pre-fill the rates from the ERP (or enter them) "
            "and approve each.", frappe.ValidationError)


# -- the pre-fill -------------------------------------------------------------------------

SHEET_DOCTYPE = "Rate Sheet"
#: Set around the writes of each path, and by nothing else: Rate Sheet's
#: _guard_provenance refuses Source, ERP quotes and Copied From without them.
#: Pinned equal to rate_sheet.py's constants by test_rate_sheet_prefill (this
#: module cannot import the controller).
PREFILL_FLAG = "konsol_prefilling_rate_sheet"
DUPLICATE_FLAG = "konsol_duplicating_rate_sheet"


@frappe.whitelist(methods=["POST"])
def prefill_from_erp(fiscal_year, fiscal_period):
    """Propose a period's Statutory rates from what the ERP feed quotes
    (konsol#592 T12; konsol#103).

    "D365 quotes 0.9378 for March: accept?" Creates the period's DRAFT
    Statutory Rate Sheet if there is none, or fills the MISSING rows of the
    draft there is; a row already on the draft, whoever wrote it, is left
    alone. An approved sheet is never touched: a correction supersedes it, so
    the pre-fill refuses by name. A sheet in Pending Approval is refused too,
    so it does not change under its approver.

    A pair needs both quotes to make a row: a Closing quote is the one in
    force at the period's end, an Average one the quote at its start. A pair
    missing either is listed under "no_quote" and skipped. Each row carries
    Source "ERP pre-fill" and its ERP quotes, one Quoted Per for both. Nothing
    is submitted. Group Accountant, Close Lead and System Manager only (PREFILL_ROLES).

    A quote the magnitude guard refuses, and a row the sheet refuses (a move
    over the declared Rate Move threshold needs a person's reason), are
    reported under "refused", not inserted, so the refusal pops no dialog: enter
    that row by hand. A quote past the declared Magnitude Warning Factor
    (konsol#179) is still drafted and listed under "warned": the person who
    saves or approves the sheet gives the reason. So are ERP sources that
    disagree.

    The fiscal year is locked first (LOCK IN SHARE MODE, as every sheet
    write does), then the period's sheets are read FOR UPDATE, so two clicks
    at once cannot both create the sheet. Returns {"sheet", "sheet_created",
    "created" (the pairs added), "existing", "no_quote", "refused", "warned"}."""
    frappe.only_for(PREFILL_ROLES)
    fy, fp = int(fiscal_year), int(fiscal_period)
    frappe.db.sql("SELECT name, status FROM `tabEPM Fiscal Year` WHERE fiscal_year=%s LOCK IN SHARE MODE", (fy,))
    sheets = frappe.db.sql(
        "SELECT name, docstatus, status FROM `tabRate Sheet` WHERE rate_basis = 'Statutory' "
        "AND fiscal_year = %s AND fiscal_period = %s AND docstatus < 2 ORDER BY docstatus, name FOR UPDATE",
        (fy, fp))
    drafts = [r for r in sheets if int(r[1] or 0) == 0]
    approved = [r[0] for r in sheets if int(r[1] or 0) == 1]
    if not drafts and approved:
        frappe.throw(
            f"{approved[0]} is the approved Statutory sheet for FY{fy} P{fp}, and the pre-fill does not touch "
            "an approved sheet. To correct a rate, supersede it (a new draft that names it in Supersedes, with "
            "a Reason for Change).", frappe.ValidationError)
    doc, created_sheet = None, False
    if drafts:
        name, _, status = drafts[0]
        if status not in (None, "", "Draft"):
            frappe.throw(f"{name} is {status}, so its approver may be reading it. Reject it back to Draft "
                         "before pre-filling it.", frappe.ValidationError)
        doc = frappe.get_doc(SHEET_DOCTYPE, name)
    have = {(r.get("from_currency"), r.get("to_currency")) for r in (doc.get("rates") or [])} if doc else set()
    pairs = required_pairs(fy, fp) or tree_pairs(fy, fp)
    # A Closing rate is struck at the period end; an Average spans the period,
    # and the ERP keys it on the period's start.
    rows_by_type = {"Closing": erp_quote_rows(period_end(fy, fp)),
                    "Average": erp_quote_rows(period_start(fy, fp))}
    refs = usd_references({c for pair in pairs for c in pair})
    factor = magnitude_warning_factor()
    out = {"sheet": doc.name if doc else None, "sheet_created": False, "created": [], "existing": [],
           "no_quote": [], "refused": [], "warned": []}
    if doc is None:
        created_sheet = True
        doc = frappe.get_doc({"doctype": SHEET_DOCTYPE, "rate_basis": "Statutory", "fiscal_year": fy,
                              "fiscal_period": fp, "rates": []})
        # validate() excludes its own name from the one-sheet-per-key read, and
        # the move check reads the sheets before it: give it the name autoname
        # will, then take it away for the insert.
        doc.name = f"RS-S-{fy}-{fp:02d}"
    frappe.flags[PREFILL_FLAG] = True
    try:
        for f, t in sorted(pairs):
            label = f"{f} → {t}"
            if (f, t) in have:
                out["existing"].append(label)
                continue
            quotes = {rt: resolve_quotes(rows_by_type[rt], f, t, rt) for rt in RATE_TYPES}
            missing = [rt for rt in RATE_TYPES if not quotes[rt]]
            if missing:
                out["no_quote"].extend(f"{label} {rt}" for rt in missing)
                continue
            rates = {rt: quotes[rt][0]["rate"] for rt in RATE_TYPES}
            problems = [f"{label} {rt}: {p}" for rt in RATE_TYPES
                        if (p := magnitude_problem(f, t, rates[rt], refs))]
            if problems:
                # The #138 guard refusing an ERP quote is the guard working.
                out["refused"].extend(problems)
                continue
            # One Quoted Per per row: the larger of the two keeps both quotes' digits.
            per = max(choose_quoted_per(rates[rt])[1] for rt in RATE_TYPES)
            average, closing = (round(rates[rt] * per, 9) for rt in ("Average", "Closing"))
            doc.append("rates", {
                "from_currency": f, "to_currency": t, "quoted_per": str(per), "average_quote": average,
                "closing_quote": closing, "source": PREFILL_SOURCE, "erp_average_quote": average,
                "erp_closing_quote": closing})
            try:
                doc.validate()
            except frappe.ValidationError as e:
                # frappe.throw logged the message before raising; the summary
                # reports it, so no dialog pops for it.
                doc.get("rates").pop()
                frappe.clear_last_message()
                out["refused"].append(f"{label}: {e}")
                continue
            out["created"].append(label)
            for rt, quote in (("Average", average), ("Closing", closing)):
                warning = magnitude_warning(f, t, quote, per, factor, refs)
                if warning:
                    out["warned"].append(f"{label} {rt}: {warning}")
                note = describe_quotes(quotes[rt], fy, fp, f, t)
                if note.startswith("ERP SOURCES DISAGREE"):
                    out["warned"].append(f"{label} {rt}: {note}")
        if out["created"]:
            note = (f"Pre-filled from the ERP feed for FY{fy} P{fp}: Closing quotes at the period end "
                    f"({period_end(fy, fp)}), Average quotes at its start ({period_start(fy, fp)}). "
                    f"{len(out['created'])} pair(s) proposed; a person reviews and approves.")
            before = (doc.get("source_note") or "").strip()
            doc.source_note = f"{before}\n{note}" if before else note
            if created_sheet:
                doc.name = None
                doc.insert()
            else:
                doc.save()
            out["sheet"], out["sheet_created"] = doc.name, created_sheet
    finally:
        frappe.flags[PREFILL_FLAG] = False
    return out


# -- duplicate the previous sheet (konsol#592 T13, replacing konsol#584's copy) -------------

@frappe.whitelist(methods=["POST"])
def duplicate_previous(rate_basis, fiscal_year, fiscal_period=None):
    """Propose a DRAFT Rate Sheet for a key from the previous approved sheet of
    the basis (konsol#592 T13; konsol#584, Deepak Pai 22 Nov 2026).

    For sites whose rates do not move every month (pegged, stable or planning
    rates, or no ERP feed). Statutory takes the latest approved sheet of an
    earlier period (the previous Regular or Opening period with one: a Closing
    period has no sheet, and a period with none is passed over); Constant takes
    the latest approved sheet of an earlier year. ``rate_sheet_model.plan_duplicate``
    decides the draft: each row copies its quotes and Quoted Per, with Source
    "Duplicated"; the sheet's Copied From names its source and its note says
    so. Nothing is submitted: a person approves the draft for its own key,
    under the same rules as any sheet. Rejected: a silent carry-forward in
    the warehouse.

    A target that already has a draft or an approved sheet is refused (a
    correction supersedes), as is a Statutory period that is not Open and a
    Constant year that is closed. The request is refused by name, and nothing
    is written, for any of these. Group Accountant, Close Lead and System
    Manager only (PREFILL_ROLES).

    The fiscal year is locked first (LOCK IN SHARE MODE), then the target's
    keys are read FOR UPDATE, so two clicks at once cannot both draft it.
    Returns {"name", "copied_from"}."""
    from konsol.period_status import assert_year_not_closed

    frappe.only_for(PREFILL_ROLES)
    basis = rate_basis
    if basis not in (rate_sheet_model.STATUTORY, rate_sheet_model.CONSTANT):
        frappe.throw(f"Rate Basis must be Statutory or Constant, not {basis!r}.", frappe.ValidationError)
    statutory = basis == rate_sheet_model.STATUTORY
    if statutory and fiscal_period in (None, ""):
        frappe.throw("A Statutory sheet needs a Fiscal Period.", frappe.MandatoryError)
    fy = int(fiscal_year)
    fp = None if fiscal_period in (None, "") else int(fiscal_period)
    if statutory:
        assert_open(fy, fp, action="duplicate rates into it")
    elif fp is not None:
        frappe.throw("A Constant sheet covers the whole year and has no Fiscal Period.", frappe.ValidationError)
    else:
        assert_year_not_closed(fy, action="duplicate rates into a closed year")
    frappe.db.sql("SELECT name, status FROM `tabEPM Fiscal Year` WHERE fiscal_year=%s LOCK IN SHARE MODE", (fy,))
    existing = [(b, y, p if b == rate_sheet_model.STATUTORY else None) for b, y, p in frappe.db.sql(
        "SELECT rate_basis, fiscal_year, fiscal_period FROM `tabRate Sheet` "
        "WHERE rate_basis = %s AND fiscal_year = %s AND docstatus < 2 FOR UPDATE", (basis, fy))]
    if statutory:
        found = frappe.db.sql(
            "SELECT name, fiscal_year, fiscal_period FROM `tabRate Sheet` WHERE rate_basis = %s AND docstatus = 1 "
            "AND (fiscal_year < %s OR (fiscal_year = %s AND fiscal_period < %s)) "
            "ORDER BY fiscal_year DESC, fiscal_period DESC LIMIT 1", (basis, fy, fy, fp))
    else:
        found = frappe.db.sql(
            "SELECT name, fiscal_year, fiscal_period FROM `tabRate Sheet` WHERE rate_basis = %s AND docstatus = 1 "
            "AND fiscal_year < %s ORDER BY fiscal_year DESC LIMIT 1", (basis, fy))
    key = rate_sheet_model.key_label(basis, fy, fp)
    if not found:
        frappe.throw(f"There is no approved {basis} sheet before {key} to duplicate. Enter the first one, "
                     "or pre-fill it from the ERP.", frappe.ValidationError)
    name, source_year, source_period = found[0]
    rows = frappe.get_all(
        "Rate Sheet Row", filters={"parent": name, "parenttype": SHEET_DOCTYPE, "parentfield": "rates"},
        fields=["from_currency", "to_currency", "quoted_per", "average_quote", "closing_quote", "rate"],
        order_by="idx asc", limit_page_length=0)
    previous = {"name": name, "rate_basis": basis, "fiscal_year": source_year,
                "fiscal_period": source_period if statutory else None, "docstatus": 1,
                "rows": [dict(r) for r in rows]}
    plan, why = rate_sheet_model.plan_duplicate(previous, (basis, fy, fp), existing)
    if why:
        frappe.throw(why, frappe.ValidationError)
    frappe.flags[DUPLICATE_FLAG] = True
    try:
        doc = frappe.get_doc({
            "doctype": SHEET_DOCTYPE, "rate_basis": basis, "fiscal_year": plan["fiscal_year"],
            "fiscal_period": plan["fiscal_period"], "rates": plan["rows"], "copied_from": plan["copied_from"],
            "source_note": plan["source_note"]})
        doc.insert()
    finally:
        frappe.flags[DUPLICATE_FLAG] = False
    return {"name": doc.name, "copied_from": plan["copied_from"]}
