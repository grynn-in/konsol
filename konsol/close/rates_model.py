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

Part 2 (konsol#305 E402; story 4.3; R2, R5; #305-W2-14) adds ``approve_mode``
and ``pending_items``. The self-approval rule itself is not copied here
either: ``close_policy_model.self_approval_problem`` is taken **injected**,
so this module stays import-free.

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


def approve_mode(user, roles, preparers, doctype, name, policy, approver_roles, self_approval_problem):
    """What ``user`` may do with a draft, for the Rates screen's approve
    column (story 4.3; R2, R5; #305-W2-14). Returns ``{"mode", "message"}``:

    - ``not_approver`` when ``roles`` holds none of ``approver_roles`` (R2:
      only the Close Lead approves).
    - ``direct`` when the injected ``self_approval_problem`` sees no problem
      with no reason: ``user`` is not one of ``preparers`` (the owner, plus
      everyone who edited the draft, #305-W2-14), or the caller is exempt.
    - ``reason`` when a reason would clear the problem (Allowed with reason):
      the message is the no-reason refusal, so the caller can show it before
      the Analyst/Close Lead supplies one.
    - ``refused`` otherwise (Blocked, an undeclared policy, or an unknown
      one), with the policy's own message.

    ``self_approval_problem`` is injected so the rule is not copied here
    (close_policy_model.self_approval_problem).
    """
    if not (set(roles or ()) & set(approver_roles)):
        return {"mode": "not_approver", "message": "The Close Lead approves (R2)."}

    problem = self_approval_problem(policy, preparers, user, doctype, name, None, None)
    if problem is None:
        return {"mode": "direct", "message": None}

    with_reason = self_approval_problem(policy, preparers, user, doctype, name, "reason", None)
    if with_reason is None:
        return {"mode": "reason", "message": problem}

    return {"mode": "refused", "message": problem}


def _her_item(doc, preparers, mode):
    return {
        "doctype": "Historical Equity Rate",
        "name": doc["name"],
        "title": "%s · %s · %s" % (doc["data_area_id"], doc["main_account"], doc["rate_date"]),
        "detail": "%.9g (group %s)" % (doc["historical_rate"], doc["consolidation_group"]),
        "preparer": doc["owner"],
        "edited_by": sorted(preparers - {doc["owner"]}),
        "created": doc["created"],
        "approve": mode,
    }


def _op_item(doc, preparers, mode):
    """An Ownership Period draft's item. The caller's ``doc["effect"]`` (O57;
    story 4.2, C-O4) is emitted as ``ownership_effect`` (R52q, review S18:
    never the journal's ``effect`` key), and ``doc["effect_error"]`` as
    ``ownership_effect_error``. It is passed through only when the caller put it on ``doc``
    (``ownership_change.effect_for``): a dict, or None for a draft without
    ``supersedes`` (a Desk "Record ownership" draft, "effect not previewed").
    A doc without the key (a caller that did not compute it) gives an item
    without the key: it never raises, and never turns into a None that would
    read as a Desk draft. ``effect_error`` (R52i) goes with ``effect``: the
    caller's sentence when the effect could not be read, else None. ``edit``
    (O69, ``op_edit``) is passed through the
    same way: approvals_model's items carry no ``edit`` key."""
    detail = "%g%% · %s" % (doc["ownership_pct"], doc["consolidation_method"])
    if doc.get("end_date"):
        detail += " to %s" % doc["end_date"]
    item = {
        "doctype": "Ownership Period",
        "name": doc["name"],
        "title": "%s in %s from %s" % (doc["data_area_id"], doc["consolidation_group"], doc["effective_date"]),
        "detail": detail,
        "preparer": doc["owner"],
        "edited_by": sorted(preparers - {doc["owner"]}),
        "created": doc["created"],
        "approve": mode,
    }
    if "effect" in doc:
        # R52q (review S18): named for the Ownership Period, so the key never
        # collides with a journal item's ``effect`` (approvals_model).
        item["ownership_effect"] = doc.get("effect")
        # R52i (review S2): why the effect could not be read, or None.
        item["ownership_effect_error"] = doc.get("effect_error")
    if "edit" in doc:
        item["edit"] = doc.get("edit")
    return item


def _iso_day(value):
    """An ISO day of a date, datetime or string; blank gives None."""
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def regular_period_by_start(period_rows):
    """O69 (story 4.2): ``{start_date ISO: (fiscal_year, fiscal_period,
    status)}`` of the calendar's Regular periods. Only a Regular period's
    first day is an ownership change's first day (an Opening P00 shares P01's
    1 Jan). A day on which two Regular periods start maps to neither: the
    period is never guessed.

    R52h: ``status`` is the row's effective status as
    ``fiscal_calendar.fiscal_period_rows`` gives it (the same single calendar
    read), or None when the row carries none; ``op_edit`` reads it."""
    seen = {}
    for row in period_rows:
        if row.get("period_type") != "Regular":
            continue
        day = _iso_day(row.get("start_date"))
        if day is None:
            continue
        seen.setdefault(day, []).append(
            (int(row["fiscal_year"]), int(row["fiscal_period"]), row.get("status")))
    return {day: keys[0] for day, keys in seen.items() if len(keys) == 1}


def op_edit(doc, starts):
    """O69 (story 4.2; wireframe-4.2.md section 1, "The Analyst can edit it
    until it is approved"): what O67's Edit loads for the Ownership Period
    draft ``doc``, or None.

    ``starts`` is ``regular_period_by_start`` of the calendar when the caller
    may save (the roles ``save_ownership_change`` admits), else None. None is
    returned for a caller who may not save, a draft without ``supersedes``
    (a Desk "Record ownership" draft: the form edits only a change it could
    have saved), and a draft whose ``effective_date`` starts no single
    Regular period.

    R52h (review S7/U5; coordinator ruling S7/U5): None also wherever the
    form cannot load the draft, by the rule of its choices
    (``rates_api._change_choices``): a group-node draft (blank
    ``data_area_id``; the form offers only nodes that name an entity), and a
    draft whose first period's status is not Open (the form offers only Open
    Regular periods; a missing status is never read as Open)."""
    if starts is None or not doc.get("supersedes"):
        return None
    if not doc.get("data_area_id"):
        return None
    key = starts.get(_iso_day(doc.get("effective_date")))
    if key is None or key[2] != "Open":
        return None
    return {"consolidation_group": doc["consolidation_group"],
            "entity": doc["data_area_id"],
            "fiscal_year": key[0], "fiscal_period": key[1],
            "ownership_pct": float(doc["ownership_pct"]),
            "consolidation_method": doc["consolidation_method"]}


def pending_items(her, ops, preparers_by_name, user, roles, policy, approver_roles, self_approval_problem):
    """The pending Historical Equity Rate and Ownership Period drafts
    (story 4.3; E4-P8, E4-P12), sorted oldest first by ``created``.

    ``preparers_by_name`` is ``{name: frozenset}`` (self_approval.preparers_for).
    A ``name`` missing from it raises KeyError: it is never treated as
    owner-only.
    """
    items = []
    for doc in her:
        preparers = preparers_by_name[doc["name"]]
        mode = approve_mode(user, roles, preparers, "Historical Equity Rate", doc["name"],
                             policy, approver_roles, self_approval_problem)
        items.append(_her_item(doc, preparers, mode))
    for doc in ops:
        preparers = preparers_by_name[doc["name"]]
        mode = approve_mode(user, roles, preparers, "Ownership Period", doc["name"],
                             policy, approver_roles, self_approval_problem)
        items.append(_op_item(doc, preparers, mode))
    items.sort(key=lambda item: item["created"])
    return items
