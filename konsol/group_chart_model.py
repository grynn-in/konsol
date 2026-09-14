"""The group chart of accounts (konsol#182): the rules, pure and host-tested.

No frappe import. The Main Account controller and the chart upload
(konsol.chart_upload) both decide here, so a rule cannot differ between the
form and the file.

Decided 13 Sep 2026: konsol defines the shape, and any source follows it. An
account is declared, not inferred: what it is (account_type), which statement
it belongs to (statement_section), how it is translated (fx_method) and whether
it accumulates (time_balance). The vocabularies are fixed.
"""
import csv
import io
import math

PL = "Profit and Loss"
BS = "Balance Sheet"
PUBLISHED = "Published"

ACCOUNT_TYPES = ("Asset", "Liability", "Equity", "Revenue", "Expense", "Balance sheet", "Profit and loss")
VOCAB = {
    "account_type": ACCOUNT_TYPES,
    "statement_section": (PL, BS),
    "normal_balance": ("Debit", "Credit"),
    "time_balance": ("flow", "balance"),
    "fx_method": ("closing", "average", "historical"),
    # the same options as Cash Flow Category; inert until PR5b
    "cf_category": ("Operating", "Investing", "Financing"),
    "status": ("Draft", PUBLISHED, "Inactive"),
    "source": ("Manual", "Upload"),
}
BS_TYPES = frozenset({"Asset", "Liability", "Equity", "Balance sheet"})
PL_TYPES = frozenset({"Revenue", "Expense", "Profit and loss"})
DEBIT_TYPES = frozenset({"Asset", "Expense", "Profit and loss", "Balance sheet"})
CREDIT_TYPES = frozenset({"Liability", "Equity", "Revenue"})
#: What a leaf needs before it is published, and what a group (heading) needs.
LEAF_DECLARATIONS = ("account_name", "chart_of_accounts", "account_type", "statement_section",
                     "normal_balance", "time_balance", "fx_method")
GROUP_DECLARATIONS = ("account_name", "chart_of_accounts")
#: Changing one of these on a Published account re-translates every period.
RECLASSIFYING = ("account_type", "statement_section", "fx_method")
#: Everything a person (or a file) declares about an account, besides its code.
DECLARED_FIELDS = ("account_name", "chart_of_accounts", "parent_account", "is_group", "account_type",
                   "statement_section", "sub_section", "normal_balance", "time_balance", "fx_method",
                   "is_posting", "is_suspended", "allow_ic", "main_account_category", "cf_category",
                   "cf_line_item", "is_cash", "description")
CHECK_FIELDS = ("is_group", "is_posting", "is_suspended", "allow_ic", "is_cash")


def text(value):
    """A cell or field as text. Excel hands back 1000.0 for a code typed 1000."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def flag(value):
    """A Check value as 0/1: None, '', '0', 'false', 0 and False are 0."""
    if isinstance(value, str):
        return 0 if value.strip().lower() in ("", "0", "false") else 1
    return 1 if value else 0


def code_problem(code):
    """Why a code cannot be an account code, or None. It is the join key in the
    warehouse, so blank, tabs and line breaks are refused."""
    if not code:
        return "the account code is blank"
    if any(c in code for c in "\t\r\n"):
        return f"the account code {code!r} contains a tab or a line break"
    return None


def _is_group(row):
    return bool(flag(row.get("is_group")))


# -- declarations -------------------------------------------------------------------

def apply_defaults(row):
    """A copy of ``row`` with what a declaration implies filled in, where blank.

    Leaves only (a heading is not posted to, so it is neither translated nor
    balanced): normal_balance from account_type; time_balance from
    statement_section; fx_method historical for Equity, else closing for the
    Balance Sheet and average for Profit and Loss (IAS 21).
    """
    out = dict(row)
    if _is_group(out):
        return out
    kind, section = out.get("account_type") or "", out.get("statement_section") or ""
    if not out.get("normal_balance"):
        if kind in DEBIT_TYPES:
            out["normal_balance"] = "Debit"
        elif kind in CREDIT_TYPES:
            out["normal_balance"] = "Credit"
    if not out.get("time_balance") and section:
        out["time_balance"] = "flow" if section == PL else "balance"
    if not out.get("fx_method"):
        if kind == "Equity":
            out["fx_method"] = "historical"
        elif section == BS:
            out["fx_method"] = "closing"
        elif section == PL:
            out["fx_method"] = "average"
    return out


def declaration_problems(row, parent=None):
    """Everything wrong with an account's declaration, as sentences. Empty
    means it may be saved (Draft or Published: these hold always).

    ``parent`` is the parent account's row when ``row`` names one, or None when
    that account does not exist.
    """
    code = text(row.get("main_account"))
    out = []
    problem = code_problem(code)
    if problem:
        out.append(problem[0].upper() + problem[1:])
    for field, allowed in VOCAB.items():
        value = row.get(field) or ""
        if value and value not in allowed:
            out.append(f"{code}: {field} {value!r} is not one of {', '.join(allowed)}")
    kind, section = row.get("account_type") or "", row.get("statement_section") or ""
    fx, time_balance = row.get("fx_method") or "", row.get("time_balance") or ""
    if _is_group(row):
        if flag(row.get("is_posting")):
            out.append(f"{code} is a heading (is_group), so it is never posted to: clear is_posting")
        if flag(row.get("allow_ic")):
            out.append(f"{code} is a heading (is_group), so it carries no intercompany rows: clear allow_ic")
    else:
        if kind and section:
            expected = BS if kind in BS_TYPES else PL if kind in PL_TYPES else None
            if expected and section != expected:
                out.append(f"{code}: an account of type {kind} belongs on the {expected}, not the {section}")
        if section == PL and fx == "historical":
            out.append(f"{code}: a Profit and Loss account is not translated at a historical rate; "
                       "use average (or closing, for hyperinflation)")
        if section == BS and fx == "average":
            out.append(f"{code}: a Balance Sheet account is not translated at the average rate; "
                       "use closing (or historical, for equity)")
        if section and time_balance:
            expected = "flow" if section == PL else "balance"
            if time_balance != expected:
                out.append(f"{code}: a {section} account's time_balance is {expected}, not {time_balance}")
    parent_code = text(row.get("parent_account"))
    if parent_code:
        if parent_code == code:
            out.append(f"{code} cannot be its own parent")
        elif parent is None:
            out.append(f"{code}: parent {parent_code} does not exist")
        else:
            if not _is_group(parent):
                out.append(f"{code}: parent {parent_code} is not a heading (is_group); "
                           "only a heading has accounts under it")
            if text(parent.get("chart_of_accounts")) != text(row.get("chart_of_accounts")):
                out.append(f"{code}: parent {parent_code} is in chart {text(parent.get('chart_of_accounts'))!r}, "
                           f"not {text(row.get('chart_of_accounts'))!r}")
            parent_section = parent.get("statement_section") or ""
            if parent_section and section and parent_section != section:
                out.append(f"{code} is on the {section} but its parent {parent_code} is on the {parent_section}")
    return out


def publish_problems(row, parent=None):
    """What stops an account being published: every declaration a leaf needs
    (a heading needs its name and chart), and a parent that is Published."""
    code = text(row.get("main_account"))
    needed = GROUP_DECLARATIONS if _is_group(row) else LEAF_DECLARATIONS
    missing = [f for f in needed if not text(row.get(f))]
    out = [f"{code} cannot be published without {', '.join(missing)}"] if missing else []
    parent_code = text(row.get("parent_account"))
    if parent_code and (parent is None or parent.get("status") != PUBLISHED):
        out.append(f"{code}: publish its parent {parent_code} first")
    return out


def in_use_problems(code, postings=(), intercompany=(), difference_groups=(), heading=False):
    """Why ``code`` cannot leave the group chart (unpublished, made Inactive or
    deleted), as sentences; empty when it may. For a heading, what holds the
    accounts under it.

    ``postings`` are (account, entity, fiscal_year, fiscal_period) of submitted
    trial balances. Their balances would drop out of both statements while
    translation still counts them, and the balance sheet would stop balancing.
    ``intercompany`` names Published Intercompany Accounts (either side),
    ``difference_groups`` the Consolidation Groups that book intercompany
    differences to it.
    """
    what = f"{code} (a heading: the accounts under it)" if heading else code
    out = []
    if postings:
        by_account = {}
        for account, entity, year, period in sorted(set(postings)):
            by_account.setdefault(account, []).append(f"{entity} FY{year} P{int(period):02d}")
        listed = "; ".join(f"{a}: {', '.join(v[:10])}{', …' if len(v) > 10 else ''}"
                           for a, v in by_account.items())
        out.append(f"{what} cannot leave the group chart while submitted trial balances post to it ({listed}). "
                   "Their balances would drop out of both statements while translation still counts them, and "
                   "the balance sheet would stop balancing. Correct those trial balances (cancel and amend, "
                   "while the period is open), or reclassify the account instead.")
    if intercompany:
        out.append(f"{what} cannot leave the group chart while Published Intercompany Accounts name it "
                   f"({', '.join(sorted(intercompany))}): make them Inactive first.")
    if difference_groups:
        out.append(f"{what} cannot leave the group chart while Consolidation Groups book intercompany "
                   f"differences to it ({', '.join(sorted(difference_groups))}): choose another difference "
                   "account first.")
    return out


def reclassified(before, after):
    """The reclassifying fields that differ between two versions of a row."""
    return [f for f in RECLASSIFYING if (before.get(f) or "") != (after.get(f) or "")]


def cash_flow_mapping(row):
    """The account's cash-flow mapping, or None when it has none.

    The chart is the source of the cash-flow mapping: a Balance Sheet leaf that
    declares cf_category and cf_line_item is mapped, and Cash Flow Category rows
    mirror it (konsol#196). A heading, a Profit and Loss account, or a leaf with
    either cf field blank is not mapped.
    """
    if _is_group(row) or row.get("statement_section") != BS:
        return None
    category, line_item = text(row.get("cf_category")), text(row.get("cf_line_item"))
    if not category or not line_item:
        return None
    return {"main_account": text(row.get("main_account")), "cf_category": category,
            "cf_line_item": line_item, "is_cash": 1 if flag(row.get("is_cash")) else 0}


# -- the chart file -------------------------------------------------------------------

#: The chart file's header, case-insensitive. Only the first three must be
#: there; every other column, when present, is read, and when absent is left
#: as it is on an existing account.
HEADER = ("main_account", "account_name", "account_type", "statement_section", "sub_section",
          "normal_balance", "time_balance", "fx_method", "cf_category", "cf_line_item", "is_posting",
          "allow_ic", "parent_account", "chart_of_accounts")
OPTIONAL = ("is_group", "is_suspended", "is_cash", "main_account_category", "description")
REQUIRED = ("main_account", "account_name", "chart_of_accounts")
HEADER_ALIASES = {
    "account": "main_account", "code": "main_account",
    "name": "account_name",
    "parent": "parent_account",
    "chart": "chart_of_accounts", "coa": "chart_of_accounts",
    "section": "statement_section",
    "translation_method": "fx_method",
}
VALUE_ALIASES = {
    "statement_section": {"p&l": PL, "pl": PL, "pnl": PL, "income statement": PL, "bs": BS},
    "fx_method": {"close": "closing", "cr": "closing", "closing rate": "closing",
                  "avg": "average", "ar": "average", "average rate": "average",
                  "historic": "historical", "hist": "historical", "hr": "historical",
                  "historical rate": "historical"},
    "normal_balance": {"dr": "Debit", "d": "Debit", "cr": "Credit", "c": "Credit"},
    "time_balance": {"period": "flow", "movement": "flow", "pit": "balance", "point in time": "balance"},
    "account_type": {"income": "Revenue", "sales": "Revenue",
                     "cogs": "Expense", "cost of goods sold": "Expense", "expenses": "Expense",
                     "capital": "Equity", "stockholders equity": "Equity"},
}
TRUE = frozenset({"y", "yes", "true", "1", "x"})
FALSE = frozenset({"n", "no", "false", "0", ""})
#: Line problems are reported together, up to this many (tb_bulk_model's convention).
MAX_LINE_ERRORS = 20


def table_from_csv(content):
    return list(csv.reader(io.StringIO(content)))


def _header_name(value):
    name = text(value).lstrip("﻿").lower().replace(" ", "_").replace("-", "_")
    return HEADER_ALIASES.get(name, name)


def _key(value):
    return " ".join(text(value).lower().replace("'", "").split())


def canonical(field, value):
    """A cell as its vocabulary value ('' when blank), or None when it is not one."""
    key = _key(value)
    if not key:
        return ""
    for option in VOCAB[field]:
        if option.lower() == key:
            return option
    return VALUE_ALIASES.get(field, {}).get(key)


def boolean(value):
    """y/yes/true/1/x -> 1, n/no/false/0 -> 0, blank -> None (the default), else raises."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and not (isinstance(value, float) and math.isnan(value)):
        if value in (0, 1):
            return int(value)
        raise ValueError(value)
    key = _key(value)
    if key == "":
        return None
    if key in TRUE:
        return 1
    if key in FALSE:
        return 0
    raise ValueError(value)


def parse_chart_table(table):
    """Header + rows (lists of cells) -> [row], one dict per account, in file order.

    Each row carries main_account, the columns the file has, is_group (always),
    and ``_line``. Checks everything one file can say about itself, and raises
    ValueError listing every problem (up to MAX_LINE_ERRORS lines) so the file
    is fixed in one pass: values outside the vocabularies, booleans, blank
    required cells, duplicate codes, more than one chart code, and a row other
    rows put accounts under that is not a heading. What depends on the accounts
    already in konsol (unknown parents, cycles, published changes) is
    plan_chart_load's.
    """
    lines = [(i, row) for i, row in enumerate(table, start=1) if any(text(c) for c in row)]
    if not lines:
        raise ValueError("The file is empty: expected a header row")
    head_line, head = lines[0]
    names = [_header_name(h) for h in head]
    known = set(HEADER) | set(OPTIONAL)
    problems = []
    unknown = [n for n in names if n and n not in known]
    if unknown:
        problems.append(f"Line {head_line}: unknown column(s) {', '.join(unknown)}. The columns are "
                        f"{', '.join(HEADER)} and optionally {', '.join(OPTIONAL)}")
    twice = sorted({n for n in names if n and names.count(n) > 1})
    if twice:
        problems.append(f"Line {head_line}: column(s) {', '.join(twice)} appear more than once")
    missing = [c for c in REQUIRED if c not in names]
    if missing:
        problems.append(f"Line {head_line}: missing column(s) {', '.join(missing)}")
    if problems:
        raise ValueError("\n".join(problems))
    col = {n: names.index(n) for n in set(names) if n}
    present = [n for n in HEADER + OPTIONAL if n in col]

    rows, errors = [], []
    for lineno, cells in lines[1:]:
        def get(name):
            i = col.get(name)
            return cells[i] if i is not None and i < len(cells) else None

        if [c for c in cells[len(names):] if text(c)]:
            errors.append(f"Line {lineno}: more cells than the header has columns")
            continue
        row = {"_line": lineno}
        for name in present:
            value = get(name)
            if name in VOCAB:
                parsed = canonical(name, value)
                if parsed is None:
                    errors.append(f"Line {lineno}: {name} {text(value)!r} is not one of "
                                  f"{', '.join(VOCAB[name])}")
                    continue
                row[name] = parsed
            elif name in CHECK_FIELDS:
                try:
                    row[name] = boolean(value)
                except ValueError:
                    errors.append(f"Line {lineno}: {name} {text(value)!r} is not yes or no")
            else:
                row[name] = text(value)
        for name in REQUIRED:
            if name not in row:
                continue
            if not row[name]:
                errors.append(f"Line {lineno}: {name} is blank")
        problem = code_problem(row.get("main_account", "")) if row.get("main_account") else None
        if problem:
            errors.append(f"Line {lineno}: {problem}")
        rows.append(row)

    lines_of = {}
    for r in rows:
        if r.get("main_account"):
            lines_of.setdefault(r["main_account"], []).append(r["_line"])
    for code, at in lines_of.items():
        if len(at) > 1:
            errors.append(f"{code} appears on lines {', '.join(map(str, at))}: one row per account")
    charts = sorted({r["chart_of_accounts"] for r in rows if r.get("chart_of_accounts")})
    if len(charts) > 1:
        errors.append(f"One chart per file: this file names {', '.join(charts)}")

    # A heading is a row other rows are under. With no is_group column that is
    # the definition; with one, a row that has accounts under it must say so.
    parents = {r.get("parent_account") for r in rows if r.get("parent_account")}
    for r in rows:
        named = r.get("main_account") in parents
        given = r.get("is_group")
        if given is None:
            r["is_group"] = 1 if named else 0
            r["_is_group_inferred"] = True
        elif named and not given:
            errors.append(f"Line {r['_line']}: {r['main_account']} has accounts under it, so it must be "
                          "a heading (is_group)")
        # a blank is_posting is the default: a leaf is posted to, a heading is not
        if "is_posting" in r and r["is_posting"] is None:
            r["is_posting"] = 0 if r["is_group"] else 1
        for name in CHECK_FIELDS:
            if name != "is_posting" and name in r and r[name] is None:
                r[name] = 0

    if errors:
        more = f" (and {len(errors) - MAX_LINE_ERRORS} more)" if len(errors) > MAX_LINE_ERRORS else ""
        raise ValueError("\n".join(errors[:MAX_LINE_ERRORS]) + more)
    if not rows:
        raise ValueError("The file has a header but no accounts")
    return rows


def topological(rows):
    """Codes parent-first: every account after the account it is under, when
    that one is in ``rows`` too; otherwise file order. Raises ValueError naming
    the accounts of any cycle."""
    by_code = {r["main_account"]: r for r in rows}
    order, state = [], {}

    for start in by_code:
        path = []
        code = start
        while code in by_code and state.get(code) != "done":
            if state.get(code) == "on_path":
                cycle = path[path.index(code):]
                raise ValueError(f"These accounts are under each other in a circle: {' -> '.join(cycle + [code])}")
            state[code] = "on_path"
            path.append(code)
            code = by_code[code].get("parent_account") or None
        for c in reversed(path):
            state[c] = "done"
            order.append(c)
    return order


def _normalised(field, value):
    if field in CHECK_FIELDS:
        return flag(value)
    return text(value)


def plan_chart_load(rows, existing):
    """What loading a parsed chart file would do. Writes nothing.

    ``existing`` is {code: row} of every Main Account (status included). The
    rules:

    * a new code is inserted as a Draft; an existing Draft (or Inactive) row is
      updated and keeps its status;
    * an existing Published row is updated in place and stays Published; its
      changes are listed under ``published_changes`` and must leave it
      publishable;
    * a code in konsol but not in the file is listed under ``not_in_file``,
      never deleted;
    * every account's declaration is checked as it would be after the load
      (the file over what konsol holds), parents and cycles included, and every
      problem is reported: ``ok`` is False and nothing may be written.

    ``writes`` is the load itself, parent-first: [(action, code, fields)] with
    action "insert" or "update" and only the fields the file sets.
    """
    errors = []
    chart = next((r["chart_of_accounts"] for r in rows if r.get("chart_of_accounts")), "")
    given = {}
    for r in rows:
        fields = {k: v for k, v in r.items() if not k.startswith("_") and k != "main_account"}
        before = existing.get(r["main_account"])
        if r.get("_is_group_inferred") and before and flag(before.get("is_group")):
            # no is_group column: a heading the file lists no accounts under stays one
            fields["is_group"] = 1
        if "is_posting" not in fields and (before is None
                                           or flag(before.get("is_group")) != flag(fields.get("is_group"))):
            # new, or turned into (or out of) a heading: a heading is not posted to, a leaf is
            fields["is_posting"] = 0 if flag(fields.get("is_group")) else 1
        given[r["main_account"]] = fields

    # the chart as it would be after the load
    after = {c: dict(v) for c, v in existing.items()}
    for code, fields in given.items():
        base = after.get(code) or {"main_account": code, "status": "Draft"}
        after[code] = {**base, **fields, "main_account": code}

    try:
        order = topological([{"main_account": c, **f} for c, f in given.items()])
    except ValueError:
        order = list(given)   # reported below, with circles through konsol's accounts
    circles = set()
    for code in given:
        chain, cur = [code], text(after[code].get("parent_account"))
        while cur and cur in after and cur not in chain:
            chain.append(cur)
            cur = text(after[cur].get("parent_account"))
        if cur == code and len(chain) > 1 and frozenset(chain) not in circles:
            circles.add(frozenset(chain))
            errors.append(f"These accounts are under each other in a circle: {' -> '.join(chain + [code])}")

    report = {"chart_of_accounts": chart, "rows": len(rows), "insert": [], "update": [],
              "published_changes": [], "inactive": [], "unchanged": [], "not_ready": [],
              "not_in_file": sorted(c for c, r in existing.items()
                                    if text(r.get("chart_of_accounts")) == chart and c not in given)}
    writes = []
    for code in order:
        row = apply_defaults(after[code])
        parent_code = text(row.get("parent_account"))
        parent = apply_defaults(after[parent_code]) if parent_code in after else None
        errors.extend(declaration_problems(row, parent))
        before = existing.get(code)
        if before is None:
            report["insert"].append(code)
            writes.append(("insert", code, given[code]))
        else:
            old = apply_defaults(before)
            changed = {f: [_normalised(f, old.get(f)), _normalised(f, row.get(f))] for f in DECLARED_FIELDS
                       if _normalised(f, old.get(f)) != _normalised(f, row.get(f))}
            if not changed:
                report["unchanged"].append(code)
                continue
            writes.append(("update", code, given[code]))
            status = before.get("status")
            if status == PUBLISHED:
                report["published_changes"].append({"main_account": code, "fields": changed})
                errors.extend(publish_problems(row, parent))
            elif status == "Inactive":
                report["inactive"].append(code)
            else:
                report["update"].append(code)
        if row.get("status") != PUBLISHED:
            # a Draft parent of this chart is published with it, parent first
            ready_parent = dict(parent, status=PUBLISHED) if parent and parent.get("status") == "Draft" \
                and text(parent.get("chart_of_accounts")) == chart else parent
            problems = publish_problems(row, ready_parent)
            if problems:
                report["not_ready"].append({"main_account": code, "problems": problems})
    # Accounts konsol holds under an account the file changes, and not in the
    # file themselves: re-checked against their parent as it would be. A
    # heading moved to another chart, onto the other statement, or turned
    # into a leaf would otherwise leave them in a tree they no longer fit.
    for code, row in existing.items():
        parent_code = text(row.get("parent_account"))
        if code in given or parent_code not in given:
            continue
        errors.extend(declaration_problems(apply_defaults(after[code]), apply_defaults(after[parent_code])))
    report["errors"] = errors
    report["ok"] = not errors
    report["writes"] = writes if not errors else []
    return report
