"""Governed group exchange rates (konsol#103): the rules around Group Exchange Rate.

Decided by the user on 13 Sep 2026. Group finance owns one rate table in konsol.
The ERP feed is demoted to a proposal: ``prefill_from_erp`` reads what the ERP
quotes for a period and creates DRAFT rates, and a person approves (submits)
them. Rates lock when their period closes, and a period cannot close while a
currency its ledgers translate lacks an approved Closing or Average rate into
the group reporting currency (``assert_rates_complete``, called from Period
Status).

``adopt_erp_rates`` is the one-time upgrade: it records, as approved governed
rows authored by the system and labelled "Adoption", the rates the warehouse
already translated each period at, so the translation that reads only
governed rates (konsolidat#93) gives the same figures on its first build.

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
* **Soft:** a rate that moves more than 50% from the previous approved rate for
  its key, or from the ERP quote it was proposed from, needs a Reason for
  Change. Real moves that size happen (ARS fell 55% in Dec 2023), so a reason
  lets it through.
"""
import decimal
import json
import math
import re

import frappe

from konsol.fx_reference import REFERENCE_CURRENCY, usd_reference  # noqa: F401 — the one rule
from konsol.period_status import PeriodNotDeclared, period_dates  # noqa: F401 — surfaced for callers

DOCTYPE = "Group Exchange Rate"
RATE_TYPES = ("Closing", "Average")
PREFILL_ROLES = ("EPM Analyst", "EPM Admin", "System Manager")
PREFILL_SOURCE = "ERP pre-fill"
ADOPTION_SOURCE = "Adoption"

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
#: A move larger than this fraction needs a Reason for Change.
MOVE_NEEDS_REASON = 0.5
#: "Quoted per" (decided 13 Sep 2026: one source of truth, published by konsol).
#: A quote is units of the group currency per this many units of the
#: from-currency, so its direction never flips: 0.6607 USD per 100 JPY. The
#: TRUE rate is quote / quoted_per; konsol computes it once, when it publishes,
#: and the warehouse never scales or inverts a rate.
QUOTED_PER = (1, 10, 100, 1000, 10000)
#: MariaDB keeps 9 decimal places. A quote must keep at least this many
#: significant digits there (0.0001 or more); a bigger Quoted per fixes it.
MIN_SIGNIFICANT_DIGITS = 6
#: The pre-fill and the adoption aim higher: the smallest Quoted per that puts
#: the quote at 0.1 or more (9 significant digits). JPY -> USD is per 100.
PREFERRED_MIN_QUOTE = 0.1


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


def published_rows(docs):
    """epm_staging.group_exchange_rates rows for approved rates: (to, from,
    fiscal year, fiscal period, rate type, TRUE rate, document). The one place
    a quote becomes a rate; Float64 in the warehouse keeps what MariaDB's 9
    decimal places can't."""
    return [[d.to_currency, d.from_currency, int(d.fiscal_year), int(d.fiscal_period), d.rate_type,
             true_rate(d.quote, d.quoted_per), d.name] for d in docs]


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


def move_problem(rate, previous=None, erp_rate=None, unit=""):
    """None, or why ``rate`` needs a Reason for Change: it moves more than 50%
    from ``previous`` ((rate, label) of the last approved rate for its key) or
    from ``erp_rate`` (the ERP quote it was proposed from). Rates are "to per
    1 from", whatever unit they are quoted per; ``unit`` labels them
    ("USD per JPY")."""
    rate = float(rate)
    unit = f" {unit}" if unit else ""
    moves = []
    for ref, what in ((previous[0], f"the previous approved rate {previous[1]}") if previous else (None, None),
                      (erp_rate, "the ERP quote")):
        if ref and float(ref) > 0:
            change = rate / float(ref) - 1.0
            if abs(change) > MOVE_NEEDS_REASON:
                moves.append(f"{change:+.0%} from {what} ({float(ref):.9g}{unit})")
    if not moves:
        return None
    return (f"This rate ({rate:.9g}{unit}) moves " + " and ".join(moves) + ". A move over 50% can be "
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


def check_references(actions, refs):
    """(actions, unset): the currencies the adoption would ENTER with no
    magnitude reference (``unset``, sorted), and the actions with every skip
    of a currency that has none saying so. Pure.

    Only an "adopt" action enters a rate, so only its two currencies need a
    reference (joint re-review of #174): a currency translated at the 1.0
    parity fallback, at two rates, or already governed is skipped whatever
    its reference, and must not stop the upgrade."""
    missing = {c for c, v in refs.items() if v is None}
    unset = sorted({c for a in actions if a[0] == "adopt" for c in a[1][:2]} & missing)
    out = []
    for action in actions:
        lacking = [c for c in action[1][:2] if c in missing]
        if action[0] == "skip" and lacking:
            action = ("skip", action[1], f"{action[2]}; no magnitude reference for {' and '.join(lacking)} either")
        out.append(action)
    return out, unset


def no_reference_message(unset, site="<site>"):
    """What to do about currencies the adoption would enter with no reference."""
    return (f"konsol#103 adoption: no magnitude reference (ISO Currency usd_log10) for {', '.join(unset)}, "
            "which the adoption would enter; every such rate would be refused. "
            + " ".join(f"Create or edit ISO Currency {c}, set USD Reference (log10)." for c in unset)
            + " Then rerun `bench migrate` (or, once upgraded: " + RECOVERY_COMMAND.format(site=site) + ").")


def plan_adoption(used, governed, quote_rows_by_period, today):
    """The one-time adoption, as a list of actions. Pure.

    ``used``: (from, to, fiscal_year, fiscal_period, [closing rates], [average
    rates]) as gold_consolidated_trial_balance translated them. ``governed``:
    keys (from, to, fy, fp, rate_type) that already have an approved rate.
    Returns [("adopt", key, rate, erp_rate, note) | ("skip", key, reason)],
    true rates "to per 1 from" (``choose_quoted_per`` picks the unit to enter
    them per).
    """
    actions = []
    for f, t, fy, fp, closing, average in used:
        fy, fp = int(fy), int(fp)
        for rate_type, rates in (("Closing", closing), ("Average", average)):
            key = (f, t, fy, fp, rate_type)
            distinct = sorted({_sig(r) for r in rates})
            if key in governed:
                actions.append(("skip", key, "already has an approved group rate"))
            elif len(distinct) != 1:
                actions.append(("skip", key, f"translated at more than one rate {distinct}; needs a person"))
            elif distinct[0] == 1.0:
                actions.append(("skip", key, "translated at the 1.0 parity fallback (no ERP quote); "
                                             "needs a person"))
            else:
                rate = distinct[0]
                quotes = resolve_quotes(quote_rows_by_period.get((fy, fp), []), f, t, rate_type)
                match = next((q for q in quotes if abs(q["rate"] - rate) <= 1e-9 * rate), None)
                origin = (f"the ERP feed's {match['source']} {match['erp_type']} quote, valid from "
                          f"{match['valid_from']} ({match['how']})" if match else
                          "a rate that matches no current ERP quote")
                note = (f"Adopted at the konsol#103 upgrade on {today}: the {rate_type} rate "
                        f"FY{fy} P{fp} was already translated at, {origin}. Authored by the system, "
                        "not reviewed by a person. Replace it (cancel and amend, while the period is "
                        "open) if group finance sets another.")
                actions.append(("adopt", key, rate, match["rate"] if match else None, note))
    return actions


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


def translation_needs(fiscal_year, fiscal_period):
    """(pairs, groups without a currency) for exactly the rows
    gold_consolidated_trial_balance translates in the period: an entity with
    ledger rows, into each group gold_entity_ownership places it under for the
    period, with the translation's own filter (method not 'equity' or 'none',
    a complete chain, so nothing outside the ownership window). An equity
    associate's currency owes no rate. A group node with no reporting
    currency cannot be translated into at all; the warehouse guard refuses
    it, so the gate names it.

    Read from the last build: ownership changed since then is seen at the next."""
    rows = _ch_rows(
        "SELECT DISTINCT ec.accounting_currency, g.reporting_currency, eo.consolidation_group "
        "FROM (SELECT DISTINCT data_area_id FROM epm_gold.gold_trial_balance "
        "      WHERE fiscal_year = {fy:UInt16} AND fiscal_period = {fp:UInt16}) AS tb "
        "INNER JOIN (SELECT data_area_id, accounting_currency FROM epm_silver.silver_entity_currencies "
        "            WHERE accounting_currency != '') AS ec ON ec.data_area_id = tb.data_area_id "
        "INNER JOIN (SELECT consolidation_group, data_area_id FROM epm_gold.gold_entity_ownership "
        f"            WHERE fiscal_year = {{fy:UInt16}} AND fiscal_period = {{fp:UInt16}} AND {_TRANSLATED}) AS eo "
        "    ON eo.data_area_id = tb.data_area_id "
        f"LEFT JOIN {_GROUP_NODES} AS g ON g.consolidation_group = eo.consolidation_group "
        "WHERE ec.accounting_currency != g.reporting_currency",
        {"fy": int(fiscal_year), "fp": int(fiscal_period)},
    )
    return split_needs(rows)


def split_needs(rows):
    """(from, to, group) rows -> ({(from, to)}, [groups with no currency]). Pure."""
    return ({(f, t) for f, t, _ in rows if t}, sorted({g for _, t, g in rows if not t}))


def required_pairs(fiscal_year, fiscal_period):
    """The (entity currency, group reporting currency) pairs the period's
    translation needs (``translation_needs``, without the blockers)."""
    return translation_needs(fiscal_year, fiscal_period)[0]


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
                   "document")
#: Seconds a publish waits for another one to finish before it gives up and
#: logs (the next approval, or the reconcile after a migrate, republishes).
PUBLISH_LOCK_WAIT = 120


def _ch_literal(value):
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


_APPROVED_SQL = ("SELECT name, to_currency, from_currency, fiscal_year, fiscal_period, rate_type, quote, "
                 "quoted_per, modified FROM `tabGroup Exchange Rate` WHERE docstatus = 1 ORDER BY name")


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
    """The approved rates as committed right now. Read on a connection of its
    own and closed at once: the snapshot starts at this read (nothing ran on
    that connection before it), and no lock outlives it. The caller's
    transaction is untouched, so a snapshot it already took cannot hide a
    rate, and a lock it would have held cannot block an approval."""
    db = _fresh_db()
    try:
        return db.sql(_APPROVED_SQL, as_dict=True)
    finally:
        db.close()


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
        docs = _read_approved()
        rows = published_rows(docs)
        execute(f"DROP TABLE IF EXISTS {shadow}")
        execute(f"CREATE TABLE {shadow} AS {PUBLISH_TABLE}")
        columns = ", ".join(PUBLISH_COLUMNS)
        for i in range(0, len(rows), 1000):
            execute(f"INSERT INTO {shadow} ({columns}) VALUES "
                    + ", ".join("(" + ", ".join(_ch_literal(v) for v in row) + ")" for row in rows[i:i + 1000]))
        execute(f"EXCHANGE TABLES {PUBLISH_TABLE} AND {shadow}")
        execute(f"DROP TABLE IF EXISTS {shadow}")
        modified = [d.modified for d in docs if d.modified]
        _stamp_watermark(PUBLISH_TABLE, len(rows),
                         max(modified).strftime("%Y-%m-%d %H:%M:%S") if modified else None)
        return len(rows)
    except Exception as e:  # noqa: BLE001 — never break the save this follows
        _record_sync_failure(PUBLISH_TABLE, "publish_failed", str(e))
        frappe.logger().exception(f"group exchange rates NOT published to {PUBLISH_TABLE}")
        return None
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock,))


# -- governed rows --------------------------------------------------------------------

def previous_approved(to_currency, from_currency, rate_type, fiscal_year, fiscal_period):
    """(rate "to per 1 from", label) of the latest approved rate for the same
    key in an earlier period, or None. The last approved one, so a gap (a
    period with no rate) compares with the rate before it."""
    rows = frappe.db.sql(
        "SELECT name, quote, quoted_per, fiscal_year, fiscal_period FROM `tabGroup Exchange Rate` "
        "WHERE to_currency = %s AND from_currency = %s AND rate_type = %s AND docstatus = 1 "
        "AND (fiscal_year < %s OR (fiscal_year = %s AND fiscal_period < %s)) "
        "ORDER BY fiscal_year DESC, fiscal_period DESC LIMIT 1",
        (to_currency, from_currency, rate_type, int(fiscal_year), int(fiscal_year), int(fiscal_period)),
    )
    if not rows:
        return None
    name, quote, per, fy, fp = rows[0]
    return true_rate(quote, per), f"FY{fy} P{fp} ({name})"


# -- the close gate -------------------------------------------------------------------

def _approved_keys(fiscal_year, fiscal_period, lock=False):
    """The (from, to, rate_type) of every approved rate for the period.

    Plain read by default: this is also called on every read of the close
    app's My work screen (close.mywork_api), where LOCK IN SHARE MODE
    would take share locks on Group Exchange Rate rows and can block rate
    saves for the request's duration.

    ``lock=True`` reads LOCK IN SHARE MODE instead: a plain read
    (frappe.get_all) can return this transaction's REPEATABLE READ snapshot,
    which a rate cancelled and committed while a close waited on the year
    lock would still show as approved (PR #191 re-review finding 4; mirrors
    period_status.period_row and fiscal_calendar.periods_in_use(lock=True)).
    Only the close gate (assert_rates_complete) passes it."""
    rows = frappe.db.sql(
        "SELECT from_currency, to_currency, rate_type FROM `tabGroup Exchange Rate` "
        "WHERE docstatus = %s AND fiscal_year = %s AND fiscal_period = %s"
        + (" LOCK IN SHARE MODE" if lock else ""),
        (1, int(fiscal_year), int(fiscal_period)),
    )
    return {(f, t, rt) for f, t, rt in rows}


def missing_rates(fiscal_year, fiscal_period, pairs=None, lock=False):
    pairs = required_pairs(fiscal_year, fiscal_period) if pairs is None else pairs
    have = _approved_keys(fiscal_year, fiscal_period, lock=lock)
    return sorted((f, t, rt) for f, t in pairs for rt in RATE_TYPES if (f, t, rt) not in have)


def rate_gate(fiscal_year, fiscal_period, lock=False):
    """What the close gate says: (missing keys, None, blockers), or (None, why
    the warehouse can't answer, []). ``blockers`` names each group the period
    translates into that has no reporting currency. The close app's My work
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
    means the rates cannot be checked."""
    try:
        pairs, groups = translation_needs(fiscal_year, fiscal_period)
    except Exception as e:  # noqa: BLE001 — any failure to read means "can't verify"
        names = sorted(ch_error_names(e))
        if _not_built(e):
            try:
                if not ledgers_built():
                    return [], None, []
            except Exception:  # noqa: BLE001 — can't tell, so fail closed
                pass
        return None, type(e).__name__ + (f" {', '.join(names)}" if names else ""), []
    return missing_rates(fiscal_year, fiscal_period, pairs, lock=lock), None, [
        f"Consolidation Group {g} has no reporting currency" for g in groups]


def assert_rates_complete(fiscal_year, fiscal_period):
    """Refuse to close a period while a translated currency lacks an approved
    rate, or a group it translates into has no reporting currency.

    The pairs come from the last build (``translation_needs``): the gate checks
    what that build translates, not ownership edited since."""
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
            + ". Set each group's Reporting Currency, pre-fill the rates from the ERP (or enter them) "
            "and approve each.", frappe.ValidationError)


# -- the pre-fill -------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def prefill_from_erp(fiscal_year, fiscal_period):
    """Propose DRAFT rates for a period from what the ERP feed quotes.

    "D365 quotes 0.9378 for March: accept?" A Closing proposal is the quote in
    force at the period's end, an Average one the quote at its start. Each proposal is a draft carrying
    the quote, its source and how it was reached; nothing is submitted, so
    nothing applies itself. A key that already has a draft or an approved rate
    is left alone. Group Accountant, Close Lead and System Manager only.

    A quote the magnitude guard refuses is reported, not inserted, so the
    refusal pops no dialog. A quote that moves more than 50% needs a person's
    reason, so it is reported too: enter it by hand with the reason."""
    frappe.only_for(PREFILL_ROLES)
    fy, fp = int(fiscal_year), int(fiscal_period)
    pairs = required_pairs(fy, fp) or tree_pairs(fy, fp)
    # A Closing rate is struck at the period end; an Average spans the period,
    # and the ERP keys it on the period's start.
    rows_by_type = {"Closing": erp_quote_rows(period_end(fy, fp)),
                    "Average": erp_quote_rows(period_start(fy, fp))}
    taken = {(r.from_currency, r.to_currency, r.rate_type) for r in frappe.get_all(
        DOCTYPE, filters={"fiscal_year": fy, "fiscal_period": fp, "docstatus": ["<", 2]},
        fields=["from_currency", "to_currency", "rate_type"], limit_page_length=0)}
    refs = usd_references({c for pair in pairs for c in pair})
    out = {"created": [], "existing": [], "no_quote": [], "refused": []}
    frappe.flags.konsol_prefilling_rates = True
    try:
        for f, t in sorted(pairs):
            for rate_type in RATE_TYPES:
                label = f"{f} → {t} {rate_type}"
                if (f, t, rate_type) in taken:
                    out["existing"].append(label)
                    continue
                quotes = resolve_quotes(rows_by_type[rate_type], f, t, rate_type)
                if not quotes:
                    out["no_quote"].append(label)
                    continue
                problem = magnitude_problem(f, t, quotes[0]["rate"], refs)
                if problem:
                    # The #138 guard refusing an ERP quote is the guard working.
                    out["refused"].append(f"{label}: {problem}")
                    continue
                quote, per = choose_quoted_per(quotes[0]["rate"])
                note = describe_quotes(quotes, fy, fp, f, t)
                if per > 1:
                    note += f" Entered as {quote_label(quote, per, f, t)}, so it keeps its digits."
                doc = frappe.get_doc({
                    "doctype": DOCTYPE, "to_currency": t, "from_currency": f, "rate_type": rate_type,
                    "fiscal_year": fy, "fiscal_period": fp, "quote": quote, "quoted_per": str(per),
                    "erp_quote": quote, "source": PREFILL_SOURCE, "source_note": note,
                })
                try:
                    doc.insert()
                except frappe.ValidationError as e:
                    # frappe.throw logged the message before raising; the
                    # summary reports it, so no dialog pops for it.
                    frappe.clear_last_message()
                    out["refused"].append(f"{label}: {e}")
                    continue
                out["created"].append(doc.name)
    finally:
        frappe.flags.konsol_prefilling_rates = False
    return out


# -- the one-time adoption -------------------------------------------------------------------

def translated_rates():
    """The rates gold_consolidated_trial_balance translated each foreign
    currency at, per group reporting currency and period."""
    return _ch_rows(
        "SELECT accounting_currency, reporting_currency, fiscal_year, fiscal_period, "
        "groupUniqArray(closing_rate), groupUniqArray(average_rate) "
        "FROM epm_gold.gold_consolidated_trial_balance "
        "WHERE accounting_currency != '' AND reporting_currency != '' "
        "AND accounting_currency != reporting_currency "
        "GROUP BY accounting_currency, reporting_currency, fiscal_year, fiscal_period "
        "ORDER BY fiscal_year, fiscal_period, accounting_currency, reporting_currency")


RECOVERY_COMMAND = "bench --site {site} execute konsol.group_rates.adopt_erp_rates"


def adopt_erp_rates(dry_run=False):
    """Adopt, once, the rates each period was already translated at (konsol#103).

    Run by the upgrade patch; safe to run again (a key that has an approved
    rate is skipped), and ``dry_run`` prints the plan without writing:

        bench --site <site> execute konsol.group_rates.adopt_erp_rates --kwargs "{'dry_run': 1}"

    Every period gold_consolidated_trial_balance holds is adopted, open and
    closed: the translation that reads only governed rates must give the same
    figures on its first build. Each row is approved (submitted) by the system,
    labelled source "Adoption" with a note saying where the rate came from, and
    may be replaced by group finance while its period is open. A closed
    period's row is locked like any other. What was translated at the 1.0
    parity fallback, or at two rates, is not adopted: it is printed as needing
    a person, and the translation will name it until one is approved.

    A warehouse with nothing built (UNKNOWN_TABLE, or UNKNOWN_DATABASE before
    epm_gold exists) has nothing to adopt. Any other failure to read it, a
    ClickHouse that isn't up included, fails loudly and names the command that
    finishes the job once it is.
    """
    if frappe.session.user != "Administrator":
        frappe.throw("The rate adoption runs as the system (Administrator) only.", frappe.PermissionError)
    try:
        used = translated_rates()
        periods = sorted({(int(r[2]), int(r[3])) for r in used})
        quotes = {p: erp_quote_rows(period_start(*p)) for p in periods}
    except Exception as e:  # noqa: BLE001
        if _not_built(e):
            print("konsol#103 adoption: the warehouse has never built a consolidated trial balance "
                  f"({', '.join(sorted(ch_error_names(e)))}); nothing to adopt.")
            return {"adopted": [], "skipped": [], "refused": []}
        site = getattr(getattr(frappe, "local", None), "site", None) or "<site>"
        raise RuntimeError(
            f"konsol#103 adoption could not read the warehouse ({type(e).__name__}: {str(e)[:300]}). "
            "Nothing was adopted, and the governed translation will refuse every period it translated "
            "until this runs. Once ClickHouse is up, run: " + RECOVERY_COMMAND.format(site=site)
            + " (add --kwargs \"{'dry_run': 1}\" to preview).") from e
    governed = {(r.from_currency, r.to_currency, int(r.fiscal_year), int(r.fiscal_period), r.rate_type)
                for r in frappe.get_all(DOCTYPE, filters={"docstatus": 1}, limit_page_length=0,
                                        fields=["from_currency", "to_currency", "fiscal_year",
                                                "fiscal_period", "rate_type"])}
    actions = plan_adoption(used, governed, quotes, str(frappe.utils.today()))
    actions, unset = check_references(actions, usd_references({c for a in actions for c in a[1][:2]}))
    if unset:
        message = no_reference_message(unset, getattr(getattr(frappe, "local", None), "site", None) or "<site>")
        if not dry_run:
            # Nothing is written, and the patch runs again at the next migrate.
            raise RuntimeError(message + " Nothing was adopted.")
        print(f"konsol#103 adoption (dry run): {message}")

    summary = {"adopted": [], "skipped": [], "refused": []}
    frappe.flags.konsol_adopting_rates = True
    try:
        for action in actions:
            key = action[1]
            label = f"{key[0]} → {key[1]} FY{key[2]} P{key[3]} {key[4]}"
            if action[0] == "skip":
                summary["skipped"].append(f"{label}: {action[2]}")
                continue
            _, _, rate, erp_rate, note = action
            if dry_run:
                summary["adopted"].append(f"{label} = {rate:.9g} (dry run)")
                continue
            quote, per = choose_quoted_per(rate)
            frappe.db.savepoint("konsol_rate_adoption")
            try:
                doc = frappe.get_doc({
                    "doctype": DOCTYPE, "from_currency": key[0], "to_currency": key[1],
                    "fiscal_year": key[2], "fiscal_period": key[3], "rate_type": key[4],
                    "quote": quote, "quoted_per": str(per),
                    # the ERP quote, per the same unit
                    "erp_quote": round(erp_rate * per, 9) if erp_rate else None,
                    "source": ADOPTION_SOURCE, "source_note": note,
                })
                doc.insert(ignore_permissions=True)
                doc.submit()
            except frappe.ValidationError as e:
                frappe.db.rollback(save_point="konsol_rate_adoption")
                frappe.clear_last_message()
                summary["refused"].append(f"{label}: {e}")
                continue
            summary["adopted"].append(f"{label} = {rate:.9g} ({doc.name})")
    finally:
        frappe.flags.konsol_adopting_rates = False

    for kind in ("adopted", "skipped", "refused"):
        for line in summary[kind]:
            print(f"konsol#103 adoption: {kind}: {line}")
    print(f"konsol#103 adoption: {len(summary['adopted'])} adopted, {len(summary['skipped'])} skipped, "
          f"{len(summary['refused'])} refused" + (" (dry run: nothing written)" if dry_run else ""))
    frappe.logger("konsol").info(f"konsol#103 rate adoption: {json.dumps(summary)}")
    return summary
