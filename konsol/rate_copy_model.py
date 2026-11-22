"""Copy rates from previous period: the pure rules (konsol#584).

Decided by Deepak Pai on 22 Nov 2026: a button on the Group Exchange Rate list
proposes DRAFTS for a period from the approved Closing and Average rates of
one Rate Set in the previous regular period. Nothing is submitted, and a
person approves each draft for its own period. Rejected: a silent
carry-forward in the warehouse, which would break the no-silent-fallback rule
and leave no approver per period.

``group_rates.copy_from_previous_period`` reads the calendar and the rates and
inserts the drafts; this module decides which period is "previous" and which
rates are copied. It imports nothing from frappe or konsol.
"""

REGULAR = "Regular"
RATE_TYPES = ("Closing", "Average")
TAKEN = "the target period already has a draft or an approved rate for this key"


def key_label(from_currency, to_currency, rate_type):
    """The key as the pre-fill names it: "EUR → CHF Closing"."""
    return f"{from_currency} → {to_currency} {rate_type}"


def _regular(rows, below=None):
    """The Regular period numbers of a declared year, below ``below`` if given."""
    return sorted(int(r["period"]) for r in rows or ()
                  if r.get("type") == REGULAR and (below is None or int(r["period"]) < below))


def previous_regular_period(fiscal_year, fiscal_period, calendar):
    """((year, period), None) of the Regular period before the target, or
    (None, why).

    ``calendar`` is {year: [{"period": n, "type": t}, ...]} for each declared
    year asked for; a year missing from it is undeclared. The previous period
    is the latest Regular period of the target's year below the target, else
    the LAST Regular period that the year before declares (so P1's previous
    period is the previous year's last regular period, whatever its number).
    Opening, Closing and Adjustment periods are never a source. An undeclared
    year is refused by name, never skipped over to an earlier one."""
    year, period = int(fiscal_year), int(fiscal_period)
    if year not in calendar:
        return None, f"FY{year} is not declared: create it in EPM Fiscal Year."
    if period not in {int(r["period"]) for r in calendar[year] or ()}:
        return None, f"FY{year} has no period {period}: declare it in EPM Fiscal Year."
    earlier = _regular(calendar[year], below=period)
    if earlier:
        return (year, earlier[-1]), None
    prior = year - 1
    if prior not in calendar:
        return None, (f"FY{year} P{period} has no earlier Regular period in FY{year}, and FY{prior} is not "
                      f"declared, so there is no previous period to copy from. Declare FY{prior} in EPM "
                      "Fiscal Year, or enter the rates by hand.")
    last = _regular(calendar[prior])
    if not last:
        return None, (f"FY{year} P{period} has no earlier Regular period in FY{year}, and FY{prior} declares "
                      "no Regular period, so there is no previous period to copy from.")
    return (prior, last[-1]), None


def plan_copy(sources, taken, rate_set):
    """(drafts, skipped): the source rates to draft, and "label: reason" for
    each one left alone.

    ``sources`` are the previous period's Group Exchange Rate rows (mappings
    with name, from_currency, to_currency, rate_type, docstatus, rate_set).
    Only an APPROVED (docstatus 1) Closing or Average rate of ``rate_set`` is
    a source; anything else is not a candidate and is not listed. ``taken``
    is the set of (from, to, rate_type) keys the target period already has a
    draft or an approved rate for in ``rate_set``: each is skipped."""
    drafts, skipped, seen = [], [], {}
    candidates = sorted(
        (s for s in sources
         if int(s["docstatus"]) == 1 and s["rate_set"] == rate_set and s["rate_type"] in RATE_TYPES),
        key=lambda s: (s["from_currency"], s["to_currency"], RATE_TYPES.index(s["rate_type"]), s["name"]))
    for s in candidates:
        key = (s["from_currency"], s["to_currency"], s["rate_type"])
        label = key_label(*key)
        if key in taken:
            skipped.append(f"{label}: {TAKEN}")
        elif key in seen:
            skipped.append(f"{label}: {s['name']} is a second approved rate for this key in the source period")
        else:
            seen[key] = s["name"]
            drafts.append(s)
    return drafts, skipped


def copy_note(source, source_period, quoted):
    """The Source Note of a copied draft: which rate, which period, which set."""
    year, period = source_period
    return (f"Copied from {source['name']}, the approved {source['rate_type']} rate for FY{year} P{period} "
            f"in Rate Set {source['rate_set']}: {quoted}. Approve it for this period only if the rate "
            "still holds.")
