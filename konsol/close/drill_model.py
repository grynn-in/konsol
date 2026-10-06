"""Drill model, pure: konsol/close/drill_model.py (konsol#305 Wave 4, N50;
stories 8.2 — line -> entity -> account -> source in three clicks or fewer;
D2-5 — eliminations, CTA, topsides, equity method and deal journals are
their own rows, never under an entity, regardless of ``data_area_id``;
konsolidat#245 — no dimension breakdown; W4-E6, W4-E9, W4-E11, W4-E12).

``drill(rows, journals, accounts, heading, keys, declared, allowed,
statement_line)`` breaks one statement heading's amount into layer rows.
Only the ``entity`` layer splits by entity; every other layer (eliminations,
CTA, top-side journals, equity method, acquisitions/disposals, and any
``adjustment_type`` this module does not recognise) is one row whatever
``data_area_id`` the warehouse happens to carry (D2-5). Each row lists its
accounts (lft order) and its source — a TB reference or the journals that
posted it.

Display sign is reused from ``statement_model``, never re-derived here
(#305-W4-2 2a-ii, AMENDED 4 Oct): a Balance Sheet heading's side comes from
``statement_model._bs_heading_sides`` (Main Account ``normal_balance`` —
Debit unflipped, Credit flipped; a blank one raises
``STATEMENT_HEADING_SIDE_UNDECLARED`` naming the heading, the same error the
statement itself raises — one sign rule, not two). Profit and Loss keeps the
same section-wide flip ``statement.py`` applies (income positive, costs
negative).

Pure: no frappe, no konsol imports. ``statement_model.py`` is a sibling
module loaded by path (as ``readiness_model.py`` loads ``signoff_model`` and
``close_policy_model``), never via a ``konsol.close`` import, so the hygiene
test (``test_module_imports_no_frappe``) still passes and this module keeps
loading stand-alone in its own test.
"""
import importlib.util
import os
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
_CENTS = Decimal("0.01")


def _load_sibling(name):
    spec = importlib.util.spec_from_file_location(
        "konsol_close_drill_model_" + name, os.path.join(_HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


statement_model = _load_sibling("statement_model")

#: Layers whose label merges several raw ``adjustment_type`` values into one
#: drill row (konsol#305 N50 facts; gold_fully_consolidated_tb.sql,
#: gold_consolidation_adjustments.sql, gold_business_combination_journal.sql,
#: gold_acquisition_adjustments.sql, gold_goodwill_amortisation_journal.sql,
#: gold_business_disposal_journal.sql, recon). ``entity`` and ``cta`` are
#: handled separately below (entity splits by entity; cta is forced to one
#: row). An ``adjustment_type`` absent from this map is its own row labelled
#: with the raw code (W4-E6: visible, never under an entity, never dropped).
_LAYER_LABELS = {
    "ic_elimination": "Intercompany eliminations",
    "ic_elimination_nci": "Intercompany eliminations",
    "topside": "Top-side journals",
    "reclassification": "Top-side journals",
    "auto_reversal": "Top-side journals",
    "equity_method": "Equity method",
    "acquisition": "Acquisitions and disposals",
    "pnl_proration": "Acquisitions and disposals",
    "goodwill_amortisation": "Acquisitions and disposals",
    "disposal": "Acquisitions and disposals",
}

#: The canonical ``layer`` key shown for a merged label (the row's own
#: identity, independent of which raw adjustment_type happened to post).
_LAYER_KEY_BY_LABEL = {
    "Intercompany eliminations": "ic_elimination",
    "Top-side journals": "topside",
    "Equity method": "equity_method",
    "Acquisitions and disposals": "acquisitions_and_disposals",
}

#: W4-E11: only these adjustment_types are backed by
#: gold_consolidation_adjustments, so only their merged row ("Top-side
#: journals") carries a ``journals`` list.
_TOPSIDE_LABEL = "Top-side journals"

_RESULT_LAYER_LABEL = "Current-year result (profit or loss to date)"

DIMENSIONS_NOTE = "Not broken down by dimension yet (konsolidat#245)."


def _dec(amount):
    return Decimal(str(amount))


def _round(amount):
    return float(amount.quantize(_CENTS))


def _heading_of(code, accounts):
    if not code:
        return None
    return (accounts.get(code) or {}).get("parent_account")


def _accounts_list(code_amounts, accounts, mult):
    ordered = sorted(code_amounts, key=lambda c: accounts[c]["lft"])
    return [
        {
            "main_account": code,
            "account_name": accounts[code].get("account_name"),
            "amount": _round(mult * code_amounts[code]),
        }
        for code in ordered
    ]


def _find_include(statement_line, kind, heading):
    for include in statement_line.get("includes") or []:
        if include.get("kind") == kind:
            return include["current"]
    raise ValueError(
        f"{heading}: statement_line has no {kind!r} include; the drill and "
        "the statement disagree about what this heading carries"
    )


def drill(rows, journals, accounts, heading, keys, declared, allowed, statement_line):
    """One statement heading's amount, broken into layer rows (konsol#305
    N50).

    ``rows`` are the entity-grain warehouse rows (``{"fiscal_year",
    "fiscal_period", "data_area_id", "main_account", "adjustment_type",
    "amount", "null_rows"}``, N52's read). ``journals`` are the selected
    period's ``gold_consolidation_adjustments`` rows (``{"journal_id",
    "adjustment_type", "data_area_id", "main_account", "net_amount",
    "description", "posted_by", "approved_by"}``). ``accounts`` and
    ``declared`` are ``statement_model.statement``'s inputs. ``keys`` is the
    N48 key set for this line's basis (the current key for Profit and Loss;
    every key <= current for the cumulative Balance Sheet). ``allowed`` is
    the caller's entity scope (``None`` = every entity visible).
    ``statement_line`` is the matching line from
    ``statement_model.statement(...)``: this drill's ``total`` must equal
    its ``"current"`` exactly, or this raises ``ValueError`` — a bug, never
    shown as a silent difference.

    Returns ``{"heading", "heading_name", "section", "rows", "total",
    "dimensions_note"}``. Each row in ``rows`` is ``{"layer", "label",
    "entity"|None, "amount", "accounts": [...], "source"}``; amounts are in
    the heading's display sign.
    """
    keys = set(keys)
    if not keys:
        raise ValueError(f"{heading}: no keys given to drill into")
    cur_key = max(keys)

    entry = accounts.get(heading) or {}
    section = entry.get("statement_section")
    if section == statement_model.BS:
        mult = statement_model._bs_heading_sides([heading], accounts)[heading]
    else:
        mult = -1

    cta_heading = _heading_of(declared.get("cta_account"), accounts)
    result_heading = _heading_of(declared.get("result_account"), accounts)

    entity_buckets = {}
    other_buckets = {}
    cta_amount = Decimal("0")

    for row in rows:
        key = (row["fiscal_year"], row["fiscal_period"])
        if key not in keys:
            continue

        code = row["main_account"]
        if row.get("null_rows"):
            raise ValueError(
                f"{code} {row['fiscal_year']}/{row['fiscal_period']}: "
                "has null rows, amount cannot be read as 0"
            )
        if row["amount"] is None:
            raise ValueError(
                f"{code} {row['fiscal_year']}/{row['fiscal_period']}: "
                "amount is None, cannot be read as 0"
            )
        amount = _dec(row["amount"])
        adjustment_type = row.get("adjustment_type")

        if adjustment_type == "cta":
            if heading != cta_heading:
                continue
            cta_amount += amount
            continue

        leaf = accounts.get(code)
        if leaf is None or leaf.get("is_group") or leaf.get("parent_account") != heading:
            continue

        if adjustment_type == "entity":
            bucket = entity_buckets.setdefault(
                row.get("data_area_id"), {"amount": Decimal("0"), "accounts": {}})
        else:
            slot = _LAYER_LABELS.get(adjustment_type, adjustment_type)
            bucket = other_buckets.setdefault(
                slot, {"amount": Decimal("0"), "accounts": {}})

        bucket["amount"] += amount
        bucket["accounts"][code] = bucket["accounts"].get(code, Decimal("0")) + amount

    out_rows = []

    # -- entity layer: split by entity, cut to scope (W4-E9) ---------------
    allowed_entities = None if allowed is None else set(allowed)
    outside_count = 0
    outside_amount = Decimal("0")
    for data_area_id in sorted(entity_buckets):
        bucket = entity_buckets[data_area_id]
        if allowed_entities is not None and data_area_id not in allowed_entities:
            outside_count += 1
            outside_amount += bucket["amount"]
            continue
        out_rows.append({
            "layer": "entity", "label": None, "entity": data_area_id,
            "amount": _round(mult * bucket["amount"]),
            "accounts": _accounts_list(bucket["accounts"], accounts, mult),
            "source": {
                "kind": "tb", "entity": data_area_id,
                "fiscal_year": cur_key[0], "fiscal_period": cur_key[1],
            },
        })

    if outside_count:
        out_rows.append({
            "layer": "entity", "label": "%d entities outside your scope" % outside_count,
            "entity": None, "amount": _round(mult * outside_amount),
            "accounts": [], "source": None,
        })

    # -- CTA: one row, never under an entity (D2-5) -------------------------
    if heading == cta_heading:
        out_rows.append({
            "layer": "cta", "label": "Currency translation (CTA)", "entity": None,
            "amount": _round(mult * cta_amount),
            "accounts": [],
            "source": {
                "kind": "tb", "entity": None,
                "fiscal_year": cur_key[0], "fiscal_period": cur_key[1],
            },
        })

    # -- every other layer: one row each, whatever data_area_id it carries --
    for slot in sorted(other_buckets):
        bucket = other_buckets[slot]
        row = {
            "layer": _LAYER_KEY_BY_LABEL.get(slot, slot),
            "label": slot, "entity": None,
            "amount": _round(mult * bucket["amount"]),
            "accounts": _accounts_list(bucket["accounts"], accounts, mult),
        }
        if slot == _TOPSIDE_LABEL:
            heading_codes = set(bucket["accounts"])
            row["journals"] = [
                {
                    "journal_id": journal["journal_id"],
                    "description": journal.get("description"),
                    "amount": _round(mult * _dec(journal["net_amount"])),
                    "posted_by": journal.get("posted_by"),
                    "approved_by": journal.get("approved_by"),
                }
                for journal in journals
                if journal.get("main_account") in heading_codes
            ]
            row["journals_basis"] = (
                "posted in this period (the heading's amount is cumulative)"
                if section == statement_model.BS else "posted in this period"
            )
            row["source"] = {"kind": "journals"}
        else:
            row["source"] = None
        out_rows.append(row)

    # -- current-year result: no accounts, amount from the statement line --
    result_amount = None
    if heading == result_heading:
        result_amount = _find_include(statement_line, "current_year_result", heading)
        out_rows.append({
            "layer": "current_year_result", "label": _RESULT_LAYER_LABEL,
            "entity": None,
            "amount": result_amount,
            "accounts": [], "source": None,
        })

    # -- compare at the raw level (S1, W4 server review) --------------------
    # Each display row above is rounded to cents on its own (one row per
    # entity/layer), so their sum can drift a cent or two from the true
    # total: the warehouse sums sub-cent tails (Float64) and
    # ``statement_model`` rounds its heading total only ONCE, from the same
    # raw rows (statement_model.py docstring; heading_amounts/statement).
    # Summing already-rounded rows and comparing THAT to the statement
    # line raised on almost every live heading for no real discrepancy.
    # The fix: sum the unrounded Decimal buckets (the same raw inputs
    # statement_model summed), quantize once, and compare that to the
    # statement line. ``result_amount`` is itself already the statement's
    # own once-rounded figure, so adding it in directly (not re-deriving
    # it) keeps the two sides built from the identical computation.
    entity_raw_total = sum(
        (bucket["amount"] for bucket in entity_buckets.values()), Decimal("0"))
    other_raw_total = sum(
        (bucket["amount"] for bucket in other_buckets.values()), Decimal("0"))
    raw_total = mult * (entity_raw_total + other_raw_total + cta_amount)
    if result_amount is not None:
        raw_total += _dec(result_amount)
    true_total = _round(raw_total)

    displayed_total = _round(sum((_dec(row["amount"]) for row in out_rows), Decimal("0")))
    if displayed_total != true_total:
        # The per-row rounding above lost (or gained) a few cents against
        # the true, once-rounded total. Show it, never hide it.
        out_rows.append({
            "layer": "rounding", "label": "Rounding", "entity": None,
            "amount": _round(_dec(true_total) - _dec(displayed_total)),
            "accounts": [], "source": None,
        })

    total = true_total
    expected = _round(_dec(statement_line["current"]))
    if total != expected:
        raise ValueError(
            f"{heading}: drill total {total} does not match the statement "
            f"line's current {expected} (a bug, never shown as a silent "
            "difference)"
        )

    return {
        "heading": heading,
        "heading_name": entry.get("account_name"),
        "section": section,
        "rows": out_rows,
        "total": total,
        "dimensions_note": DIMENSIONS_NOTE,
    }
