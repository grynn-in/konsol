"""Dimension Value: the rules, with no frappe and no ClickHouse (konsol#287, 2A).

Decision 2A (Deepak Pai, 6 Nov 2026, konsol#287): one generic, read-only
virtual doctype, ``Dimension Value``, replaces Budget Cost Center, which
hard-coded one customer's dimension against the D365 silver table. A document
is one value of one declared Dimension, named ``<dimension>:<value>`` where
``<dimension>`` is the Dimension record's name. A site gates budget writes by
a dimension by pointing that Dimension's ``permission_doctype`` at
``Dimension Value`` and granting User Permissions on it; no code.

Rejected: a doctype per dimension (the shape Budget Cost Center had), which
puts a customer's dimension into konsol's code.

The controller (``epm/doctype/dimension_value``), the write gate
(``api._assert_dimension_access``) and the cut-over patch
(``patches/adopt_dimension_value``) hold the frappe and ClickHouse calls; the
decisions they make are here, so the host tests can pin them.
"""
import json
import re

DOCTYPE = "Dimension Value"
#: The doctype this one replaces. Read only by the cut-over patch.
RETIRED_DOCTYPE = "Budget Cost Center"
SEPARATOR = ":"

#: The warehouse tables a declared dimension's values are read from: the gold
#: trial balance (actuals, from every source) and the two budget input tables.
#: Each carries one column per dimension, named after the Dimension
#: (schema_apply adds them; dbt builds the gold trial balance from
#: ``var('dimensions')``). A dimension is read from every one of them that has
#: its column, so a value that exists only in actuals can still be granted for
#: a first budget.
VALUE_TABLES = (
    "epm_gold.gold_trial_balance",
    "epm_gold.budget_annual_input",
    "epm_gold.budget_monthly_input",
)

#: A dimension name that may be interpolated into SQL as a column. The same
#: shape as schema_apply._SAFE_IDENTIFIER, which created the column; a name
#: that fails is never put into a statement.
_SAFE_COLUMN = re.compile(r"[a-z][a-z0-9_]*")

#: The fields a Dimension Value has; a filter on anything else is refused.
FIELDS = ("name", "dimension", "value")


# --- naming --------------------------------------------------------------------

def make_name(dimension, value):
    return f"{dimension}{SEPARATOR}{value}"


def parse_name(name):
    """``(dimension, value)``, or None unless both halves are non-empty.

    Split on the first separator: a dimension name cannot hold one, a value can.
    """
    dimension, sep, value = (name or "").partition(SEPARATOR)
    if not sep or not dimension or not value:
        return None
    return dimension, value


def resolve_name(name, declared):
    """``(dimension, value)`` when ``name`` parses and its dimension is declared.

    None otherwise: a name without the separator, or on a dimension that is not
    declared, is not a Dimension Value. Never guessed onto another dimension.
    """
    parsed = parse_name(name)
    if parsed is None or parsed[0] not in set(declared):
        return None
    return parsed


# --- the declared set --------------------------------------------------------------

def declared_dimensions(rows):
    """Names of the Published Dimensions ticked in_budget or in_trial_balance, sorted."""
    return sorted(
        r["name"] for r in rows
        if r.get("status") == "Published"
        and (_on(r.get("in_budget")) or _on(r.get("in_trial_balance")))
    )


def _on(value):
    try:
        return int(value or 0) == 1
    except (TypeError, ValueError):
        return False


# --- the warehouse query ------------------------------------------------------------

def value_sources(declared, table_columns):
    """``[(dimension, table)]``: every value table that carries the dimension's column.

    ``table_columns`` is ``{table: set(column names)}`` as ClickHouse reports
    them. A dimension whose name is not a safe column is left out, never
    interpolated.
    """
    out = []
    for dimension in sorted(declared):
        if not _SAFE_COLUMN.fullmatch(dimension or ""):
            continue
        for table in sorted(table_columns):
            if dimension in table_columns[table]:
                out.append((dimension, table))
    return out


def values_sql(sources):
    """One query for the distinct non-blank (dimension, value) pairs, or None."""
    if not sources:
        return None
    parts = [
        f"SELECT '{dimension}' AS dimension, toString({dimension}) AS value FROM {table}"
        for dimension, table in sources
    ]
    return (
        "SELECT DISTINCT dimension, value FROM ("
        + " UNION ALL ".join(parts)
        + ") WHERE value != '' ORDER BY dimension, value FORMAT JSONCompactEachRow"
    )


def parse_rows(raw):
    """Rows ``{name, dimension, value}`` from the JSONCompactEachRow answer."""
    pairs = set()
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        dimension, value = json.loads(line)
        if dimension and value:
            pairs.add((dimension, value))
    return [{"name": make_name(d, v), "dimension": d, "value": v}
            for d, v in sorted(pairs)]


# --- list filtering ----------------------------------------------------------------

def _like(pattern, text):
    regex = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
                    for ch in str(pattern))
    return re.fullmatch(regex, str(text), re.IGNORECASE | re.DOTALL) is not None


def _match(row, field, op, value):
    if field not in FIELDS:
        raise ValueError(f"{DOCTYPE} has no field {field!r} to filter on")
    have = row[field]
    op = (op or "=").strip().lower()
    if op in ("=", "=="):
        return have == value
    if op == "!=":
        return have != value
    if op == "like":
        return _like(value, have)
    if op == "not like":
        return not _like(value, have)
    if op in ("in", "not in"):
        values = value.split(",") if isinstance(value, str) else list(value or [])
        values = [str(v).strip() for v in values]
        return (have in values) == (op == "in")
    raise ValueError(f"{DOCTYPE} does not support the filter operator {op!r}")


def _conditions(filters):
    """``[(field, op, value)]`` from a frappe filters dict or list."""
    if not filters:
        return []
    if isinstance(filters, dict):
        out = []
        for field, spec in filters.items():
            if isinstance(spec, (list, tuple)) and len(spec) == 2:
                out.append((field, spec[0], spec[1]))
            else:
                out.append((field, "=", spec))
        return out
    out = []
    for f in filters:
        if isinstance(f, dict):
            out.extend(_conditions(f))
        elif isinstance(f, (list, tuple)) and len(f) in (3, 4):
            field, op, value = f[-3:]
            out.append((field, op, value))
        else:
            raise ValueError(f"{DOCTYPE} cannot read the filter {f!r}")
    return out


def filter_rows(rows, filters=None, or_filters=None, txt=None):
    """Rows matching every filter, any or_filter, and ``txt`` in the name.

    An operator or field this doctype does not support is refused
    (ValueError), never ignored: an ignored filter would show rows the caller
    asked to exclude.
    """
    conds = _conditions(filters)
    ors = _conditions(or_filters)
    txt = (txt or "").strip().lower()
    out = []
    for row in rows:
        if not all(_match(row, *c) for c in conds):
            continue
        if ors and not any(_match(row, *c) for c in ors):
            continue
        if txt and txt not in row["name"].lower():
            continue
        out.append(row)
    return out


def page(rows, start=0, page_length=0):
    start = int(start or 0)
    page_length = int(page_length or 0)
    return rows[start:start + page_length] if page_length else rows[start:]


def _field_key(field):
    """``name`` from ``name``, ``value as label`` or ``\\`tabX\\`.\\`name\\```; else None."""
    if not isinstance(field, str):
        return None
    field = field.split(" as ", 1)[0].strip()
    field = field.rsplit(".", 1)[-1].strip("`").strip()
    return field if field in FIELDS else None


def pluck(rows, field):
    """One field of each row, for a caller that asked ``pluck``."""
    key = _field_key(field)
    if key is None:
        raise ValueError(f"{DOCTYPE} has no field {field!r} to pluck")
    return [row[key] for row in rows]


def as_list(rows, fields):
    """Rows as lists in ``fields`` order, for a caller that asked ``as_list``.

    A field this doctype does not have (the link search's computed relevance)
    is None.
    """
    keys = [_field_key(f) for f in (fields or ["name"])]
    return [[row[k] if k else None for k in keys] for row in rows]


# --- the budget write gate ------------------------------------------------------------

def allowed_values(dimension, permission_doctype, granted):
    """The values of ``dimension`` a user may write, from their granted names.

    ``granted`` is the set of User Permission values on ``permission_doctype``,
    or None for an unrestricted user. On ``Dimension Value`` a grant is
    ``<dimension>:<value>`` and allows that value on that dimension only: a
    grant on another dimension never allows. Any other permission doctype is
    returned unchanged, as before 2A.
    """
    if granted is None or permission_doctype != DOCTYPE:
        return granted
    out = set()
    for name in granted:
        parsed = parse_name(name)
        if parsed and parsed[0] == dimension:
            out.add(parsed[1])
    return out


# --- retiring Budget Cost Center ------------------------------------------------------------

def plan_retirement(dimensions, user_permissions, existing):
    """What the cut-over patch does, decided without touching anything.

    ``dimensions``: ``{name, permission_doctype}`` for every Dimension.
    ``user_permissions``: ``{name, user, for_value, applicable_for}`` for every
    User Permission whose ``allow`` is the retired doctype. ``existing``:
    ``{user, for_value, applicable_for}`` already on Dimension Value.

    The old gate (``api._assert_dimension_access``) compared a grant on the
    retired doctype with every Dimension whose ``permission_doctype`` named
    it, so a grant of V allowed V on each of them. The cut-over keeps exactly
    that: one old row becomes ``<d>:V`` for every such Dimension ``d``. The
    old row is rewritten to the first new grant it does not already have;
    copies are added for the rest.

    With no such Dimension, no dimension ever enforced the grant, so there is
    nothing to keep: the row is unmapped, never guessed onto a dimension. A
    blank value is unmapped too. Rejected: falling back to the Dimension whose
    ``allocation_role`` names the cost-center role. It names one customer's
    dimension in shipped code, which konsol#287 forbids
    (test_no_hardcoded_dimensions), and it would switch on grants that were
    never enforced.

    Returns ``{targets, repoint, rewrite, add, duplicate, unmapped, remove}``:
    ``targets`` the pointing Dimensions, sorted; ``repoint`` the Dimensions to
    point at Dimension Value (the same set); ``rewrite`` ``[(old row, new
    for_value)]``; ``add`` ``[(old row to copy, new for_value)]``;
    ``duplicate`` ``[(old row, for_value already granted)]``; ``unmapped``
    ``[(old row, reason)]``; ``remove`` the old rows to delete: every unmapped
    row, and every row whose grants all exist already.
    """
    targets = sorted(d["name"] for d in dimensions
                     if (d.get("permission_doctype") or "").strip() == RETIRED_DOCTYPE)
    have = {(e.get("user"), e.get("for_value"), e.get("applicable_for") or None)
            for e in existing}
    rewrite, add, duplicate, unmapped, remove = [], [], [], [], []
    for up in user_permissions:
        value = (up.get("for_value") or "").strip()
        if not targets:
            unmapped.append((up["name"], f"no Dimension had permission_doctype "
                                         f"'{RETIRED_DOCTYPE}', so no dimension "
                                         f"enforced this grant"))
            remove.append(up["name"])
            continue
        if not value:
            unmapped.append((up["name"], "blank for_value"))
            remove.append(up["name"])
            continue
        rewritten = False
        for dimension in targets:
            new = make_name(dimension, value)
            key = (up.get("user"), new, up.get("applicable_for") or None)
            if key in have:
                duplicate.append((up["name"], new))
                continue
            have.add(key)
            if rewritten:
                add.append((up["name"], new))
            else:
                rewrite.append((up["name"], new))
                rewritten = True
        if not rewritten:
            remove.append(up["name"])
    return {"targets": targets, "repoint": targets, "rewrite": rewrite, "add": add,
            "duplicate": duplicate, "unmapped": unmapped, "remove": remove}
