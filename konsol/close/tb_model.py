"""TB check model: what is wrong with each line of a trial balance (konsol#305 A11, story 3.2).

Pure: imports no frappe, and is loaded by path in its tests. The input rows are
``parse_tb_csv`` output (trial_balance_submission.py), which carries the
physical CSV line each row came from as ``"line"`` (konsol#305 A38). Rows
without it (a caller's own fixtures) fall back to ``index + 2`` — the first
data row is line 2 when there is no blank line or multi-line quoted field
to drift it.

``check_rows`` reports per line, with a suggestion where one can be made, so a
screen can point at the line to fix. The rules and their wording mirror
``validate_tb_rows`` and ``chart_errors``, which report the same rules per file;
a parity test holds the two together until A35 rebuilds ``validate_tb_rows``
on top of this module (Problems 1, decision P1).
"""

import difflib
import importlib.util
import os

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_APP_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_basis = _load_sibling("konsol_close_tb_basis_model", "tb_basis_model.py")

#: sum(debit) and sum(credit) may differ by at most this much (currency units).
#: The caller passes the tolerance; this mirrors the controller's constant, and a
#: test asserts the two are equal.
BALANCE_TOLERANCE = 0.01

#: Float noise only, far below a cent: the balance comparison ignores differences
#: from the tolerance smaller than this (konsol#305 A60).
_FLOAT_EPSILON = 1e-9

#: konsol#182: a site with no Published Main Account has no chart to post to.
#: The same text as trial_balance_submission.NO_CHART (a test asserts it).
NO_CHART = ("No group chart is published yet: upload and publish one (Main Account) "
            "before submitting trial balances")

PARTNER = "partner_data_area_id"
BASIS = "amount_basis"

UNKNOWN_ACCOUNT = "UNKNOWN_ACCOUNT"
HEADING_ACCOUNT = "HEADING_ACCOUNT"
CLOSED_ACCOUNT = "CLOSED_ACCOUNT"
NEGATIVE_AMOUNT = "NEGATIVE_AMOUNT"
DUPLICATE_ROW = "DUPLICATE_ROW"
SELF_PARTNER = "SELF_PARTNER"
UNKNOWN_PARTNER = "UNKNOWN_PARTNER"
AMOUNT_BASIS = "AMOUNT_BASIS"


def _problem(code, message, suggestion=""):
    return {"code": code, "message": message, "suggestion": suggestion}


def _shared_prefix_len(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _closest_code(code, candidates, cutoff=0.6):
    """The nearest candidate to ``code``, breaking a difflib ratio tie.

    ``difflib.get_close_matches`` ranks by ``heapq.nlargest`` over
    ``(ratio, candidate)``, so a tie goes to the candidate that sorts last —
    measured live: 1001 -> "7100" although 1000 and 1010 are nearer codes.
    The declared tie-break (konsol#305 A43): highest ratio, then longest
    shared prefix, then smallest absolute numeric distance when both codes
    are numeric, then the lowest code.
    """
    best = None
    best_key = None
    for candidate in candidates:
        ratio = difflib.SequenceMatcher(None, code, candidate).ratio()
        if ratio < cutoff:
            continue
        prefix = _shared_prefix_len(code, candidate)
        if code.isdigit() and candidate.isdigit():
            numeric_distance = abs(int(code) - int(candidate))
        else:
            numeric_distance = float("inf")
        key = (-ratio, -prefix, numeric_distance, candidate)
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
    return best


def _account_problems(code, chart, posting_codes):
    account = chart.get(code)
    if account is None:
        match = _closest_code(code, posting_codes)
        return [_problem(UNKNOWN_ACCOUNT, f"Account {code} is not in the group chart",
                         f"Did you mean {match}?" if match else "")]
    if account.get("is_group"):
        return [_problem(HEADING_ACCOUNT,
                         f"{code} is a heading in the group chart; post to the accounts under it.",
                         "Post to the accounts under it")]
    if not account.get("is_posting"):
        return [_problem(CLOSED_ACCOUNT,
                         f"Account {code} is not open for posting in the group chart (is_posting is off)",
                         "Post to another account, or ask the Close Lead to open it.")]
    return []


def _negative_problems(row):
    out = []
    for column, opposite in (("debit", "credit"), ("credit", "debit")):
        if row[column] < 0:
            out.append(_problem(
                NEGATIVE_AMOUNT,
                f"Negative {column} — post the value to the opposite column instead of using a sign",
                f"Enter {abs(row[column]):,.2f} as a {opposite} instead",
            ))
    return out


def _partner_problems(partner, entity, known, by_upper):
    if not partner:
        return []
    if entity and partner.upper() == entity.upper():
        return [_problem(
            SELF_PARTNER,
            f"Partner is the entity itself ({entity}) — a partner is the other group entity; "
            "leave it blank for a third party",
            "Leave the partner blank for a third party",
        )]
    if known is not None and partner not in known:
        match = by_upper.get(partner.upper())
        return [_problem(
            UNKNOWN_PARTNER,
            f"Unknown partner entity: {partner} — a partner must be an existing entity "
            "that is not a group",
            f"Did you mean {match}?" if match else "",
        )]
    return []


def _basis_problems(form_basis, line, cell, form_level):
    if not cell:
        return []  # a blank cell is "not given" (the controller does the same)
    return [_problem(AMOUNT_BASIS, p, "Fix the form or the file: one file holds one amount basis")
            for p in _basis.basis_problems(form_basis, [(line, cell)]) if p not in form_level]


def check_rows(rows, chart, entity, known_entities, form_basis, tolerance):
    """Per-line problems, file problems and totals for parsed trial-balance rows.

    ``chart`` is ``group_chart.chart_accounts()`` ({code: {"is_group", "is_posting", …}});
    None or empty is the NO_CHART file problem and no account is judged.
    ``entity`` is the submitting entity (None skips the self-partner check);
    ``known_entities`` the non-group entities a partner may be (None skips it).
    ``form_basis`` is the declared amount basis. Returns
    ``{ok, rows:[{line, main_account, partner, debit, credit, problems}], file_problems, totals}``.
    """
    file_problems = []
    if not chart:
        file_problems.append(NO_CHART)
    posting_codes = sorted(c for c, a in (chart or {}).items()
                           if not a.get("is_group") and a.get("is_posting"))

    lines_by_key = {}
    for index, r in enumerate(rows):
        lines_by_key.setdefault((r["main_account"], r.get(PARTNER) or ""), []).append(
            r.get("line", index + 2))

    known = set(known_entities) if known_entities is not None else None
    by_upper = {e.upper(): e for e in (known or ())}
    form_level = _basis.basis_problems(form_basis, [])
    file_problems.extend(form_level)

    out_rows = []
    for index, r in enumerate(rows):
        line = r.get("line", index + 2)
        partner = r.get(PARTNER) or ""
        problems = []
        if chart:
            problems.extend(_account_problems(r["main_account"], chart, posting_codes))
        problems.extend(_negative_problems(r))
        others = [n for n in lines_by_key[(r["main_account"], partner)] if n != line]
        if others:
            label = f"{r['main_account']} (partner {partner})" if partner else r["main_account"]
            problems.append(_problem(
                DUPLICATE_ROW,
                f"Duplicate row for {label}: also on line {', '.join(str(n) for n in others)} — "
                "one row per account and partner",
                "Merge them into one row",
            ))
        problems.extend(_partner_problems(partner, entity, known, by_upper))
        problems.extend(_basis_problems(form_basis, line, r.get(BASIS) or "", form_level))
        out_rows.append({
            "line": line,
            "main_account": r["main_account"],
            "partner": partner,
            "debit": r["debit"],
            "credit": r["credit"],
            "problems": problems,
        })

    total_debit = sum(r["debit"] for r in rows)
    total_credit = sum(r["credit"] for r in rows)
    # Compare exactly, allowing only float noise: 100.01 - 100 is
    # 0.010000000000005, which is still one cent and within 0.01. Rounding to
    # cents first would loosen the rule (0.014 would pass 0.01), so it is not
    # done (konsol#305 A60).
    if abs(total_debit - total_credit) - tolerance > _FLOAT_EPSILON:
        file_problems.append(
            f"Debits ({total_debit:,.2f}) do not equal credits "
            f"({total_credit:,.2f}); difference "
            f"{total_debit - total_credit:,.2f} exceeds the "
            f"{tolerance} tolerance"
        )

    ok = not file_problems and not any(row["problems"] for row in out_rows)
    return {
        "ok": ok,
        "rows": out_rows,
        "file_problems": file_problems,
        "totals": {
            "debit": round(total_debit, 2),
            "credit": round(total_credit, 2),
            "difference": round(total_debit - total_credit, 2),
        },
    }


def is_on_behalf(roles, entity, assigned_subtree):
    """R4 (konsol#297, konsol#305 A18): was this TB uploaded on the entity's behalf?

    False only for an Entity Accountant uploading for an entity inside their
    assigned subtree; anyone else (EPM Admin, System Manager, an accountant
    outside their scope) uploads on the entity's behalf. The caller computes
    the subtree server-side; nothing here comes from the request.
    """
    return not ("Entity Accountant" in set(roles or ())
                and entity in set(assigned_subtree or ()))
