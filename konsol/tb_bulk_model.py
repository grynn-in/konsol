"""Bulk trial balance files: one file, many entity-periods. Pure; host-tested.

A group with hundreds of entities sends one file a month, not hundreds. The
file is split into one ordinary Trial Balance Submission per entity and
period, so every rule a single submission obeys still applies: debits equal
credits, accounts are in the group chart, the period is open, there is one
live submission per entity-period, and the uploader may access the entity.

File contract (header required, case-insensitive; CSV, or the first sheet of
an .xlsx workbook):

    data_area_id, fiscal_year, fiscal_period, main_account, debit, credit[, description][, partner_data_area_id][, amount_basis]

`entity`, `year`, `period` and `account` are accepted for the first four, and
`partner`, `partner_entity`, `partner_id` or `counterparty` for the partner.
Amounts are in each entity's own accounting currency, one row per account and
partner, debits and credits both positive (the same contract as a single
upload). The partner is the other group entity an intercompany row is held
with; it is optional (konsol#159).

amount_basis (konsolidat#199; `basis` and `amount basis` are accepted too)
says what each row's debit and credit are: this period's movements, the
year-to-date movements, or the closing balance at period end
(konsol.tb_basis_model). Every row of one entity-period must agree; a blank
cell is "not given". An entity-period without its own value takes the
upload's Amount Basis (resolve_basis); one with neither is refused, because
there is no default that does not guess.
"""
import csv
import io
import math

from konsol.tb_basis_model import ALIASES as BASIS_ALIASES, AMOUNT_BASES, COLUMN as BASIS, canonical
from konsol.tb_dimension_model import accepted_dimension_columns, dimension_problems, is_dimension_column

PARTNER = "partner_data_area_id"
REQUIRED = ("data_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit")
ALIASES = {
    "entity": "data_area_id",
    "entity_id": "data_area_id",
    "company": "data_area_id",
    "year": "fiscal_year",
    "period": "fiscal_period",
    "account": "main_account",
    "account_id": "main_account",
    # the single upload accepts the same spellings
    "partner": PARTNER,
    "partner_entity": PARTNER,
    "partner_id": PARTNER,
    "counterparty": PARTNER,
    # konsolidat#199: the single upload accepts the same spellings for the basis
    **{alias.replace(" ", "_"): BASIS for alias in BASIS_ALIASES},
}
#: Optional columns a file may carry, beyond REQUIRED.
OPTIONAL = ("description", PARTNER, BASIS)
#: Every header this contract accepts, AFTER alias resolution. A header
#: outside this set is refused by name rather than dropped (konsol#255).
ACCEPTED = frozenset(REQUIRED + OPTIONAL)
#: Structural problems are reported together, up to this many lines.
MAX_LINE_ERRORS = 20


def _allowed_bases():
    return ", ".join(f'"{basis}"' for basis in AMOUNT_BASES)


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


def split_table(table, declared_dimensions=()):
    """Header + rows (lists of cell values) → {(entity, year, period): [rows]}.

    Keys keep the order they first appear in the file. Each row is
    {main_account, debit, credit, description, partner_data_area_id,
    amount_basis}, the shape a single submission parses; amount_basis is the
    exact basis string, or '' when the file has no such column or the cell is
    blank. Every row of one entity-period that gives a basis must give the
    same one (group_basis reads it). Raises ValueError listing every
    structural problem (up to MAX_LINE_ERRORS lines) so a file can be fixed
    in one pass.

    `declared_dimensions` are the site's Dimension rows (dimension_name,
    status, in_trial_balance); the default, no dimensions, means a site that
    declares none and keeps every existing caller working. A dim_* column the
    site has Published and ticked in_trial_balance is accepted and lands on
    each row under its own name, as a string, '' when the cell is blank — a
    dimension is optional per row. Any other dim_* header is refused saying
    WHICH of declare / publish / tick is missing (konsol.tb_dimension_model),
    since the fix differs. This function stays pure: the caller does the
    looking up.
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
            "[, partner_data_area_id][, amount_basis]"
        )
    # An unrecognised header is refused, not ignored (konsol#255). This used
    # to check only that REQUIRED was present, so any other column was never
    # read and its values were dropped without a word — a file of cost centres
    # loaded clean and arrived with the cost centres gone. A loader that
    # silently discards what it was given is konsol#247 broken at the intake.
    # Blank names are skipped: Excel writes a trailing comma, which is not a
    # column.
    #
    # A dim_* header is the declared dimensions' business, not this set's, and
    # it is refused with its own sentence — declare it, publish it, or tick the
    # flag — because the generic "here is the accepted header" line does not
    # tell the reader which of the three to do. Both kinds of problem are
    # raised together, as the line errors below are, so one pass fixes the file.
    declared = list(declared_dimensions)
    accepted_dims = accepted_dimension_columns(declared)
    unknown = [n for n in names
               if n and n not in ACCEPTED and n not in accepted_dims
               and not is_dimension_column(n)]
    problems = []
    if unknown:
        problems.append(
            f"Unrecognised column(s) {', '.join(sorted(set(unknown)))} on line "
            f"{head_line}. The header may be "
            "data_area_id, fiscal_year, fiscal_period, main_account, debit, credit"
            "[, description][, partner_data_area_id][, amount_basis]"
        )
    problems.extend(dimension_problems([n for n in names if n not in accepted_dims], declared))
    if problems:
        raise ValueError("\n".join(problems))
    if names.count(PARTNER) > 1:
        raise ValueError(f"Two partner columns on line {head_line}: keep one")
    if names.count(BASIS) > 1:
        raise ValueError(f"Two amount_basis columns on line {head_line}: keep one of "
                         + ", ".join(BASIS_ALIASES))
    col = {n: names.index(n) for n in set(names)}
    #: The accepted dim_* columns this file actually carries, in header order.
    dim_names = [n for n in names if n in accepted_dims]

    groups, errors = {}, []
    first_basis = {}   # group key -> (lineno, basis) of the first row that gives one
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
        basis_cell = cell(get(BASIS))
        basis = canonical(basis_cell) if basis_cell else ""
        if basis_cell and basis is None:
            errors.append(f"Line {lineno}: amount_basis {basis_cell!r} is not one of {_allowed_bases()}")
            basis = ""
        if not entity or not account or year is None or period is None:
            continue
        key = (entity, year, period)
        if basis:
            first_line, first = first_basis.setdefault(key, (lineno, basis))
            if basis != first:
                errors.append(f'Line {lineno}: amount_basis "{basis}" but line {first_line} of the same '
                              f'entity-period says "{first}"; one entity-period holds one amount basis')
        groups.setdefault(key, []).append({
            "main_account": account, "debit": debit, "credit": credit,
            "description": cell(get("description")), PARTNER: cell(get(PARTNER)),
            BASIS: basis,
            **{d: cell(get(d)) for d in dim_names},
        })

    if errors:
        more = " (and more)" if len(errors) >= MAX_LINE_ERRORS else ""
        raise ValueError("\n".join(errors) + more)
    if not groups:
        raise ValueError("The file has a header but no data rows")
    return groups


def group_basis(rows):
    """The amount basis an entity-period's rows give, or '' when none does.

    split_table has already refused rows of one entity-period that disagree,
    so the first value given is the group's.
    """
    for r in rows:
        if r.get(BASIS):
            return r[BASIS]
    return ""


def resolve_basis(group_basis, form_basis):
    """(basis, problem) for one entity-period: the file's own value when it
    gives one, else the upload's Amount Basis; exactly one of the pair is None.

    Neither guesses: an entity-period with no basis anywhere is a problem
    sentence for that group's report row, and the load refuses it
    (konsolidat#199).
    """
    given = canonical(group_basis)
    if given:
        return given, None
    form = canonical(form_basis)
    if form:
        return form, None
    if form_basis and str(form_basis).strip():
        return None, f'Amount Basis "{str(form_basis).strip()}" is not one of {_allowed_bases()}.'
    return None, (
        "Amount Basis is required: the file gives none for this entity-period, so say on the upload "
        f"whether its rows are {_allowed_bases()}, or add an amount_basis column to the file."
    )


def group_csv(rows, source=None):
    """One entity-period as the single-submission CSV
    (main_account,debit,credit,description,partner_data_area_id[,amount_basis]).

    `source` (the upload's name) is written as an extra column, which the
    single-upload parser ignores. It records where the file came from, and
    it makes each upload's files unique: Frappe reuses an existing File with
    the same content, which would give this submission another upload's file.

    The amount_basis column is written only when the rows give one, so the
    generated file says what the uploaded file said and the submission's
    validate() confirms it against the form (konsolidat#199).

    Every dim_* column the rows carry is written after the existing ones,
    sorted so the output is deterministic. Without this the dimensions the
    bulk file declared would be dropped between the two parsers — this file
    is fed straight back into parse_tb_csv (konsol#255).
    """
    basis = group_basis(rows)
    dim_names = sorted({k for r in rows for k in r if is_dimension_column(k)})
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["main_account", "debit", "credit", "description", PARTNER]
                    + ([BASIS] if basis else []) + (["source_upload"] if source else [])
                    + dim_names)
    for r in rows:
        writer.writerow([r["main_account"], f"{r['debit']:.2f}", f"{r['credit']:.2f}", r.get("description", ""),
                         r.get(PARTNER, "")] + ([r.get(BASIS, "")] if basis else [])
                        + ([source] if source else [])
                        + [r.get(d, "") for d in dim_names])
    return out.getvalue()


def check_group(key, rows, *, known_accounts, visible, leaf, period, postable_types, existing, validate_rows,
                known_entities=None, warnings=(), partnerless_ic_rows=0):
    """Everything that would stop this entity-period loading, as one report row.

    The facts come from the caller; `validate_rows` is the single-submission
    validator, so the bulk path can never accept what a single upload refuses.
    `known_entities` are the entities a partner may name. `warnings` never stop
    a load (an intercompany row without a partner is allowed, and reported:
    `partnerless_ic_rows` counts them).

    `period` is None when the (year, period) is not a declared period, else a
    dict with at least `code`, `type` (Opening/Regular/Closing/Adjustment) and
    `status` (the effective status: Open/Closed/Locked). `postable_types` is
    the set of period types this site accepts trial balances for (Regular is
    always in it).
    """
    entity, year, period_no = key
    errors = []
    if not visible:
        errors.append(f"Entity {entity} does not exist, or you have no access to it")
    elif not leaf:
        errors.append(f"{entity} is a group; trial balances belong to the entities under it")
    if period is None:
        errors.append(f"FY{year} P{period_no} is not declared")
    elif period["type"] not in postable_types:
        errors.append(f"{period['code']} ({period['type']}) does not take trial balances on this site")
    elif period["status"] != "Open":
        errors.append(f"FY{year} P{period_no:02d} is {period['status'].lower()}")
    if existing:
        errors.append(f"{existing} is already submitted for this entity and period; cancel or amend it first")
    errors.extend(validate_rows(rows, known_accounts=known_accounts, entity=entity, known_entities=known_entities))
    return {
        "entity": entity, "fiscal_year": year, "fiscal_period": period_no, "rows": len(rows),
        "total_debit": round(sum(r["debit"] for r in rows), 2),
        "total_credit": round(sum(r["credit"] for r in rows), 2),
        "errors": errors, "ok": not errors, "existing": existing,
        "warnings": list(warnings), "partnerless_ic_rows": partnerless_ic_rows,
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
