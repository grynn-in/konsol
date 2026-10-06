"""The fingerprint of the numbers a close run checked (konsol#338).

Decided by Deepak Pai, 6 Oct 2026 (#338-1): a fingerprint of the numbers a
close run checked is stored when the checks run; when a build completes,
each signed period's fingerprint is recomputed and only the periods whose
numbers changed are voided. Rejected: #338-2 (void on every rebuild, which
voids signatures over unchanged numbers) and #338-3 (flag without voiding,
which lets a signature stand on changed numbers, against R2b).

Coordinator's engineering calls (#338 decision comment):

- A period's fingerprint covers every period up to and including it, in
  (fiscal_year, fiscal_period) order: the balance sheet is cumulative.
- It is built per consolidation group from ``gold_fully_consolidated_tb``,
  aggregated to (entity, account, adjustment_type) per period.
- Amounts are rounded to cents, so float noise (about 1e-7, measured) does
  not read as a change. A key that rounds to 0.00 reads as absent: a zero
  balance and no row are the same numbers.
- Each key's hash is summed (mod 2**256), not XORed: with XOR two equal
  hashes cancel.

Pure: no frappe, no ClickHouse. A NULL amount raises ``ValueError``: an
unknown number cannot be fingerprinted as any value.
"""
import bisect
import hashlib

#: The fingerprint's format. Stored as "<VERSION>:<64 hex>", so a later
#: change of recipe reads as a different fingerprint, never as a match.
VERSION = "v1"
_MOD = 2 ** 256
_SEP = "\x1f"


def _cents(amount, row):
    if amount is None:
        raise ValueError(
            "A NULL amount in gold_fully_consolidated_tb (group %s, FY%s P%s, entity %s, "
            "account %s, %s): the numbers cannot be fingerprinted." % (
                row.get("group"), row.get("fiscal_year"), row.get("fiscal_period"),
                row.get("entity"), row.get("account"), row.get("adjustment_type")))
    return float(amount)


def _key_hash(group, fy, fp, entity, account, adjustment_type, cents):
    text = _SEP.join((str(group or ""), str(fy), str(fp), str(entity or ""),
                      str(account or ""), str(adjustment_type or ""), str(cents)))
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest(), "big")


def _hex(total):
    return "%s:%064x" % (VERSION, total % _MOD)


def _period_sums(rows):
    """{group: {(fy, fp): sum of that period's key hashes}}."""
    totals = {}
    for row in rows:
        amount = _cents(row.get("amount"), row)
        key = (row.get("group"), int(row["fiscal_year"]), int(row["fiscal_period"]),
               row.get("entity"), row.get("account"), row.get("adjustment_type"))
        totals[key] = totals.get(key, 0.0) + amount
    sums = {}
    for key, total in totals.items():
        cents = int(round(total * 100))
        if cents == 0:
            continue
        group, fy, fp = key[0], key[1], key[2]
        per_group = sums.setdefault(group, {})
        per_group[(fy, fp)] = (per_group.get((fy, fp), 0) + _key_hash(*key, cents)) % _MOD
    return sums


def _cumulative(per_period):
    """Sorted period keys and their running sums."""
    keys = sorted(per_period)
    running, total = [], 0
    for k in keys:
        total = (total + per_period[k]) % _MOD
        running.append(total)
    return keys, running


def _upto(keys, running, period):
    i = bisect.bisect_right(keys, period)
    return running[i - 1] if i else 0


def group_fingerprints(rows, periods):
    """{(group, fy, fp): fingerprint} for every group with numbers and every
    (fy, fp) in ``periods``; each covers every period up to and including it.
    A group with no numbers up to a period still gets the empty fingerprint
    for it."""
    out = {}
    for group, per_period in _period_sums(rows).items():
        keys, running = _cumulative(per_period)
        for fy, fp in periods:
            out[(group, int(fy), int(fp))] = _hex(_upto(keys, running, (int(fy), int(fp))))
    return out


def run_fingerprints(rows, periods):
    """{(fy, fp): fingerprint} of every group's numbers up to each period:
    the value stored on a close run. The per-group fingerprints are combined
    in group order, over the groups with numbers up to that period."""
    sums = _period_sums(rows)
    cumulative = {g: _cumulative(p) for g, p in sums.items()}
    out = {}
    for fy, fp in periods:
        period = (int(fy), int(fp))
        lines = []
        for group in sorted(cumulative, key=lambda g: str(g)):
            total = _upto(*cumulative[group], period)
            if total:
                lines.append("%s%s%s" % (group, _SEP, _hex(total)))
        digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
        out[period] = "%s:%s" % (VERSION, digest)
    return out


def run_fingerprint(rows, fiscal_year, fiscal_period):
    """The fingerprint of every group's numbers up to (fiscal_year, fiscal_period)."""
    period = (int(fiscal_year), int(fiscal_period))
    return run_fingerprints(rows, [period])[period]
