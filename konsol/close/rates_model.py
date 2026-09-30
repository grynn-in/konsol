"""Rates model, part 1, pure (konsol#305 E401; story 4.1; #305-W2-3, D2-9).

``grid(required, docs, earlier, threshold, move_problem)`` turns the period's
required (from, to) pairs and this period's Group Exchange Rate rows into the
grid the Rates screen shows: one row per pair, with a Closing cell and an
Average cell. A rate for a pair the period does not need is listed apart, in
``unrequired``, never dropped.

The rate rule itself is not copied here: ``group_rates.move_problem`` imports
frappe at module level, so it is taken **injected** as ``move_problem``. The
true rate is computed by the API before a doc dict reaches this module, so
every ``rate``/``erp_rate`` this module sees is already "to per 1 from".

Imports nothing from frappe or konsol.
"""

RATE_TYPES = ("Closing", "Average")


def _previous_index(earlier):
    """``{(from, to, rate_type): earlier_row}`` — the latest (by fiscal_year,
    fiscal_period) approved row for each key. The last approved one, so a gap
    (a period with no rate) compares with the rate before it."""
    index = {}
    for row in earlier:
        key = (row["from_currency"], row["to_currency"], row["rate_type"])
        candidate = (row["fiscal_year"], row["fiscal_period"])
        current = index.get(key)
        if current is None or candidate > (current["fiscal_year"], current["fiscal_period"]):
            index[key] = row
    return index


def _by_pair_type(docs):
    """``{(from, to, rate_type): [doc, ...]}``. Raises ValueError naming any
    ``rate_type`` outside ``RATE_TYPES``."""
    index = {}
    for doc in docs:
        rate_type = doc["rate_type"]
        if rate_type not in RATE_TYPES:
            raise ValueError(f"Unknown rate_type {rate_type!r}; expected one of {RATE_TYPES}.")
        key = (doc["from_currency"], doc["to_currency"], rate_type)
        index.setdefault(key, []).append(doc)
    return index


def _previous_cell(prev_row):
    if prev_row is None:
        return None
    return {
        "rate": prev_row["rate"],
        "quoted_per": prev_row["quoted_per"],
        "fiscal_year": prev_row["fiscal_year"],
        "fiscal_period": prev_row["fiscal_period"],
        "name": prev_row["name"],
        "label": f"FY{prev_row['fiscal_year']} P{prev_row['fiscal_period']} ({prev_row['name']})",
    }


def _empty_cell():
    return {
        "status": "missing",
        "name": None,
        "owner": None,
        "quote": None,
        "quoted_per": None,
        "rate": None,
        "previous": None,
        "delta": None,
        "flag": None,
        "change_reason": None,
        "source": None,
        "extra_drafts": [],
        "approver": None,
    }


def _cell(from_currency, to_currency, rate_type, pair_type_docs, previous_index, threshold, move_problem):
    """One grid cell for ``(from_currency, to_currency, rate_type)``. With no
    doc at all, ``missing`` and ``move_problem`` is never called."""
    docs = pair_type_docs.get((from_currency, to_currency, rate_type), [])
    approved = [d for d in docs if d.get("docstatus") == 1]
    drafts = [d for d in docs if d.get("docstatus") == 0]

    if approved:
        shown = approved[0]
        extra_names = [d["name"] for d in approved[1:]] + [d["name"] for d in drafts]
    elif drafts:
        shown = max(drafts, key=lambda d: d["modified"])
        extra_names = [d["name"] for d in drafts if d["name"] != shown["name"]]
    else:
        shown = None
        extra_names = []

    if shown is None:
        return _empty_cell()

    prev_row = previous_index.get((from_currency, to_currency, rate_type))
    previous = _previous_cell(prev_row)
    rate = shown["rate"]
    delta = (rate / previous["rate"] - 1) if (previous is not None and rate is not None) else None
    previous_arg = (previous["rate"], previous["label"]) if previous is not None else None
    unit = f"{to_currency} per {from_currency}"
    flag = move_problem(rate, previous_arg, shown.get("erp_rate"), unit=unit, threshold=threshold)

    return {
        "status": "approved" if shown.get("docstatus") == 1 else "awaiting_approval",
        "name": shown["name"],
        "owner": shown.get("owner"),
        "quote": shown.get("quote"),
        "quoted_per": shown.get("quoted_per"),
        "rate": rate,
        "previous": previous,
        "delta": delta,
        "flag": flag,
        "change_reason": shown.get("change_reason"),
        "source": shown.get("source"),
        "extra_drafts": extra_names,
        "approver": None,
    }


def grid(required, docs, earlier, threshold, move_problem):
    """``{"rows", "unrequired", "summary"}`` for the period's rates grid.

    ``required`` is a set of ``(from, to)``, or None when the warehouse
    could not say (E4-P9): then every pair with docs goes into ``rows`` with
    ``required: None`` and ``unrequired`` is ``[]``. Otherwise ``rows`` holds
    exactly the required pairs (even with no docs at all) and ``unrequired``
    holds pairs that have docs but are not required.

    ``threshold`` is passed through to ``move_problem`` untouched: never
    replaced with a guessed value.
    """
    pair_type_docs = _by_pair_type(docs)
    previous_index = _previous_index(earlier)
    doc_pairs = {(d["from_currency"], d["to_currency"]) for d in docs}

    if required is None:
        row_pairs = sorted(doc_pairs, key=lambda p: (p[1], p[0]))
        required_flag = {pair: None for pair in row_pairs}
        unrequired_pairs = []
    else:
        required_pairs = set(required)
        row_pairs = sorted(required_pairs, key=lambda p: (p[1], p[0]))
        required_flag = {pair: True for pair in row_pairs}
        unrequired_pairs = sorted(doc_pairs - required_pairs, key=lambda p: (p[1], p[0]))

    def build_row(from_currency, to_currency, required_value):
        closing = _cell(from_currency, to_currency, "Closing", pair_type_docs, previous_index, threshold, move_problem)
        average = _cell(from_currency, to_currency, "Average", pair_type_docs, previous_index, threshold, move_problem)
        return {
            "from_currency": from_currency,
            "to_currency": to_currency,
            "required": required_value,
            "closing": closing,
            "average": average,
        }

    rows = [build_row(from_currency, to_currency, required_flag[(from_currency, to_currency)])
            for from_currency, to_currency in row_pairs]
    unrequired = [build_row(from_currency, to_currency, False)
                  for from_currency, to_currency in unrequired_pairs]

    summary = {"missing": 0, "awaiting_approval": 0, "approved": 0}
    for row in rows:
        summary[row["closing"]["status"]] += 1
        summary[row["average"]["status"]] += 1

    return {"rows": rows, "unrequired": unrequired, "summary": summary}
