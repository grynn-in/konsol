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
import datetime
import json
import math
import re

import frappe

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
#: the currency's units per 1 USD. USD is the anchor, so its reference is 0 by
#: definition. A Frappe Float cannot be NULL (unset reads as 0), so for every
#: other currency 0 means "not set".
REFERENCE_CURRENCY = "USD"
REFERENCE_FIELD = "usd_log10"
#: A rate more than this many powers of ten from the references is refused.
MAGNITUDE_TOLERANCE_DECADES = 1.0
#: A move larger than this fraction needs a Reason for Change.
MOVE_NEEDS_REASON = 0.5
#: A quote is stored in the direction that keeps its digits: MariaDB keeps 9
#: decimal places, so a quote below 0.1 (KRW -> USD 0.00074) is entered and
#: stored the other way round (1350 KRW per USD) and inverted in the warehouse.
MIN_QUOTE = 0.1


# -- pure rules -----------------------------------------------------------------

def effective_rate(rate, inverse_quote):
    """Units of the group currency per 1 unit of the from-currency."""
    rate = float(rate or 0)
    if not rate:
        return 0.0
    return 1.0 / rate if int(inverse_quote or 0) else rate


def as_stored(rate):
    """(quote, inverse_quote) for a rate "to per 1 from": the rate itself when
    it keeps its digits at 9 decimal places, else its inverse."""
    rate = float(rate)
    if rate >= MIN_QUOTE:
        return round(rate, 9), 0
    return round(1.0 / rate, 9), 1


def usd_reference(code, value):
    """A currency's usd_log10, or None when it has none (see REFERENCE_FIELD)."""
    if code == REFERENCE_CURRENCY:
        return 0.0
    if value in (None, ""):
        return None
    value = float(value)
    return None if value == 0 else value


def usd_references(codes):
    """{code: usd_log10 or None} from ISO Currency, for the codes given."""
    codes = sorted({c for c in codes if c})
    rows = frappe.get_all("ISO Currency", filters={"name": ["in", codes]},
                          fields=["name", REFERENCE_FIELD], limit_page_length=0) if codes else []
    found = {r.name: r.get(REFERENCE_FIELD) for r in rows}
    return {c: usd_reference(c, found.get(c)) for c in codes}


def magnitude_problem(from_currency, to_currency, rate, refs=None):
    """None, or why ``rate`` (units of ``to_currency`` per 1 ``from_currency``)
    is implausible (#138).

    Refused when abs(log10(rate) - (usd_log10(to) - usd_log10(from))) > 1: the
    rate is more than 10x from what the ISO Currency references imply. A
    currency with no reference is refused, naming the ISO Currency to fix."""
    refs = usd_references((from_currency, to_currency)) if refs is None else refs
    unset = [c for c in (from_currency, to_currency) if refs.get(c) is None]
    if unset:
        return (f"No magnitude reference for {' and '.join(unset)}: set USD Reference (log10) "
                f"({REFERENCE_FIELD}) on ISO Currency {', '.join(unset)}, roughly log10 of its units "
                "per 1 USD, so a scaling error can be told from a real rate (#138).")
    rate = float(rate)
    if rate <= 0:
        return "Rate must be a positive number."
    expected = refs[to_currency] - refs[from_currency]
    off = math.log10(rate) - expected
    if abs(off) <= MAGNITUDE_TOLERANCE_DECADES:
        return None
    return (f"{rate:.9g} {to_currency} per {from_currency} is about {10 ** abs(off):,.0f}x "
            f"{'above' if off > 0 else 'below'} the roughly {10 ** expected:.3g} the ISO Currency "
            f"references imply (usd_log10 {to_currency} {refs[to_currency]:g}, {from_currency} "
            f"{refs[from_currency]:g}); more than 10x off is a scaling error, not a market move (#138). "
            f"Enter the true rate: units of {to_currency} per 1 {from_currency}, with no multiplier, "
            "or tick the inverse quote and enter it the other way round.")


def move_problem(rate, previous=None, erp_rate=None):
    """None, or why ``rate`` needs a Reason for Change: it moves more than 50%
    from ``previous`` ((rate, label) of the last approved rate for its key) or
    from ``erp_rate`` (the ERP quote it was proposed from). Rates are "to per
    1 from"."""
    rate = float(rate)
    moves = []
    for ref, what in ((previous[0], f"the previous approved rate {previous[1]}") if previous else (None, None),
                      (erp_rate, "the ERP quote")):
        if ref and float(ref) > 0:
            change = rate / float(ref) - 1.0
            if abs(change) > MOVE_NEEDS_REASON:
                moves.append(f"{change:+.0%} from {what} ({float(ref):.9g})")
    if not moves:
        return None
    return ("This rate moves " + " and ".join(moves) + ". A move over 50% can be real, but say why "
            "(Reason for Change) before it is saved.")


def period_start(fiscal_year, fiscal_period):
    """The date the warehouse keys a period on (dbt build_date_from_year_period:
    the 1st of month P, period 0 as January). CLS (13) takes December's."""
    return datetime.date(max(int(fiscal_year), 1900), min(max(int(fiscal_period), 1), 12), 1)


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


def describe_quotes(quotes, fiscal_year, fiscal_period):
    parts = [f"{q['source']} {q['erp_type']} {q['rate']:.9g}, valid from {q['valid_from']} ({q['how']})"
             for q in quotes]
    note = f"Pre-filled from the ERP feed for FY{fiscal_year} P{fiscal_period}: " + "; ".join(parts) + "."
    if len({_sig(q["rate"]) for q in quotes}) > 1:
        note = (f"ERP SOURCES DISAGREE: the proposal takes {quotes[0]['source']}'s quote. Check "
                "before approving. " + note)
    return note


def plan_adoption(used, governed, quote_rows_by_period, today):
    """The one-time adoption, as a list of actions. Pure.

    ``used``: (from, to, fiscal_year, fiscal_period, [closing rates], [average
    rates]) as gold_consolidated_trial_balance translated them. ``governed``:
    keys (from, to, fy, fp, rate_type) that already have an approved rate.
    Returns [("adopt", key, rate, erp_rate, note) | ("skip", key, reason)],
    rates "to per 1 from" (``as_stored`` picks the direction to keep).
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


#: The group node's currency, per group: the node with no entity.
_GROUP_CURRENCIES = ("(SELECT consolidation_group, reporting_currency FROM epm_gold.consolidation_groups "
                     "WHERE data_area_id = '' AND reporting_currency != '')")


def required_pairs(fiscal_year, fiscal_period):
    """(entity currency, group reporting currency) for exactly the rows
    gold_consolidated_trial_balance translates in the period: an entity with
    ledger rows, into each group gold_entity_ownership places it under for the
    period, with the translation's own filter (method not 'equity' or 'none',
    a complete chain, so nothing outside the ownership window). An equity
    associate's currency owes no rate: the equity-method model does not
    translate through this table.

    Read from the last build: ownership changed since then is seen at the next."""
    rows = _ch_rows(
        "SELECT DISTINCT ec.accounting_currency, g.reporting_currency "
        "FROM (SELECT DISTINCT data_area_id FROM epm_gold.gold_trial_balance "
        "      WHERE fiscal_year = {fy:UInt16} AND fiscal_period = {fp:UInt16}) AS tb "
        "INNER JOIN (SELECT data_area_id, accounting_currency FROM epm_silver.silver_entity_currencies "
        "            WHERE accounting_currency != '') AS ec ON ec.data_area_id = tb.data_area_id "
        "INNER JOIN (SELECT consolidation_group, data_area_id FROM epm_gold.gold_entity_ownership "
        "            WHERE fiscal_year = {fy:UInt16} AND fiscal_period = {fp:UInt16} "
        "            AND consolidation_method NOT IN ('equity', 'none') AND has_complete_chain = 1) AS eo "
        "    ON eo.data_area_id = tb.data_area_id "
        f"INNER JOIN {_GROUP_CURRENCIES} AS g ON g.consolidation_group = eo.consolidation_group "
        "WHERE ec.accounting_currency != g.reporting_currency",
        {"fy": int(fiscal_year), "fp": int(fiscal_period)},
    )
    return {(f, t) for f, t in rows}


def tree_pairs():
    """The same pairs for every entity in the tree, ledgers or not: what a
    period needs before its trial balances arrive (pre-fill only; the gate
    never reads this)."""
    rows = _ch_rows(
        "SELECT DISTINCT ec.accounting_currency, g.reporting_currency "
        "FROM epm_staging.consolidation_ancestry AS a "
        "INNER JOIN (SELECT data_area_id, accounting_currency FROM epm_silver.silver_entity_currencies "
        "            WHERE accounting_currency != '') AS ec ON ec.data_area_id = a.data_area_id "
        f"INNER JOIN {_GROUP_CURRENCIES} AS g ON g.consolidation_group = a.consolidation_group "
        "WHERE ec.accounting_currency != g.reporting_currency")
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


# -- governed rows --------------------------------------------------------------------

def previous_approved(to_currency, from_currency, rate_type, fiscal_year, fiscal_period):
    """(rate "to per 1 from", label) of the latest approved rate for the same
    key in an earlier period, or None. The last approved one, so a gap (a
    period with no rate) compares with the rate before it."""
    rows = frappe.db.sql(
        "SELECT name, rate, inverse_quote, fiscal_year, fiscal_period FROM `tabGroup Exchange Rate` "
        "WHERE to_currency = %s AND from_currency = %s AND rate_type = %s AND docstatus = 1 "
        "AND (fiscal_year < %s OR (fiscal_year = %s AND fiscal_period < %s)) "
        "ORDER BY fiscal_year DESC, fiscal_period DESC LIMIT 1",
        (to_currency, from_currency, rate_type, int(fiscal_year), int(fiscal_year), int(fiscal_period)),
    )
    if not rows:
        return None
    name, rate, inverse, fy, fp = rows[0]
    return effective_rate(rate, inverse), f"FY{fy} P{fp} ({name})"


# -- the close gate -------------------------------------------------------------------

def _approved_keys(fiscal_year, fiscal_period):
    return {(r.from_currency, r.to_currency, r.rate_type) for r in frappe.get_all(
        DOCTYPE, filters={"docstatus": 1, "fiscal_year": int(fiscal_year),
                          "fiscal_period": int(fiscal_period)},
        fields=["from_currency", "to_currency", "rate_type"], limit_page_length=0)}


def missing_rates(fiscal_year, fiscal_period, pairs=None):
    pairs = required_pairs(fiscal_year, fiscal_period) if pairs is None else pairs
    have = _approved_keys(fiscal_year, fiscal_period)
    return sorted((f, t, rt) for f, t in pairs for rt in RATE_TYPES if (f, t, rt) not in have)


def rate_gate(fiscal_year, fiscal_period):
    """What the close gate says: (missing keys, None), or (None, why the
    warehouse can't answer). The role home shows the same answer.

    Fails closed. Only a warehouse that has never built a trial balance owes
    nothing: the read fails with UNKNOWN_TABLE or UNKNOWN_DATABASE AND
    ``EXISTS TABLE epm_gold.gold_trial_balance`` is false. Any other failure,
    a missing gold_entity_ownership beside a built trial balance included,
    means the rates cannot be checked."""
    try:
        pairs = required_pairs(fiscal_year, fiscal_period)
    except Exception as e:  # noqa: BLE001 — any failure to read means "can't verify"
        names = sorted(ch_error_names(e))
        if _not_built(e):
            try:
                if not ledgers_built():
                    return [], None
            except Exception:  # noqa: BLE001 — can't tell, so fail closed
                pass
        return None, type(e).__name__ + (f" {', '.join(names)}" if names else "")
    return missing_rates(fiscal_year, fiscal_period, pairs), None


def assert_rates_complete(fiscal_year, fiscal_period):
    """Refuse to close a period while a translated currency lacks an approved rate.

    The pairs come from the last build (``required_pairs``): the gate checks
    what that build translates, not ownership edited since."""
    missing, error = rate_gate(fiscal_year, fiscal_period)
    if error:
        frappe.throw(
            f"Cannot close fiscal period {fiscal_period} of FY{fiscal_year}: the warehouse could not "
            f"say which currencies its ledgers translate ({error}), so its group exchange "
            "rates cannot be checked.", frappe.ValidationError)
    if missing:
        frappe.throw(
            f"Cannot close fiscal period {fiscal_period} of FY{fiscal_year}: no approved group exchange "
            "rate for " + ", ".join(f"{f} → {t} {rt}" for f, t, rt in missing)
            + ". Pre-fill them from the ERP (or enter them) and approve each.",
            frappe.ValidationError)


# -- the pre-fill -------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def prefill_from_erp(fiscal_year, fiscal_period):
    """Propose DRAFT rates for a period from what the ERP feed quotes.

    "D365 quotes 0.9378 for March: accept?" Each proposal is a draft carrying
    the quote, its source and how it was reached; nothing is submitted, so
    nothing applies itself. A key that already has a draft or an approved rate
    is left alone. Group Accountant, Close Lead and System Manager only.

    A quote the magnitude guard refuses is reported, not inserted, so the
    refusal pops no dialog. A quote that moves more than 50% needs a person's
    reason, so it is reported too: enter it by hand with the reason."""
    frappe.only_for(PREFILL_ROLES)
    fy, fp = int(fiscal_year), int(fiscal_period)
    pairs = required_pairs(fy, fp) or tree_pairs()
    rows = erp_quote_rows(period_start(fy, fp))
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
                quotes = resolve_quotes(rows, f, t, rate_type)
                if not quotes:
                    out["no_quote"].append(label)
                    continue
                problem = magnitude_problem(f, t, quotes[0]["rate"], refs)
                if problem:
                    # The #138 guard refusing an ERP quote is the guard working.
                    out["refused"].append(f"{label}: {problem}")
                    continue
                quote, inverse = as_stored(quotes[0]["rate"])
                doc = frappe.get_doc({
                    "doctype": DOCTYPE, "to_currency": t, "from_currency": f, "rate_type": rate_type,
                    "fiscal_year": fy, "fiscal_period": fp, "rate": quote, "inverse_quote": inverse,
                    "erp_rate": quote, "source": PREFILL_SOURCE,
                    "source_note": describe_quotes(quotes, fy, fp),
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
            quote, inverse = as_stored(rate)
            frappe.db.savepoint("konsol_rate_adoption")
            try:
                doc = frappe.get_doc({
                    "doctype": DOCTYPE, "from_currency": key[0], "to_currency": key[1],
                    "fiscal_year": key[2], "fiscal_period": key[3], "rate_type": key[4],
                    "rate": quote, "inverse_quote": inverse,
                    # the ERP quote in the same direction as the stored rate
                    "erp_rate": round(effective_rate(erp_rate, inverse), 9) if erp_rate else None,
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
