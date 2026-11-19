"""The fingerprint of a Budget Cycle's approved INPUTS (konsol#348 S4 K7,
BUD-11-3; S4-Q5 a, chosen under the standing rule on 15 Nov 2026).

The close fingerprint (konsol/close/fingerprint_model.py) hashes the numbers
a close run checked. A budget has no such number to sign yet: what is
approved is the Cycle's inputs, and what BUD-11-3 asks is that an input
changed after Approve is reported, never silently carried. So this hashes
the inputs, not the translated result (a Cycle on actual rates would void
every month as the actual rates arrive):

- the Cycle's Rate Set;
- its Budget Annual Input rows: grain (entity, account, layer, currency,
  the declared budget dimensions), amount and spread profile;
- its Budget Entry Lines: grain (entity, year, period, account, layer,
  currency, basis, dimensions), debit and credit, summed per grain as the
  warehouse sums them, so a line split in two is the same budget;
- the Spread Profile weights the annual rows name (an unused profile is not
  an input);
- the approved Group Exchange Rates (quote and unit, as entered) and
  Historical Equity Rates of that set;
- every approved Ownership Period link on the site (konsol#552, Deepak Pai,
  18 Nov 2026). An ownership exit recorded after Approve must read as a
  changed input; it must not silently change the consolidated budget. Every
  link is read, whatever its entity or date. A holding that has no budget rows
  but owns a Cycle entity changes only its own link when it exits, so a
  narrower rule would miss it. The rule is conservative: any ownership change
  marks every approved Cycle stale. Each link's percentage (2 places),
  method, effective and end dates, acquisition and disposal markers, and
  supersession are hashed. Acquisition
  and disposal prices are not hashed: a budget does not consume them.

Each row's text is hashed with its kind, and the hashes are summed mod
2**256 (never XORed: two equal hashes would cancel), so the result is
order-independent and any one changed value changes it. Amounts are
normalised to their stored scale (Decimal(18,2) annual, Decimal(18,4)
lines, 9 places for a rate quote, 6 for a historical rate), so 100, 100.0
and "100.00" are one number. A NULL amount raises ``ValueError``: an
unknown number cannot be fingerprinted as any value.

Pure: no frappe, no warehouse. ``konsol/epm/budget_fingerprint.py`` reads
the rows and stamps the result on the Cycle (K8).
"""
import hashlib
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

#: Stored as "<VERSION>:<64 hex>", so a later change of recipe reads as a
#: different fingerprint, never as a match.
VERSION = "v2"  # v2: ownership links (konsol#552); every v1 stamp now reads as stale
_MOD = 2 ** 256
_SEP = "\x1f"

#: Each kind: (tag, grain fields, (value field, decimal places)...). The
#: declared budget dimensions are appended to the two budget grains.
ANNUAL_GRAIN = ("data_area_id", "main_account", "layer", "currency", "spread_profile_id")
ANNUAL_VALUES = (("annual_amount", 2),)
LINE_GRAIN = ("data_area_id", "fiscal_year", "fiscal_period", "main_account", "layer", "currency",
              "amount_basis")
LINE_VALUES = (("debit", 4), ("credit", 4))
WEIGHT_GRAIN = ("profile_id", "fiscal_period")
WEIGHT_VALUES = (("weight", 9),)
RATE_GRAIN = ("from_currency", "to_currency", "fiscal_year", "fiscal_period", "rate_type")
RATE_VALUES = (("quote", 9), ("quoted_per", 0))
HISTORICAL_GRAIN = ("consolidation_group", "data_area_id", "main_account", "rate_date", "to_currency")
HISTORICAL_VALUES = (("historical_rate", 6),)
OWNERSHIP_GRAIN = ("consolidation_group", "data_area_id", "owner_group", "owner_data_area_id",
                   "consolidation_method", "effective_date", "end_date", "acquisition_date",
                   "is_first_acquisition", "is_disposal", "disposal_date", "supersedes",
                   "superseded_end_date")
OWNERSHIP_VALUES = (("ownership_pct", 2),)


def _number(value, places, kind, row, field):
    """The value as a Decimal at its stored scale; NULL raises by row."""
    if value is None or value == "":
        raise ValueError(
            "A NULL %s on a %s row (%s): the inputs cannot be fingerprinted." % (
                field, kind, ", ".join("%s=%s" % (k, row.get(k)) for k in sorted(row) if k != field)))
    try:
        return Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    except InvalidOperation:
        raise ValueError("%s %r on a %s row is not a number." % (field, value, kind))


def _key(kind, row, grain):
    return (kind,) + tuple(str(row.get(f)) if row.get(f) is not None else "" for f in grain)


def _hash(key, values):
    text = _SEP.join(list(key) + [str(v) for v in values])
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest(), "big")


def _summed(kind, rows, grain, values):
    """{key: [summed value per value field]}: rows of one grain are one number."""
    totals = {}
    for row in rows:
        key = _key(kind, row, grain)
        amounts = [_number(row.get(f), p, kind, row, f) for f, p in values]
        if key in totals:
            totals[key] = [a + b for a, b in zip(totals[key], amounts)]
        else:
            totals[key] = amounts
    return totals


def _each(kind, rows, grain, values):
    """One hash per row: reference rows (weights, rates) are not summed, so a
    duplicate row is a change rather than a doubled value."""
    for row in rows:
        yield _hash(_key(kind, row, grain), [_number(row.get(f), p, kind, row, f) for f, p in values])


def input_fingerprint(rate_set, annual_inputs, entry_lines, weights, exchange_rates, historical_rates,
                      ownership_links, dimensions=()):
    """The fingerprint of a Cycle's approved inputs: "v1:<64 hex>".

    ``dimensions`` are the site's declared budget dimension fields (dim_*),
    part of both budget grains; a dim_* key on a row that is not declared is
    not read. ``ownership_links`` are the site's approved links, every one
    hashed (konsol#552: a holding without budget rows can exit too).
    ``weights`` may hold every profile: only the profiles the
    annual rows name are hashed.
    """
    dims = tuple(dimensions or ())
    total = _hash(("rate_set",), [rate_set or ""])
    for key, amounts in _summed("annual", annual_inputs, ANNUAL_GRAIN + dims, ANNUAL_VALUES).items():
        total += _hash(key, amounts)
    for key, amounts in _summed("line", entry_lines, LINE_GRAIN + dims, LINE_VALUES).items():
        total += _hash(key, amounts)
    used = {str(r.get("spread_profile_id")) for r in annual_inputs if r.get("spread_profile_id")}
    used_weights = [w for w in weights if str(w.get("profile_id")) in used]
    for h in _each("weight", used_weights, WEIGHT_GRAIN, WEIGHT_VALUES):
        total += h
    for h in _each("rate", exchange_rates, RATE_GRAIN, RATE_VALUES):
        total += h
    for h in _each("historical", historical_rates, HISTORICAL_GRAIN, HISTORICAL_VALUES):
        total += h
    for h in _each("ownership", ownership_links, OWNERSHIP_GRAIN, OWNERSHIP_VALUES):
        total += h
    return "%s:%064x" % (VERSION, total % _MOD)
