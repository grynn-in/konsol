"""Bulk trial balance files: one file, many entity-periods. Pure; host-tested.

A group with hundreds of entities sends one file a month, not hundreds. The
file is split into one ordinary Trial Balance Submission per entity and
period, so every rule a single submission obeys still applies: debits equal
credits, accounts are in the group chart, the period is open, there is one
live submission per entity-period, and the uploader may access the entity.

File contract (header required, case-insensitive; CSV, or the first sheet of
an .xlsx workbook):

    data_area_id, fiscal_year, fiscal_period, main_account, debit, credit[, description]

`entity`, `year`, `period` and `account` are accepted for the first four.
Amounts are in each entity's own accounting currency, one row per account,
debits and credits both positive (the same contract as a single upload).
"""
import csv
import io
import math

REQUIRED = ("data_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit")
ALIASES = {
    "entity": "data_area_id",
    "entity_id": "data_area_id",
    "company": "data_area_id",
    "year": "fiscal_year",
    "period": "fiscal_period",
    "account": "main_account",
    "account_id": "main_account",
}
#: Structural problems are reported together, up to this many lines.
MAX_LINE_ERRORS = 20


def cell(value):
    """A cell as text. Excel hands back 1000.0 for an account typed as 1000."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def table_from_csv(text):
    return list(csv.reader(io.StringIO(text)))


def _header_name(value):
    # Excel's "CSV UTF-8" puts a byte-order mark before the first header.
    name = cell(value).lstrip("\ufeff").lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(name, name)


def _amount(value, what, lineno, errors):
    if value is None or cell(value) == "":
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        try:
            number = float(cell(value))
        except ValueError:
            errors.append(f"Line {lineno}: {what} must be a number (got {cell(value)!r})")
            return 0.0
    if not math.isfinite(number):
        errors.append(f"Line {lineno}: {what} must be a finite number (got {cell(value)!r})")
        return 0.0
    # Cents, exactly as a single submission rounds them, so what is checked
    # here is what lands.
    return round(number, 2)


def _whole(value, what, lineno, errors):
    text = cell(value)
    try:
        return int(text)
    except ValueError:
        errors.append(f"Line {lineno}: {what} must be a whole number (got {text!r})")
        return None


def split_table(table):
    """Header + rows (lists of cell values) → {(entity, year, period): [rows]}.

    Keys keep the order they first appear in the file. Each row is
    {main_account, debit, credit, description}, the shape a single
    submission parses. Raises ValueError listing every structural problem
    (up to MAX_LINE_ERRORS lines) so a file can be fixed in one pass.
    """
    lines = [(i, row) for i, row in enumerate(table, start=1) if any(cell(c) for c in row)]
    if not lines:
        raise ValueError("The file is empty: expected a header row")
    head_line, head = lines[0]
    names = [_header_name(h) for h in head]
    missing = [c for c in REQUIRED if c not in names]
    if missing:
        raise ValueError(
            f"Missing column(s) {', '.join(missing)} on line {head_line}. The header must be "
            "data_area_id, fiscal_year, fiscal_period, main_account, debit, credit[, description]"
        )
    col = {n: names.index(n) for n in set(names)}

    groups, errors = {}, []
    for lineno, row in lines[1:]:
        if len(errors) >= MAX_LINE_ERRORS:
            break
        surplus = [c for c in row[len(names):] if cell(c)]
        if surplus:
            errors.append(f"Line {lineno}: more cells than the header has columns")
            continue

        def get(name):
            i = col.get(name)
            return row[i] if i is not None and i < len(row) else None

        entity = cell(get("data_area_id"))
        account = cell(get("main_account"))
        if not entity:
            errors.append(f"Line {lineno}: data_area_id is blank")
        if not account:
            errors.append(f"Line {lineno}: main_account is blank")
        year = _whole(get("fiscal_year"), "fiscal_year", lineno, errors)
        period = _whole(get("fiscal_period"), "fiscal_period", lineno, errors)
        debit = _amount(get("debit"), "debit", lineno, errors)
        credit = _amount(get("credit"), "credit", lineno, errors)
        if not entity or not account or year is None or period is None:
            continue
        groups.setdefault((entity, year, period), []).append({
            "main_account": account, "debit": debit, "credit": credit,
            "description": cell(get("description")),
        })

    if errors:
        more = " (and more)" if len(errors) >= MAX_LINE_ERRORS else ""
        raise ValueError("\n".join(errors) + more)
    if not groups:
        raise ValueError("The file has a header but no data rows")
    return groups


def group_csv(rows):
    """One entity-period as the single-submission CSV (main_account,debit,credit,description)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["main_account", "debit", "credit", "description"])
    for r in rows:
        writer.writerow([r["main_account"], f"{r['debit']:.2f}", f"{r['credit']:.2f}", r.get("description", "")])
    return out.getvalue()


def check_group(key, rows, *, known_accounts, visible, leaf, period_status, existing, validate_rows):
    """Everything that would stop this entity-period loading, as one report row.

    The facts come from the caller; `validate_rows` is the single-submission
    validator, so the bulk path can never accept what a single upload refuses.
    """
    entity, year, period = key
    errors = []
    if not visible:
        errors.append(f"Entity {entity} does not exist, or you have no access to it")
    elif not leaf:
        errors.append(f"{entity} is a group; trial balances belong to the entities under it")
    if not 1 <= period <= 12:
        errors.append("Fiscal period must be 1 to 12")
    elif period_status and period_status != "Open":
        errors.append(f"FY{year} P{period:02d} is {period_status.lower()}")
    if existing:
        errors.append(f"{existing} is already submitted for this entity and period; cancel or amend it first")
    errors.extend(validate_rows(rows, known_accounts=known_accounts))
    return {
        "entity": entity, "fiscal_year": year, "fiscal_period": period, "rows": len(rows),
        "total_debit": round(sum(r["debit"] for r in rows), 2),
        "total_credit": round(sum(r["credit"] for r in rows), 2),
        "errors": errors, "ok": not errors, "existing": existing,
    }


def merge_loaded(report, previous, upload_name=None):
    """Carry forward rows this upload loaded before.

    Re-checked, such a row is "already submitted" by its own submission: it
    is done, not a problem, and must never be loaded again. It is recognised
    by the name recorded in the previous report, or, when a load stopped
    before recording it, by the submission's file, which this upload names
    "<upload>-<entity>-<year>-P<period>.csv". A row this upload loaded that
    has since been cancelled becomes a problem: resuming must not quietly
    load old figures again.
    """
    loaded = {(p["entity"], p["fiscal_year"], p["fiscal_period"]): p["loaded"]
              for p in previous or [] if p.get("loaded")}
    prefix = f"{upload_name}-" if upload_name else None
    out = []
    for r in report:
        before = loaded.get((r["entity"], r["fiscal_year"], r["fiscal_period"]))
        existing = r.get("existing")
        ours = bool(existing) and (existing == before or (
            prefix and (r.get("existing_file") or "").rsplit("/", 1)[-1].startswith(prefix)))
        if ours:
            r = {**r, "ok": True, "errors": [], "loaded": existing}
        elif before and not existing:
            r = {**r, "ok": False,
                 "errors": [f"Loaded earlier as {before}, which has since been cancelled. "
                            "Upload the file again if it should be loaded anew."]}
        out.append(r)
    return out


def outcome(loaded, failed, ready):
    """The upload's final status after a load."""
    if ready and loaded == ready and not failed:
        return "Loaded"
    if loaded:
        return "Partly Loaded"
    return "Failed"
