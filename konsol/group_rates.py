"""Governed group exchange rates (konsol#103): the rules around Group Exchange Rate.

Decided by the user on 13 Sep 2026. Group finance owns one rate table in konsol.
The ERP feed is demoted to a proposal: ``prefill_from_erp`` reads what the ERP
quotes for a period and creates DRAFT rates, and a person approves (submits)
them. Rates lock when their period closes, and a period cannot close while a
currency in its ledgers lacks an approved Closing or Average rate into a group
reporting currency it is translated into (``assert_rates_complete``, called
from Period Status).

``adopt_erp_rates`` is the one-time upgrade: it records, as approved governed
rows authored by the system and labelled "Adoption", the rates the warehouse
already translated each period at, so the translation that reads only
governed rates (konsolidat#93) gives the same figures on its first build.
"""
import datetime
import json

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

#: The #138 magnitude guard, applied at entry. KEEP IN STEP with konsolidat's
#: dbt_project/tests/assert_exchange_rate_sane_magnitude.sql: a non-identity
#: rate outside its band is a scaling error, not a market move. WIDE_BAND is
#: the currencies that quote far from parity against the majors.
WIDE_BAND = frozenset({"JPY", "KRW", "IDR", "VND", "HUF", "CLP", "ISK", "INR", "RUB",
                       "PHP", "TRY", "THB", "CZK"})
TIGHT_BOUNDS = (0.05, 20.0)
WIDE_BOUNDS = (0.0001, 10000.0)


# -- pure rules -----------------------------------------------------------------

def magnitude_bounds(from_currency, to_currency):
    if from_currency in WIDE_BAND or to_currency in WIDE_BAND:
        return WIDE_BOUNDS
    return TIGHT_BOUNDS


def magnitude_problem(from_currency, to_currency, rate):
    """None, or why ``rate`` is implausible for the pair (#138)."""
    lo, hi = magnitude_bounds(from_currency, to_currency)
    if lo <= float(rate) <= hi:
        return None
    return (f"{float(rate):g} {to_currency} per {from_currency} is outside [{lo:g}, {hi:g}], the "
            f"plausible range for this pair (#138). Enter the true rate: units of {to_currency} "
            f"per 1 {from_currency}, with no multiplier.")


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
                quotes.append({"source": source, "erp_type": erp_type, "rate": round(hit[0], 9),
                               "valid_from": hit[1], "how": hit[2]})
                break
    return quotes


def describe_quotes(quotes, fiscal_year, fiscal_period):
    parts = [f"{q['source']} {q['erp_type']} {q['rate']:.9g}, valid from {q['valid_from']} ({q['how']})"
             for q in quotes]
    note = f"Pre-filled from the ERP feed for FY{fiscal_year} P{fiscal_period}: " + "; ".join(parts) + "."
    if len({q["rate"] for q in quotes}) > 1:
        note = (f"ERP SOURCES DISAGREE: the proposal takes {quotes[0]['source']}'s quote. Check "
                "before approving. " + note)
    return note


def plan_adoption(used, governed, quote_rows_by_period, today):
    """The one-time adoption, as a list of actions. Pure.

    ``used``: (from, to, fiscal_year, fiscal_period, [closing rates], [average
    rates]) as gold_consolidated_trial_balance translated them. ``governed``:
    keys (from, to, fy, fp, rate_type) that already have an approved rate.
    Returns [("adopt", key, rate, erp_rate, note) | ("skip", key, reason)].
    """
    actions = []
    for f, t, fy, fp, closing, average in used:
        fy, fp = int(fy), int(fp)
        for rate_type, rates in (("Closing", closing), ("Average", average)):
            key = (f, t, fy, fp, rate_type)
            distinct = sorted({round(float(r), 9) for r in rates})
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
                match = next((q for q in quotes if abs(q["rate"] - rate) <= 1e-9 * max(1.0, rate)), None)
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


def _unknown_table(exc):
    """True when ClickHouse refused because a relation doesn't exist yet (a
    site that has never built): nothing has been translated, so nothing is owed."""
    text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
    return "UNKNOWN_TABLE" in text or "Code: 60" in text


#: The group node's currency, per group: the node with no entity.
_GROUP_CURRENCIES = ("(SELECT consolidation_group, reporting_currency FROM epm_gold.consolidation_groups "
                     "WHERE data_area_id = '' AND reporting_currency != '')")


def required_pairs(fiscal_year, fiscal_period):
    """(entity currency, group reporting currency) for every entity with ledger
    rows in the period, into every group it rolls up to."""
    rows = _ch_rows(
        "SELECT DISTINCT ec.accounting_currency, g.reporting_currency "
        "FROM epm_gold.gold_trial_balance AS tb "
        "INNER JOIN (SELECT data_area_id, accounting_currency FROM epm_silver.silver_entity_currencies "
        "            WHERE accounting_currency != '') AS ec ON ec.data_area_id = tb.data_area_id "
        "INNER JOIN epm_staging.consolidation_ancestry AS a ON a.data_area_id = tb.data_area_id "
        f"INNER JOIN {_GROUP_CURRENCIES} AS g ON g.consolidation_group = a.consolidation_group "
        "WHERE tb.fiscal_year = {fy:UInt16} AND tb.fiscal_period = {fp:UInt16} "
        "AND ec.accounting_currency != g.reporting_currency",
        {"fy": int(fiscal_year), "fp": int(fiscal_period)},
    )
    return {(f, t) for f, t in rows}


def tree_pairs():
    """The same pairs for every entity in the tree, ledgers or not: what a
    period needs before its trial balances arrive."""
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


def assert_rates_complete(fiscal_year, fiscal_period):
    """Refuse to close a period while a ledger currency lacks an approved rate.

    Fails closed: if the warehouse can't say which currencies the ledgers hold,
    the period does not close. A warehouse that has never built a trial
    balance holds no ledgers, so it owes nothing."""
    try:
        pairs = required_pairs(fiscal_year, fiscal_period)
    except Exception as e:  # noqa: BLE001 — any failure to read means "can't verify"
        if _unknown_table(e):
            return
        frappe.throw(
            f"Cannot close fiscal period {fiscal_period} of FY{fiscal_year}: the warehouse could not "
            f"say which currencies its ledgers hold ({type(e).__name__}), so its group exchange "
            "rates cannot be checked.", frappe.ValidationError)
    missing = missing_rates(fiscal_year, fiscal_period, pairs)
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
    is left alone. Group Accountant, Close Lead and System Manager only."""
    frappe.only_for(PREFILL_ROLES)
    fy, fp = int(fiscal_year), int(fiscal_period)
    pairs = required_pairs(fy, fp) or tree_pairs()
    rows = erp_quote_rows(period_start(fy, fp))
    taken = {(r.from_currency, r.to_currency, r.rate_type) for r in frappe.get_all(
        DOCTYPE, filters={"fiscal_year": fy, "fiscal_period": fp, "docstatus": ["<", 2]},
        fields=["from_currency", "to_currency", "rate_type"], limit_page_length=0)}
    out = {"created": [], "existing": [], "no_quote": [], "refused": []}
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
            doc = frappe.get_doc({
                "doctype": DOCTYPE, "to_currency": t, "from_currency": f, "rate_type": rate_type,
                "fiscal_year": fy, "fiscal_period": fp, "rate": quotes[0]["rate"],
                "erp_rate": quotes[0]["rate"], "source": PREFILL_SOURCE,
                "source_note": describe_quotes(quotes, fy, fp),
            })
            try:
                doc.insert()
            except frappe.ValidationError as e:
                # The #138 guard refusing an ERP quote is the guard working.
                out["refused"].append(f"{label}: {e}")
                continue
            out["created"].append(doc.name)
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
    """
    if frappe.session.user != "Administrator":
        frappe.throw("The rate adoption runs as the system (Administrator) only.", frappe.PermissionError)
    try:
        used = translated_rates()
    except Exception as e:  # noqa: BLE001
        if _unknown_table(e):
            print("konsol#103 adoption: the warehouse has never built a consolidated trial balance; "
                  "nothing to adopt.")
            return {"adopted": [], "skipped": [], "refused": []}
        raise
    governed = {(r.from_currency, r.to_currency, int(r.fiscal_year), int(r.fiscal_period), r.rate_type)
                for r in frappe.get_all(DOCTYPE, filters={"docstatus": 1}, limit_page_length=0,
                                        fields=["from_currency", "to_currency", "fiscal_year",
                                                "fiscal_period", "rate_type"])}
    periods = sorted({(int(r[2]), int(r[3])) for r in used})
    quotes = {p: erp_quote_rows(period_start(*p)) for p in periods}
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
            frappe.db.savepoint("konsol_rate_adoption")
            try:
                doc = frappe.get_doc({
                    "doctype": DOCTYPE, "from_currency": key[0], "to_currency": key[1],
                    "fiscal_year": key[2], "fiscal_period": key[3], "rate_type": key[4],
                    "rate": rate, "erp_rate": erp_rate, "source": ADOPTION_SOURCE, "source_note": note,
                })
                doc.insert(ignore_permissions=True)
                doc.submit()
            except frappe.ValidationError as e:
                frappe.db.rollback(save_point="konsol_rate_adoption")
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
