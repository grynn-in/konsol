"""EPM API — Frappe proxy to ClickHouse for Excel batch retrieval.

Also provides consolidation workflow APIs (PRD-8, PRD-16).
"""
import hmac
import json
import re
from collections import defaultdict
from datetime import date

import frappe
import requests
from frappe.utils import now_datetime

from konsol.clickhouse import connection_url as _ch_url
from konsol.clickhouse import get_connection as _get_ch_connection
from konsol import dimension_value_model
from konsol import tb_coverage
from konsol.measure_registry import aggregation_for
from konsol.period_read_model import combine_periods, resolve_period, sql_aggregate

MAX_BATCH_SIZE = 2000

# Entity access now runs through konsol.entity_permissions, which uses the
# Entity DocType directly. The old EPM Settings.entity_permission_doctype
# indirection existed only because there was nothing real to point at, and it
# meant the whole feature was off unless someone set it (#91).

# Period ranges used to be a constant here: Q1-Q4 as calendar thirds, FY as
# 1-12. They are now read from the customer's declared calendar instead
# (`declared_calendar` / konsol/period_read_model.py, konsol#251) — the
# constant could not express a 13-period year, and its 1-12 clamp made the
# year-end close unreadable.

VALID_LAYERS = {"base", "challenge", "management", "board"}

_SAFE_IDENTIFIER = re.compile(r'^[a-z][a-z0-9_]*$')
_SAFE_SCENARIO_ID = re.compile(r'^[A-Za-z0-9_]+$')
# Fully-qualified ClickHouse table: schema.table, lowercase identifiers only.
_SAFE_TABLE_NAME = re.compile(r'^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$')


# ---------------------------------------------------------------------------
# Entity-level authorization
# ---------------------------------------------------------------------------

def _resolve_allowed_entities(user, roles, perm_doctype, user_permissions):
    """Pure policy, kept for the tests that cover it without a site.

    Superseded by konsol.entity_permissions.allowed_entity_codes, which adds
    the two things this could not do: it targets Entity directly rather than a
    configurable doctype, and it expands an assignment down the tree.
    """
    if user == "Administrator" or "System Manager" in (roles or []):
        return None
    if not perm_doctype:
        return None
    entries = (user_permissions or {}).get(perm_doctype) or []
    return {e.get("doc") for e in entries if e.get("doc")}


def _allowed_entities():
    """The current user's entity allow-list, or None when unrestricted."""
    from konsol.entity_permissions import allowed_entity_codes

    return allowed_entity_codes()


def _assert_entity_access(entity):
    """Raise PermissionError if the current user may not query ``entity``.

    Still required alongside the permission_query_conditions hooks: those
    constrain Frappe queries, and these read paths go straight to ClickHouse
    where Frappe's permission layer never runs.
    """
    from konsol.entity_permissions import assert_entity_access

    assert_entity_access(entity)


# ---------------------------------------------------------------------------
# Budget write authorization (spec #51 §B) — entity AND account AND dimensions.
# All checks no-op when unconfigured (backwards compatible); they reuse the
# entity-permission policy (_resolve_allowed_entities) against different
# permission-target doctypes, so ownership is expressed with native Frappe User
# Permissions. Enforced in explicit code (not has_permission hooks) — required
# for the Virtual DocType targets, which get no automatic SQL filtering.
# ---------------------------------------------------------------------------

# main account -> responsibility category, sourced live from the dbt silver
# layer and cached (the write path must never round-trip ClickHouse per cell).
_ACCOUNT_CATEGORY_TABLE = "epm_silver.silver_main_accounts"
_ACCOUNT_CATEGORY_CACHE_KEY = "budget_account_category_map"
_ACCOUNT_CATEGORY_TTL = 300  # seconds

# The declared fiscal calendar, per year. Read once per request-ish rather
# than once per Excel cell (konsol#251).
_CALENDAR_CACHE_KEY = "epm_declared_calendar"
_CALENDAR_TTL = 300  # seconds


def _account_permission_doctype():
    return (frappe.get_cached_value(
        "EPM Settings", "EPM Settings", "account_permission_doctype") or "").strip()


def _load_account_category_map():
    """{main_account_id: main_account_category} from ClickHouse (silver layer)."""
    from konsol.clickhouse import execute

    raw = execute(
        f"SELECT main_account_id, main_account_category "
        f"FROM {_ACCOUNT_CATEGORY_TABLE} FORMAT TabSeparated"
    )
    mapping = {}
    for line in (raw or "").splitlines():
        if not line:
            continue
        acct, _, cat = line.partition("\t")
        mapping[acct] = cat
    return mapping


def _account_category(account):
    """Resolve a main account to its responsibility category (cached, TTL).

    Note: the map is cached for ``_ACCOUNT_CATEGORY_TTL`` with no active
    invalidation, so a newly-created account or a re-categorisation in the
    silver layer is invisible to gated writers for up to that window.
    """
    cache = frappe.cache()
    mapping = cache.get_value(_ACCOUNT_CATEGORY_CACHE_KEY)
    if mapping is None:
        try:
            mapping = _load_account_category_map()
        except Exception:  # noqa: BLE001 - warehouse blip must not 500 the write
            frappe.log_error("budget account-category load failed", frappe.get_traceback())
            # Enforcement is configured but the category source is unreachable.
            # Fail with a clear, retryable message rather than a raw 500 — and
            # do NOT cache the failure, so the next request retries.
            frappe.throw(
                frappe._("Account permission check is temporarily unavailable "
                         "(category source unreachable). Please retry shortly."),
                frappe.ValidationError,
            )
        cache.set_value(_ACCOUNT_CATEGORY_CACHE_KEY, mapping,
                        expires_in_sec=_ACCOUNT_CATEGORY_TTL)
    return mapping.get(account)


def _assert_account_access(account):
    """Raise PermissionError if the user may not write ``account``.

    No-op unless ``EPM Settings.account_permission_doctype`` is configured.
    Gates by the account's *category* (responsibility group). Fail-closed: if a
    category can't be resolved while enforcement is on, the write is denied.
    """
    perm_doctype = _account_permission_doctype()
    if not perm_doctype:
        return
    allowed = _resolve_allowed_entities(
        frappe.session.user, frappe.get_roles(), perm_doctype,
        frappe.permissions.get_user_permissions(frappe.session.user))
    if allowed is None:  # System Manager / Administrator / no grants configured
        return
    category = _account_category(account)
    if category is None or category not in allowed:
        detail = f"category '{category}'" if category else "no category mapping"
        raise frappe.PermissionError(
            f"Not permitted to write account '{account}' ({detail})")


def _permission_controlled_dimensions():
    """{dimension_name: permission_doctype} for in_budget dims that gate writes."""
    rows = frappe.get_all(
        "Dimension",
        filters={"in_budget": 1, "status": "Published"},
        fields=["dimension_name", "permission_doctype"],
        limit_page_length=0,
    )
    return {r.dimension_name: r.permission_doctype.strip()
            for r in rows if (r.permission_doctype or "").strip()}


def _assert_dimension_access(data):
    """Raise PermissionError for any permission-controlled dimension whose
    submitted value the user does not own.

    Unlike accounts, the dimension *value* is itself the permission unit (e.g.
    a cost center), so no resolution/ClickHouse lookup is needed.

    On ``Dimension Value`` (konsol#287 decision 2A) a grant is
    ``<dimension>:<value>`` and allows that value on that dimension only; a
    grant on another dimension never allows. Other permission doctypes compare
    the grant to the value directly, as before.
    """
    controlled = _permission_controlled_dimensions()
    if not controlled:
        return
    user, roles = frappe.session.user, frappe.get_roles()
    perms = frappe.permissions.get_user_permissions(user)
    for dim, perm_doctype in controlled.items():
        allowed = dimension_value_model.allowed_values(
            dim, perm_doctype,
            _resolve_allowed_entities(user, roles, perm_doctype, perms))
        if allowed is None:
            continue
        value = data.get(dim) or ""
        if value not in allowed:
            raise frappe.PermissionError(
                f"Not permitted to write {dim} '{value}'")


def _assert_budget_write_access(data):
    """Compose the budget write checks: entity (LE) AND account AND dimensions.

    Called by every budget write path before mutating. Each sub-check is a
    no-op until its permission target is configured.
    """
    _assert_entity_access(data.get("data_area_id"))
    _assert_account_access(data.get("main_account"))
    _assert_dimension_access(data)


# ---------------------------------------------------------------------------
# Dataset registry helpers
# ---------------------------------------------------------------------------

_FACT_FIELDS = [
    "fact_name", "scenario_key", "clickhouse_table", "has_scenario_id",
    "has_layer",
    "measures", "dimensions", "default_measure",
    "reroute_table", "reroute_column", "reroute_measure",
]


#: Datasets the warehouse keeps one set of rows per budget scenario, and the
#: column that says which. A read filters on it, never sums across budget
#: scenarios (konsol#214).
_BUDGET_SCENARIO_COLUMN = {"variance_analysis": "budget_scenario_id"}


def _get_fact_by_scenario(scenario):
    """Load Dataset doc by scenario_key. Returns dict or None."""
    facts = frappe.get_all(
        "Dataset",
        filters={"scenario_key": scenario},
        fields=_FACT_FIELDS,
        limit=1,
    )
    return facts[0] if facts else None


def _get_fact(fact=None, scenario=None):
    """Resolve a Dataset. `fact` (fact_name) wins over `scenario`.

    fact_name match is case-insensitive (names are stored normalized lowercase).
    If both are supplied, fact wins and a warning is logged. Returns dict or None.
    """
    if fact:
        if scenario and scenario != "actuals":
            frappe.logger().warning(
                f"epm: both fact='{fact}' and scenario='{scenario}' given; fact wins"
            )
        facts = frappe.get_all(
            "Dataset",
            filters={"fact_name": (fact or "").lower()},
            fields=_FACT_FIELDS,
            limit=1,
        )
        return facts[0] if facts else None
    return _get_fact_by_scenario(scenario)


def _get_allowed_measures(fact):
    """Parse measures JSON from a Dataset doc. Returns set."""
    return set(json.loads(fact.measures or "[]"))


def _get_fact_dimensions(fact):
    """Parse dimensions JSON from a Dataset doc. Returns set."""
    return set(json.loads(fact.dimensions or "[]"))


def _published_measures():
    """Set of measure names with status=Published in the Measure registry."""
    return {
        m.measure_name
        for m in frappe.get_all(
            "Measure", filters={"status": "Published"},
            fields=["measure_name"], limit_page_length=0,
        )
    }


def _measure_for(fact_doc, measure):
    """The measure a read uses: the one it named, else the Dataset's own
    default (konsol#105 Decision 1).

    Both the single-value and the batch read path call this, so the measure a
    query is built with is the same one that was validated.

    The attribute access is not a guard on _FACT_FIELDS, and does not claim to
    be: a Dataset row arrives as frappe._dict, whose ``__getattr__`` is
    ``dict.get``, so a column the query did not select reads as None here
    instead of raising — behaviourally the same as a getattr with a blank
    fallback. Were ``default_measure`` dropped from _FACT_FIELDS, every read
    would be refused with "declares no Default Measure", which names the
    Dataset for a bug in the field list. What stops that is the test pinning
    _FACT_FIELDS (test_fact_fields_load_the_default_measure), not this line.
    """
    return measure or fact_doc.default_measure


def default_measure_for_scenario(scenario):
    """The measure a hierarchy read of ``scenario`` uses when it names none:
    the default declared by the Dataset registered for that scenario
    (konsol#105 Decision 1).

    Blank when the registry answers nothing, which happens two ways: no
    Dataset is registered under that scenario key, or the one that is declares
    no default. The callers refuse a blank and say which; neither case is a
    licence to guess. This is the hierarchy sibling of _measure_for, which
    fills a flat read from the fact doc the read already resolved.
    """
    fact_doc = _get_fact_by_scenario(scenario)
    if not fact_doc:
        return ""
    return _measure_for(fact_doc, "") or ""


def _hierarchy_measure(scenario, measure):
    """(measure, error) for a hierarchy read: the one it named, else the
    registry's default for its scenario.

    One point of resolution for both hierarchy call sites, as _measure_for is
    for the two flat ones. The fill happens here, before the request is handed
    to hierarchy_query: that module is a query builder which requires a
    measure and knows nothing of the registry.

    The scenario is normalised with hierarchy_query's own rule before the
    lookup, because that module normalises on its side of the call:
    validate_hierarchy_read and batch_query_hierarchy both do. A lookup on the
    raw string would let " actuals " — or a batch row whose explicit
    ``"scenario": ""`` defeated ``req.get("scenario", "actuals")`` — pass
    hierarchy validation and then be refused here as a scenario with no
    declared default, blaming the registry for what is a spelling. Its rule,
    imported rather than restated, so the two sides cannot drift; in the body,
    like this module's other hierarchy_query imports, as that module imports
    konsol.api inside its own functions.
    """
    from konsol.hierarchy_query import _normalize_scenario

    scenario = _normalize_scenario(scenario)
    measure = measure or default_measure_for_scenario(scenario)
    if not measure:
        return "", (
            f"No default measure is declared for scenario '{scenario}', so a "
            f"hierarchy read of it must name one: either no Dataset is "
            f"registered for that scenario, or the one that is declares no "
            f"Default Measure."
        )
    return measure, None


def _parse_dimensions_arg(dimensions):
    """Accept a dict or a JSON-encoded string; return a plain dict.

    GET epm_value sends `dimensions` as a JSON string; epm_batch items send a
    native object. Empty values are dropped by the caller.
    """
    if not dimensions:
        return {}
    if isinstance(dimensions, dict):
        return dict(dimensions)
    try:
        parsed = json.loads(dimensions)
    except (json.JSONDecodeError, TypeError):
        frappe.throw("dimensions must be a JSON object", frappe.ValidationError)
    if not isinstance(parsed, dict):
        frappe.throw("dimensions must be a JSON object", frappe.ValidationError)
    return parsed


def _hierarchy_node_from_req(req):
    """Extract hierarchy node code from batch/GET params."""
    if isinstance(req, dict):
        return (req.get("hierarchy_node") or req.get("node") or "").strip()
    return (req or "").strip()


def _hierarchy_name_from_req(req):
    """Extract explicit hierarchy name from batch/GET params."""
    if isinstance(req, dict):
        return (req.get("hierarchy") or req.get("hierarchy_name") or "").strip()
    return (req or "").strip()


def _is_hierarchy_mode(req):
    return bool(_hierarchy_node_from_req(req))


#: The keys an epm_batch row may carry. Dimensions are not among them: they go
#: in the one ``dimensions`` dict, whose names the Dataset validates.
_BATCH_ROW_KEYS = frozenset({
    "entity", "year", "period", "account", "measure", "scenario", "fact",
    "scenario_id", "layer", "dimensions",
    "hierarchy", "hierarchy_name", "hierarchy_node", "node",
})


def _batch_row_error(req):
    """Why an epm_batch row is malformed, or None (konsol#287).

    A key outside ``_BATCH_ROW_KEYS`` is refused, not ignored. The Excel
    add-in once sent two dimensions as top-level keys; a workbook still on
    that add-in would otherwise be answered with the total over every value
    of the dimension it meant to filter on.
    """
    if not isinstance(req, dict):
        return "Each request must be a JSON object"
    unknown = sorted(k for k in req if k not in _BATCH_ROW_KEYS)
    if unknown:
        return (
            f"Unknown request key {', '.join(repr(k) for k in unknown)}. "
            "Dimensions go in 'dimensions', e.g. "
            '{"dimensions": {"<name>": "<value>"}}.'
        )
    dims = req.get("dimensions")
    if dims is not None and not isinstance(dims, dict):
        return "'dimensions' must be an object of dimension name to value"
    for key in ("hierarchy", "hierarchy_name", "hierarchy_node", "node"):
        value = req.get(key)
        if value is not None and not isinstance(value, str):
            return f"'{key}' must be text, not {value!r}"
    return (_hierarchy_without_node_error(req)
            or _fact_with_node_error(req.get("fact"), _hierarchy_node_from_req(req)))


def _fact_with_node_error(fact, node):
    """A ``fact`` named on a hierarchy read, or None (konsol#287 re-review).

    A hierarchy read's table comes from its scenario, not from a Dataset, so a
    ``fact`` there would decide only which dimensions are checked, not what
    is read. Refused rather than half-honoured.
    """
    if fact and node:
        return (
            f"'fact' ('{fact}') cannot be given with a hierarchy node: a "
            "hierarchy read's table comes from its scenario. Give the "
            "scenario, or drop the node for a flat read."
        )
    return None


def _hierarchy_dimension_error(scenario, dim_names, cache=None):
    """Why a hierarchy read cannot filter on ``dim_names``, or None (konsol#287
    review).

    The hierarchy query checks a name only against an identifier pattern, so
    any column of its table would be accepted as a filter. The names must be
    the declared dimensions of the scenario's Dataset, as on the flat path.
    The scenario is normalised as the hierarchy read normalises it. ``cache``
    (a dict, one per batch) holds each scenario's (fact_name, dimensions).
    """
    if not dim_names:
        return None
    from konsol.hierarchy_query import _normalize_scenario

    scenario = _normalize_scenario(scenario)
    if cache is None:
        cache = {}
    if scenario not in cache:
        fact = _get_fact(scenario=scenario)
        cache[scenario] = (
            (fact.fact_name, _get_fact_dimensions(fact)) if fact else None)
    found = cache[scenario]
    if found is None:
        return (
            f"No Dataset is registered for scenario '{scenario}', so a "
            f"hierarchy read cannot filter on {', '.join(sorted(dim_names))}"
        )
    fact_name, declared = found
    for name in sorted(dim_names):
        if name not in declared:
            return (
                f"Invalid dimension '{name}' for fact '{fact_name}'. "
                f"Allowed: {', '.join(sorted(declared)) or 'none'}"
            )
    return None


def _hierarchy_without_node_error(req):
    """A hierarchy named with no node, or None (konsol#287).

    It used to be ignored. Since the add-in's arguments moved one place left,
    an old formula's scenario id lands in the hierarchy slot, and ignoring it
    would read every scenario (or write without the hierarchy).
    """
    hierarchy = _hierarchy_name_from_req(req)
    if hierarchy and not _hierarchy_node_from_req(req):
        return (
            f"Hierarchy '{hierarchy}' is given without a node. Give a node, "
            "or leave the hierarchy blank."
        )
    return None


def _extract_batch_dimensions(req):
    """The request's ``dimensions`` dict, without blank values."""
    raw_dims = req.get("dimensions") if isinstance(req.get("dimensions"), dict) else {}
    return {k: v for k, v in raw_dims.items() if v}


def _apply_budget_dimensions(data, dims):
    """Move a budget write's ``dimensions`` dict onto ``data`` (konsol#287).

    ``dims`` is ``_budget_dims()``: the in_budget dimensions provisioned on
    Budget Line. A name outside it is refused, never ignored: an ignored
    dimension would write the amount to the line without it. A name given
    both in the dict and as a top-level key must agree.
    """
    raw = data.pop("dimensions", None)
    if raw is None:
        return
    if isinstance(raw, str):
        raw = _parse_dimensions_arg(raw)
    if not isinstance(raw, dict):
        frappe.throw(
            "'dimensions' must be an object of dimension name to value",
            frappe.ValidationError,
        )
    for name, value in raw.items():
        if name not in dims:
            frappe.throw(
                f"Dimension '{name}' is not a budget dimension on this site. "
                f"Declared: {', '.join(sorted(dims)) or 'none'}",
                frappe.ValidationError,
            )
        value = "" if value is None else str(value)
        if data.get(name) and value and data[name] != value:
            frappe.throw(
                f"Dimension '{name}' is given twice, as '{data[name]}' and "
                f"'{value}'",
                frappe.ValidationError,
            )
        if value:
            data[name] = value


def _resolve_and_validate(fact_name, scenario, measure, dim_names):
    """Resolve the fact, validate measure (Published registry ∩ fact) and
    dimensions (must be allowed by the fact). Returns (fact_doc, error_or_None)."""
    fact = _get_fact(fact=fact_name, scenario=scenario)
    if not fact:
        if fact_name:
            allowed = sorted(
                f.fact_name for f in frappe.get_all(
                    "Dataset", fields=["fact_name"], limit_page_length=0)
            )
            return None, f"Invalid fact '{fact_name}'. Allowed: {', '.join(allowed)}"
        allowed = sorted(set(
            f.scenario_key for f in frappe.get_all(
                "Dataset", fields=["scenario_key"], limit_page_length=0)
        ))
        return None, f"Invalid scenario '{scenario}'. Allowed: {', '.join(allowed)}"

    # A read that names no measure takes the Dataset's declared default. The
    # filled value is what gets validated here and what both callers query.
    measure = _measure_for(fact, measure)
    valid_measures = _get_allowed_measures(fact) & _published_measures()
    if not measure:
        return None, (
            f"Fact '{fact.fact_name}' declares no Default Measure, so a read "
            f"must name one. Its measures are: {', '.join(sorted(valid_measures))}"
        )
    if measure not in valid_measures:
        return None, (
            f"Invalid measure '{measure}' for fact '{fact.fact_name}'. "
            f"Allowed: {', '.join(sorted(valid_measures))}"
        )

    fact_dims = _get_fact_dimensions(fact)
    for dn in dim_names:
        if dn not in fact_dims:
            return None, (
                f"Invalid dimension '{dn}' for fact '{fact.fact_name}'. "
                f"Allowed: {', '.join(sorted(fact_dims))}"
            )
    return fact, None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_json_body():
    """Parse JSON request body. Used by all POST endpoints."""
    return json.loads(frappe.request.get_data(as_text=True))


def declared_calendar(year):
    """The periods the customer declared for ``year`` (cached, TTL).

    Read from EPM Fiscal Year Period — the same rows the Fiscal Year screen
    shows — so a customer on a 13-period or 4-4-5 calendar is answered from
    their own declaration and not from a constant in this app.

    Cached like the account-category map and for the same reason: a read path
    that runs per Excel cell must not issue one query per cell. A calendar
    edited in Frappe is invisible for up to the TTL; the window is short and
    period structure changes at most once a year.
    """
    cache = frappe.cache()
    key = f"{_CALENDAR_CACHE_KEY}:{int(year)}"
    rows = cache.get_value(key)
    if rows is None:
        rows = frappe.get_all(
            "EPM Fiscal Year Period",
            filters={"parent": str(int(year))},
            fields=["fiscal_period", "period_code", "period_type", "quarter"],
            order_by="fiscal_period asc",
            limit_page_length=0,
        )
        rows = [dict(r) for r in rows]
        cache.set_value(key, rows, expires_in_sec=_CALENDAR_TTL)
    return rows


def _resolve_period(period, year):
    """Resolve a read token to a tuple of fiscal_period integers, against the
    calendar declared for ``year``.

    Accepts a declared period number or code (``7``, ``P07``, ``13``, ``CLS``,
    ``0``, ``OPN``), a declared quarter (``Q1``-``Q4``), a half (``H1``/``H2``)
    or ``FY`` (the Regular periods). Raises ValueError naming the declared set
    on anything else.

    konsol#251: this used to clamp to 1-12, which made the year-end close
    unreadable — a balance-sheet account's closing figure exists only at the
    closing period, so the only number a user could obtain was the pre-close
    one, with no warning. The rules are in konsol/period_read_model.py.
    """
    return resolve_period(period, declared_calendar(year))


def _period_field(fiscal_period):
    """Wide period column name for a fiscal period (1-12) → ``period_NN``."""
    return "period_%02d" % int(fiscal_period)


def _budget_dims():
    """``in_budget`` dim fieldnames provisioned on Budget Line — resolved ONCE
    per request and threaded through, not re-queried per cell (avoids an N+1 of
    one Dimension lookup per period × line on the Excel write path)."""
    from konsol.epm.budget_grain import budget_dimension_names

    meta = frappe.get_meta("Budget Line")
    return [d for d in budget_dimension_names() if meta.get_field(d)]


def _line_identity(data, dims):
    """main_account + in_budget dim values identifying a Budget Line in a sheet.

    ``dims`` comes from ``_budget_dims()`` (resolved once). A dimension flagged
    ``in_budget`` whose Custom Field is not yet provisioned is already excluded
    from ``dims``, so it is skipped here too.
    """
    ident = {"main_account": data["main_account"]}
    for dim in dims:
        ident[dim] = data.get(dim, "") or ""
    return ident


def _resolve_budget_cycle(data, create=True):
    """Find (or create) the Budget Cycle for (scenario, fiscal_year).

    Returns ``{"name", "status"}`` or None. A new cycle starts Open
    (docstatus 0); the manager submits it to lock. The create path tolerates the
    concurrent-create race (two first-writes to the same scenario/year): on a
    ``DuplicateEntryError`` it re-reads the row the other writer just inserted
    instead of 500-ing.
    """
    filters = {
        "scenario_id": data["scenario_id"],
        "fiscal_year": int(data["fiscal_year"]),
    }
    existing = frappe.get_all(
        "Budget Cycle", filters=filters, fields=["name", "status"], limit=1)
    if existing:
        return existing[0]
    if not create:
        return None
    doc = frappe.new_doc("Budget Cycle")
    doc.update(filters)
    doc.status = "Open"
    try:
        doc.insert()
    except frappe.exceptions.DuplicateEntryError:
        frappe.db.rollback()  # concurrent create won — re-read it
        row = frappe.get_all(
            "Budget Cycle", filters=filters, fields=["name", "status"], limit=1)
        if row:
            return row[0]
        raise
    return {"name": doc.name, "status": doc.status}


def _assert_cycle_open(cycle):
    """Reject budget writes once the cycle is locked (HTTP 423)."""
    if cycle and cycle.get("status") == "Locked":
        frappe.local.response.http_status_code = 423  # Locked
        frappe.throw(
            "Budget Cycle '{0}' is locked; the budget is final. "
            "Cancel the cycle to amend.".format(cycle["name"]),
            frappe.ValidationError,
        )


def _get_or_create_sheet(cycle_name, data, layer):
    """Find or load (else build, uninserted) the Budget Sheet for (cycle, entity, layer)."""
    filters = {
        "cycle": cycle_name,
        "data_area_id": data["data_area_id"],
        "layer": layer,
    }
    existing = frappe.get_all("Budget Sheet", filters=filters, limit=1)
    if existing:
        return frappe.get_doc("Budget Sheet", existing[0].name)
    doc = frappe.new_doc("Budget Sheet")
    doc.update(filters)
    return doc


# ---------------------------------------------------------------------------
# ClickHouse query helpers
# ---------------------------------------------------------------------------

class ClickHouseQueryError(Exception):
    """ClickHouse answered with an HTTP error; the message carries its code."""


_CH_ERROR_CODE = re.compile(r"^Code:\s*(\d+)")
_CH_EXCEPTION_NAME = re.compile(r"\(([A-Z][A-Z0-9_]*)\)")


def _clickhouse_error_message(body):
    """The message a user sees for a failed ClickHouse query.

    Only ClickHouse's error code and exception name, e.g.
    "ClickHouse query failed (47: UNKNOWN_IDENTIFIER)": the rest of its first
    line can carry table names, SQL, the server version or the user name, so
    that stays in the log (konsol#214 row K7).
    """
    first_line = (body or "").strip().split("\n", 1)[0]
    code = _CH_ERROR_CODE.match(first_line)
    if not code:
        return "ClickHouse query failed"
    names = _CH_EXCEPTION_NAME.findall(first_line)
    if names:
        return f"ClickHouse query failed ({code.group(1)}: {names[-1]})"
    return f"ClickHouse query failed ({code.group(1)})"


def _clickhouse_query(sql, params, ch_settings):
    """Execute a single ClickHouse HTTP query. Returns response text or raises.

    The SQL goes in the POST body, never in the URL. As a URL parameter it
    passed ClickHouse's 128 KiB form-field limit at the add-in's chunk size,
    and every chunk of a dense sheet failed with "Poco::Exception. Code: 1000
    — HTML Form Exception: Field value too long" (konsol#194: 1,500 cells in
    one group were fine, 1,800 were not). Only the param_* values stay in the
    URL; this is the shape konsol.clickhouse.execute already uses.

    On an HTTP error, ClickHouse's response text is logged and the raised
    ClickHouseQueryError carries its error code and exception name only
    (e.g. "ClickHouse query failed (47: UNKNOWN_IDENTIFIER)"), so the caller
    can show why without leaking the query's detail (konsol#214).
    """
    url = _ch_url(ch_settings)

    resp = requests.post(
        url,
        params=dict(params),
        data=sql.encode("utf-8"),
        auth=(ch_settings["user"], ch_settings["password"]),
        timeout=30,
        verify=ch_settings.get("verify", True),
    )
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        body = resp.text or ""
        frappe.log_error("ClickHouse query failed", body[:1000])
        raise ClickHouseQueryError(_clickhouse_error_message(body)) from exc
    return resp.text.strip()


def _batch_query_clickhouse(requests_list):
    """Execute batched ClickHouse queries grouped by (scenario, measure, dims).

    Supports dynamic dimensions — each request carries a `dimensions` dict
    mapping dimension names to filter values.  Requests are grouped by shared
    table/measure/dimension-shape for efficient batching; the period is NOT
    part of the key (konsol#231), so a year of monthly cells is one query
    rather than twelve.  `fiscal_period` is selected and grouped so a returned
    row can be attributed to the cell that asked for it, and each cell reads
    the sum of its own periods.

    Returns {"values": [...], "errors": [...]}.
    """
    from konsol.hierarchy_query import (
        _active_budget_scenarios,
        _active_budget_scenarios_by_year,
        choose_budget_scenario,
        named_budget_scenario_error,
        _scenario_ids,
        scenario_id_error,
    )

    known_cache = []  # every Scenario id, looked up once when first needed

    def known_scenarios():
        if not known_cache:
            known_cache.append(_scenario_ids())
        return known_cache[0]

    ch_settings = _get_ch_connection()
    n = len(requests_list)
    values = [None] * n
    errors = [None] * n
    error_codes = [None] * n  # tb_coverage.NO_DATA where nothing is loaded
    facts = {}  # fact_key -> Dataset (or None), resolved once per call
    budgets_by_year = None  # looked up once per call, when first needed
    active_budgets = None  # likewise, for named budget scenarios

    def resolve_fact(fact_key):
        if fact_key not in facts:
            facts[fact_key] = _get_fact(fact=fact_key) or _get_fact_by_scenario(fact_key)
        return facts[fact_key]

    # Group by (fact, measure, dim_names_frozenset, scenario_id, layer).
    # `fact` (the resolved fact_name) is the table-determining element; scenario
    # is retained per-request for backward compatibility / resolution fallback.
    # The period is deliberately absent (konsol#231): the SQL already filters
    # `fiscal_period IN (…)`, so cells differing only in period share a query.
    groups = defaultdict(list)
    keyed = []  # (group key, idx, req), grouped once actuals coverage is known
    for idx, req in enumerate(requests_list):
        dims = req.get("dimensions", {})
        fact_key = req.get("fact") or req.get("scenario")
        scenario_id = req.get("scenario_id", "")
        if scenario_id:
            # The id must have a column to filter, and (outside the variance
            # dataset, checked below) name a Scenario that exists. Ignored, it
            # read the total; filtering to nothing, it read 0 (konsol#287).
            fact = resolve_fact(fact_key)
            if fact and not _BUDGET_SCENARIO_COLUMN.get(fact.fact_name):
                err = scenario_id_error(
                    scenario_id, has_column=fact.has_scenario_id,
                    known=known_scenarios, label=f"Dataset '{fact.fact_name}'")
                if err:
                    errors[idx] = err
                    continue
        if scenario_id and _SAFE_SCENARIO_ID.match(scenario_id):
            # A named scenario must be one the dataset keeps: an active
            # budget. Any other id (actuals, inactive, misspelled) would
            # filter to nothing and read 0.0. An unsafe id is refused below.
            fact = resolve_fact(fact_key)
            if fact and _BUDGET_SCENARIO_COLUMN.get(fact.fact_name):
                if active_budgets is None:
                    active_budgets = set(_active_budget_scenarios())
                err = named_budget_scenario_error(scenario_id, active_budgets)
                if err:
                    errors[idx] = err
                    continue
        elif not scenario_id:
            # A dataset kept per budget scenario reads exactly one: the named
            # one, else the single active budget of the row's year, else a
            # refusal that says why (konsol#214). Resolved per row, before
            # grouping: a group's key has no year, so it can hold several.
            fact = resolve_fact(fact_key)
            if fact and _BUDGET_SCENARIO_COLUMN.get(fact.fact_name):
                if budgets_by_year is None:
                    budgets_by_year = _active_budget_scenarios_by_year()
                year = int(req["year"])
                scenario_id, err = choose_budget_scenario(
                    budgets_by_year.get(year, []), fiscal_year=year)
                if err:
                    errors[idx] = err
                    continue
        key = (
            fact_key,
            req["measure"],
            frozenset(dims.keys()),
            scenario_id,
            req.get("layer", ""),
        )
        keyed.append((key, idx, req))

    # An actuals cell none of whose periods has a trial balance for its
    # entity is refused, not read as 0 (konsol#401). "Loaded" is asked of the
    # table the actuals Dataset reads, once per such table (one, normally).
    # The coverage check runs before the per-group checks below, so an
    # unloaded cell that also has a bad table or measure declaration shows
    # the missing trial balance first.
    by_table = defaultdict(list)
    for key, idx, req in keyed:
        fact = resolve_fact(key[0])
        if _is_actuals_fact(fact):
            by_table[fact.clickhouse_table or ""].append((idx, req))
    for table, actual_rows in by_table.items():
        if not _SAFE_TABLE_NAME.match(table):
            loaded, failure = set(), "Invalid table identifier"
        else:
            loaded, failure = _loaded_tb_periods(
                {req["year"] for _, req in actual_rows}, ch_settings, table,
                {req["entity"] for _, req in actual_rows})
        for idx, req in actual_rows:
            err = failure or tb_coverage.no_tb_error(
                req["entity"], req["year"], req["periods"], loaded)
            if err:
                errors[idx] = err
                if not failure:
                    error_codes[idx] = tb_coverage.NO_DATA
    for key, idx, req in keyed:
        if errors[idx] is None:
            groups[key].append((idx, req))

    for (fact_key, measure, dim_names, scenario_id, layer), group_items in groups.items():
        fact = resolve_fact(fact_key)
        if not fact:
            for idx, _ in group_items:
                errors[idx] = f"No Dataset for '{fact_key}'"
            continue

        table = fact.clickhouse_table
        query_measure = measure

        # Reroute measures that live in a different table/column
        if fact.reroute_measure and measure == fact.reroute_measure:
            if fact.reroute_table:
                table = fact.reroute_table
            if fact.reroute_column:
                query_measure = fact.reroute_column

        # How this measure combines across periods, as it declares it. A
        # rerouted cumulative measure is the case that forced this: summing
        # `cumulative_balance` over a range adds balances together
        # (konsol#251).
        try:
            cube_type = aggregation_for(measure)
            period_aggregate = sql_aggregate(cube_type, query_measure)
        except ValueError as exc:
            for idx, _ in group_items:
                errors[idx] = str(exc)
            continue

        # Validate identifiers before SQL interpolation. The table name comes
        # from the Dataset doctype but is interpolated directly into FROM,
        # so it must be validated too (defence against a tampered/typo'd
        # clickhouse_table or reroute_table value).
        if not _SAFE_TABLE_NAME.match(table or ""):
            for idx, _ in group_items:
                errors[idx] = "Invalid table identifier"
            continue
        if not _SAFE_IDENTIFIER.match(query_measure):
            for idx, _ in group_items:
                errors[idx] = "Invalid measure identifier"
            continue

        # Validate dimension names
        dim_names_sorted = sorted(dim_names)
        dim_valid = True
        for dn in dim_names_sorted:
            if not _SAFE_IDENTIFIER.match(dn):
                for idx, _ in group_items:
                    errors[idx] = f"Invalid dimension identifier: {dn}"
                dim_valid = False
                break
        if not dim_valid:
            continue

        # Two column lists, which used to be one: the IN tuple matches the
        # per-cell key (entity, year, account, dims) and carries no period,
        # while SELECT/GROUP BY adds `fiscal_period` so a row can be
        # attributed to the cell that asked for it (konsol#231).
        in_col_names = ["data_area_id", "fiscal_year", "main_account"]
        in_col_names.extend(dim_names_sorted)
        select_cols = ["data_area_id", "fiscal_year", "fiscal_period", "main_account"]
        select_cols.extend(dim_names_sorted)

        # Build IN tuples and params (without fiscal_period)
        in_tuples = []
        params = {}
        for i, (idx, req) in enumerate(group_items):
            dims = req.get("dimensions", {})
            parts = [f"{{e{i}:String}}", f"{{y{i}:Int32}}", f"{{a{i}:String}}"]
            params[f"param_e{i}"] = req["entity"]
            params[f"param_y{i}"] = str(req["year"])
            params[f"param_a{i}"] = req["account"]
            for di, dn in enumerate(dim_names_sorted):
                parts.append(f"{{d{i}_{di}:String}}")
                params[f"param_d{i}_{di}"] = dims.get(dn, "")
            in_tuples.append(f"({', '.join(parts)})")

        group_by = ", ".join(select_cols)
        in_cols = f"({', '.join(in_col_names)})"
        in_values = ", ".join(in_tuples)

        # fiscal_period IN (...) — parameterized, over the union of every
        # period the group's cells asked for (konsol#231).
        group_periods = sorted({p for _, r in group_items for p in r["periods"]})
        period_placeholders = []
        for pi, p in enumerate(group_periods):
            pkey = f"fp{pi}"
            period_placeholders.append(f"{{{pkey}:Int32}}")
            params[f"param_{pkey}"] = str(p)
        period_in = ", ".join(period_placeholders)

        # A dataset kept per budget scenario filters its own column; the
        # scenario_id was resolved per row above when none was named.
        budget_column = _BUDGET_SCENARIO_COLUMN.get(fact.fact_name)

        # Optional scenario_id filter
        scenario_id_clause = ""
        if scenario_id and (fact.has_scenario_id or budget_column):
            if not _SAFE_SCENARIO_ID.match(scenario_id):
                for idx, _ in group_items:
                    errors[idx] = "Invalid scenario_id format"
                continue
            params["param_sid"] = scenario_id
            column = budget_column or "scenario_id"
            scenario_id_clause = f" AND {column} = {{sid:String}}"

        # Optional layer filter (budget facts). Omitted → sum across all layers
        # (the final budget); supplied → restrict to one layer. Mirrors the
        # scenario_id filter above: not grouped/selected, just narrowed.
        layer_clause = ""
        if layer and fact.has_layer:
            if not _SAFE_IDENTIFIER.match(layer):
                for idx, _ in group_items:
                    errors[idx] = "Invalid layer format"
                continue
            params["param_layer"] = layer
            layer_clause = " AND layer = {layer:String}"

        sql = (
            f"SELECT {group_by}, coalesce({period_aggregate}, 0) as val "
            f"FROM {table} "
            f"WHERE {in_cols} IN ({in_values}) "
            f"AND fiscal_period IN ({period_in})"
            f"{scenario_id_clause}"
            f"{layer_clause} "
            f"GROUP BY {group_by}"
        )

        try:
            raw = _clickhouse_query(sql, params, ch_settings)
            result_lookup = {}
            for line in raw.split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                key_parts = tuple(parts[:-1])
                val = float(parts[-1])
                result_lookup[key_parts] = val

            for idx, req in group_items:
                dims = req.get("dimensions", {})
                dim_values = [dims.get(dn, "") for dn in dim_names_sorted]
                # Each cell combines the periods it asked for the way its
                # measure declares (konsol#231 for the per-cell attribution,
                # konsol#251 for honouring the declaration instead of always
                # summing). A period the warehouse holds no row for is left
                # out rather than counted as 0.0, so it cannot drag an average
                # down or stand in as the `last` value.
                present = []
                for p in req["periods"]:
                    lookup_key = (req["entity"], str(req["year"]), str(p),
                                  req["account"], *dim_values)
                    if lookup_key in result_lookup:
                        present.append((p, result_lookup[lookup_key]))
                values[idx] = combine_periods(cube_type, present)

        except requests.exceptions.Timeout:
            for idx, _ in group_items:
                values[idx] = None
                errors[idx] = "ClickHouse query timeout"
        except requests.exceptions.ConnectionError:
            for idx, _ in group_items:
                values[idx] = None
                errors[idx] = "ClickHouse connection failed"
        except ClickHouseQueryError as exc:
            # already logged with ClickHouse's response text
            for idx, _ in group_items:
                values[idx] = None
                errors[idx] = str(exc)
        except Exception:
            frappe.log_error("ClickHouse query failed", frappe.get_traceback())
            for idx, _ in group_items:
                values[idx] = None
                errors[idx] = "ClickHouse query failed"

    result = {"values": values}
    if any(e is not None for e in errors):
        result["errors"] = errors
    if any(c is not None for c in error_codes):
        result["error_codes"] = error_codes
    return result


def _is_actuals_fact(fact):
    """The Dataset whose read konsol#401 checks against the loaded TBs."""
    return bool(fact) and (fact.scenario_key or "") == tb_coverage.ACTUALS


def _query_failure_message(exc, title="ClickHouse query failed"):
    """The per-row message for a failed ClickHouse call, as the data query
    reports it."""
    if isinstance(exc, requests.exceptions.Timeout):
        return "ClickHouse query timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "ClickHouse connection failed"
    if isinstance(exc, ClickHouseQueryError):
        return str(exc)  # already logged with ClickHouse's response text
    frappe.log_error(title, frappe.get_traceback())
    return "ClickHouse query failed"


def _loaded_tb_periods(years, ch_settings, table, entities):
    """({(entity, year, period)} holding a trial balance, None), or
    (empty set, message) when the lookup failed (konsol#401). ``table`` is
    the actuals Dataset's table, already validated."""
    sql, params = tb_coverage.coverage_query(years, table, entities)
    try:
        return tb_coverage.parse_coverage(_clickhouse_query(sql, params, ch_settings)), None
    except Exception as exc:
        return set(), _query_failure_message(exc)


# ---------------------------------------------------------------------------
# Data Retrieval Endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def health():
    """Health check endpoint with ClickHouse connectivity status.

    Returns:
        status: 'ok' | 'degraded' | 'down'
        app: 'konsol'
        clickhouse: detailed ClickHouse health info
    """
    from konsol.clickhouse import check_health as ch_health

    ch_status = ch_health()
    overall_status = "ok" if ch_status["status"] == "healthy" else ch_status["status"]

    return {
        "status": overall_status,
        "app": "konsol",
        "clickhouse": ch_status,
    }


@frappe.whitelist()
def epm_value(entity, year, period, account, measure="",
              scenario="actuals", fact=None, dimensions=None, scenario_id="",
              hierarchy=None, node=None, hierarchy_node=None, layer=""):
    """Single value lookup — returns {"value": <number>}.

    Period accepts: 1-12 (single month), "Q1"-"Q4", "H1"-"H2", "FY".
    Range periods return the sum across constituent months.

    Dimensions are passed as a generic dict (JSON string on this GET endpoint),
    e.g. dimensions={"dim_region":"EU","dim_product":"X"}. Keys are
    canonical dimension names, validated against the fact's allowed dimensions.
    `fact` (a Fact registry name) selects the source table and wins over
    `scenario`; if neither pins a fact, `scenario` resolves it via scenario_key.

    When ``node`` (or ``hierarchy_node``) is set, queries hierarchy gold tables
    instead of flat entity facts. ``hierarchy`` may be blank only when exactly
    one published hierarchy holds the node; a node in several is an error
    that names them.
    """
    from konsol.entity_permissions import entity_read_scope
    from konsol.hierarchy_query import batch_query_hierarchy, entity_is_wildcard
    from konsol.hierarchy_query import validate_hierarchy_read

    node_code = _hierarchy_node_from_req({
        "hierarchy_node": hierarchy_node,
        "node": node,
    })
    req_dims = _extract_batch_dimensions({
        "dimensions": _parse_dimensions_arg(dimensions),
    })

    # Resolved once, for both the hierarchy and the flat path below, and
    # wrapped: this used to be called bare at each branch, so an undeclared
    # period escaped the endpoint as a raw ValueError while the same input on
    # epm_batch came back as a clean per-row refusal (konsol#251).
    try:
        periods = _resolve_period(period, year)
    except ValueError as exc:
        frappe.throw(str(exc), frappe.ValidationError)

    if node_code:
        fact_err = _fact_with_node_error(fact, node_code)
        if fact_err:
            frappe.throw(fact_err, frappe.ValidationError)
        info, err = validate_hierarchy_read(
            _hierarchy_name_from_req({"hierarchy": hierarchy}),
            node_code,
            scenario,
        )
        if err:
            frappe.throw(err, frappe.ValidationError)
        allowed_entities = _allowed_entities()
        # Permission before the registry, in the same order as epm_batch: a
        # caller refused this entity must not be able to tell, from which
        # error comes back, whether a Dataset is registered for the scenario.
        # A named entity is refused here; a wildcard is limited to the allowed
        # set inside batch_query_hierarchy. Both ask entity_read_scope.
        if not entity_is_wildcard(entity):
            _, denied = entity_read_scope(entity, allowed_entities)
            if denied:
                raise frappe.PermissionError(denied)
        # A hierarchy read that names no measure takes the one the Dataset
        # registered for its scenario declares. The query layer requires a
        # measure, so a blank left here would be refused there instead, with
        # no idea which scenario's registry came up empty.
        measure, measure_err = _hierarchy_measure(scenario, measure)
        if measure_err:
            frappe.throw(measure_err, frappe.ValidationError)
        dim_err = _hierarchy_dimension_error(scenario, set(req_dims))
        if dim_err:
            frappe.throw(dim_err, frappe.ValidationError)
        result = batch_query_hierarchy([{
            "entity": entity,
            "year": int(year),
            "periods": periods,
            "account": account,
            "measure": measure,
            "scenario": scenario,
            "dimensions": req_dims,
            "scenario_id": scenario_id,
            "layer": layer,
            "hierarchy_name": info["hierarchy_name"],
            "hierarchy_node": info["member_code"],
        }], allowed_entities=allowed_entities)
        if result.get("errors") and result["errors"][0]:
            frappe.throw(result["errors"][0], frappe.ValidationError)
        return {"value": result["values"][0]}

    _assert_entity_access(entity)

    dims = {k: v for k, v in req_dims.items() if v}

    fact_doc, err = _resolve_and_validate(fact, scenario, measure, dims.keys())
    if err:
        frappe.throw(err, frappe.ValidationError)
    # Query the measure that was validated: a blank left here would reach the
    # identifier check in _batch_query_clickhouse and read nothing.
    measure = _measure_for(fact_doc, measure)

    result = _batch_query_clickhouse([{
        "entity": entity, "year": int(year),
        "periods": periods,
        "account": account, "measure": measure,
        "fact": fact_doc.fact_name, "scenario": scenario,
        "dimensions": dims,
        "scenario_id": scenario_id,
        "layer": layer,
    }])
    # A refused read (e.g. no or several active budget scenarios) says why
    # instead of coming back as an empty cell (konsol#214).
    if result.get("errors") and result["errors"][0]:
        frappe.throw(result["errors"][0], frappe.ValidationError)
    return {"value": result["values"][0]}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def excel_addin_auth():
    """Login for Excel Online iframe — returns bearer token (cookies often blocked).

    Store the token in localStorage; send X-Konsolidat-Token on API calls.
    Shared runtime makes the same localStorage visible to custom functions.
    """
    from frappe.auth import LoginManager
    from konsol.excel_addin_auth import issue_token

    body = _get_json_body()
    usr = (body.get("usr") or "").strip()
    pwd = body.get("pwd") or ""
    if not usr or not pwd:
        frappe.throw("usr and pwd required", frappe.ValidationError)

    login_manager = LoginManager()
    login_manager.authenticate(user=usr, pwd=pwd)
    login_manager.post_login()

    token = issue_token(frappe.session.user)
    return {"token": token, "user": frappe.session.user}


@frappe.whitelist(allow_guest=True)
def excel_addin_whoami():
    """Return current user (cookie session or X-Konsolidat-Token)."""
    user = frappe.session.user
    return {"user": user if user and user != "Guest" else None}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def excel_addin_logout():
    """Revoke bearer token issued by excel_addin_auth."""
    from konsol.excel_addin_auth import revoke_token

    body = _get_json_body() if frappe.request.get_data() else {}
    token = (body.get("token") or frappe.get_request_header("X-Konsolidat-Token") or "").strip()
    revoke_token(token)
    return {"ok": True}


@frappe.whitelist(methods=["GET"])
def list_report_templates():
    """Catalog of product-owned report templates for add-in Apply."""
    from konsol.report_compiler import list_templates

    return {"templates": list_templates()}


@frappe.whitelist(methods=["POST"])
def build_cell_map():
    """Compile a report template + params into an Excel cell map (formulas)."""
    from konsol.report_compiler import build_cell_map as compile_cell_map

    body = _get_json_body()
    template_id = (body.get("template_id") or "").strip()
    entity = (body.get("entity") or "").strip()
    year = body.get("year")
    scenario_id = (body.get("scenario_id") or "actuals").strip()

    if not template_id or not entity or year is None:
        frappe.throw("template_id, entity, and year are required", frappe.ValidationError)

    _assert_entity_access(entity)

    accounts = _pnl_accounts() if template_id == "pnl_monthly" else None
    try:
        return compile_cell_map(template_id, entity, int(year), scenario_id, accounts=accounts)
    except ValueError as exc:
        frappe.throw(str(exc), frappe.ValidationError)


def _pnl_accounts():
    """The group chart's Published Profit and Loss leaves as (code, caption), in code order.

    The monthly P&L reads one chart: leaves spread over several charts are refused.
    """
    rows = frappe.get_all(
        "Main Account",
        filters={"status": "Published", "is_group": 0, "statement_section": "Profit and Loss"},
        fields=["main_account", "account_name", "chart_of_accounts"],
        order_by="main_account asc",
    )
    charts = sorted({r.chart_of_accounts for r in rows if r.chart_of_accounts})
    if len(charts) > 1:
        frappe.throw(
            "The monthly P&L reads one chart, and Published Profit and Loss accounts exist in "
            f"charts {', '.join(charts)}. Make one chart Inactive or Draft.",
            frappe.ValidationError,
        )
    return [(r.main_account, r.account_name or r.main_account) for r in rows]


def _fetch_trial_balance_rows(entity, year, period_from, period_to):
    """Load trial balance rows from gold_trial_balance for snapshot export."""
    from konsol.report_compiler import TB_LONG_MAX_ROWS

    ch_settings = _get_ch_connection()
    sql = (
        "SELECT data_area_id, fiscal_year, fiscal_period, main_account, "
        "any(account_name) AS account_name, any(account_type_name) AS account_type_name, "
        "any(multiIf(is_pnl = 1, 'P&L', is_balance_sheet = 1, 'BS', '')) AS bs_pnl, "
        "sum(period_debit) AS debit, sum(period_credit) AS credit, "
        "sum(period_debit) - sum(period_credit) AS net "
        "FROM epm_gold.gold_trial_balance "
        "WHERE data_area_id = {entity:String} "
        "AND fiscal_year = {year:Int32} "
        "AND fiscal_period >= {pfrom:Int32} AND fiscal_period <= {pto:Int32} "
        "GROUP BY data_area_id, fiscal_year, fiscal_period, main_account "
        "ORDER BY main_account, fiscal_period "
        f"LIMIT {int(TB_LONG_MAX_ROWS)}"
    )
    params = {
        "param_entity": entity,
        "param_year": str(int(year)),
        "param_pfrom": str(int(period_from)),
        "param_pto": str(int(period_to)),
    }
    raw = _clickhouse_query(sql, params, ch_settings)
    rows = []
    for line in raw.split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 10:
            continue
        rows.append({
            "entity": parts[0],
            "year": int(parts[1]),
            "period": int(parts[2]),
            "account": parts[3],
            "account_name": parts[4],
            "account_type": parts[5],
            "bs_pnl": parts[6],
            "debit": float(parts[7]),
            "credit": float(parts[8]),
            "net": float(parts[9]),
        })
    return rows


@frappe.whitelist(methods=["POST"])
def build_snapshot():
    """Fetch data and compile a values-only Excel cell map (snapshot)."""
    from konsol.report_compiler import build_trial_balance_long_map

    body = _get_json_body()
    template_id = (body.get("template_id") or "").strip()
    entity = (body.get("entity") or "").strip()
    year = body.get("year")
    period_from = body.get("period_from", 1)
    period_to = body.get("period_to", 12)

    if not template_id or not entity or year is None:
        frappe.throw("template_id, entity, and year are required", frappe.ValidationError)

    if template_id != "trial_balance_long":
        frappe.throw(
            f"Snapshot not supported for template {template_id!r}",
            frappe.ValidationError,
        )

    try:
        period_from = int(period_from)
        period_to = int(period_to)
    except (TypeError, ValueError):
        frappe.throw("period_from and period_to must be integers", frappe.ValidationError)

    if period_from < 1 or period_to > 12 or period_from > period_to:
        frappe.throw("period_from and period_to must be between 1 and 12", frappe.ValidationError)

    _assert_entity_access(entity)

    rows = _fetch_trial_balance_rows(entity, int(year), period_from, period_to)
    return build_trial_balance_long_map(entity, int(year), rows, period_from, period_to)


@frappe.whitelist(methods=["POST"])
def epm_batch():
    """Batch value retrieval — accepts JSON array, returns {"values": [...], "errors": [...]}."""
    requests_list = _get_json_body()

    if not isinstance(requests_list, list):
        frappe.throw("Expected a JSON array", frappe.ValidationError)

    if len(requests_list) > MAX_BATCH_SIZE:
        frappe.throw(
            f"Batch size {len(requests_list)} exceeds maximum of {MAX_BATCH_SIZE}",
            frappe.ValidationError,
        )

    from konsol.entity_permissions import entity_read_scope
    from konsol.hierarchy_query import batch_query_hierarchy, entity_is_wildcard
    from konsol.hierarchy_query import validate_hierarchy_read

    n = len(requests_list)
    normalized = [None] * n
    errors_list = [None] * n
    # Resolve the caller's entity allow-list once for the whole batch.
    allowed_entities = _allowed_entities()
    # Each hierarchy scenario's Dataset dimensions, looked up once per batch.
    dataset_dims = {}
    for i, req in enumerate(requests_list):
        row_error = _batch_row_error(req)
        if row_error:
            errors_list[i] = row_error
            continue
        scenario = req.get("scenario", "actuals")
        measure = req.get("measure") or ""
        dimensions = _extract_batch_dimensions(req)

        # A missing, non-integer or non-positive year (e.g. JSON null from a
        # blank Excel cell) fails only this row — not raise and 500 the whole
        # batch, and not read as FY0.
        raw_year = req.get("year")
        if raw_year is None or raw_year == "":
            errors_list[i] = "Invalid year"
            continue
        try:
            if isinstance(raw_year, bool) or (
                    isinstance(raw_year, float) and not raw_year.is_integer()):
                raise ValueError(raw_year)
            year = int(req.get("year"))
            if year < 1:
                raise ValueError(year)
        except (ValueError, TypeError):
            errors_list[i] = f"Invalid year '{raw_year}'"
            continue

        # After the year, because the accepted periods are the ones that year
        # declares (konsol#251). The resolver's own message names the declared
        # set, which is more use to a reader than "Invalid period '13'".
        try:
            periods = _resolve_period(req.get("period", 0), year)
        except (ValueError, TypeError) as exc:
            errors_list[i] = str(exc) or f"Invalid period '{req.get('period')}'"
            continue

        entity = req.get("entity", "")

        if _is_hierarchy_mode(req):
            info, err = validate_hierarchy_read(
                _hierarchy_name_from_req(req),
                _hierarchy_node_from_req(req),
                scenario,
            )
            if err:
                errors_list[i] = err
                continue
            if not entity_is_wildcard(entity):
                _, denied = entity_read_scope(entity, allowed_entities)
                if denied:
                    errors_list[i] = denied
                    continue
            # As in epm_value: filled from the registry here, so the request
            # handed over carries a measure. A row the registry cannot answer
            # for fails on its own, not the whole batch.
            measure, measure_err = _hierarchy_measure(scenario, measure)
            if measure_err:
                errors_list[i] = measure_err
                continue
            dim_err = _hierarchy_dimension_error(
                scenario, set(dimensions), dataset_dims)
            if dim_err:
                errors_list[i] = dim_err
                continue
            normalized[i] = {
                "entity": entity,
                "year": year,
                "periods": periods,
                "account": req.get("account", ""),
                "measure": measure,
                "scenario": scenario,
                "dimensions": dimensions,
                "scenario_id": req.get("scenario_id", ""),
                "layer": req.get("layer", ""),
                "hierarchy_name": info["hierarchy_name"],
                "hierarchy_node": info["member_code"],
                "_hierarchy_mode": True,
            }
            continue

        fact_name = req.get("fact")
        fact_doc, err = _resolve_and_validate(
            fact_name, scenario, measure, dimensions.keys())
        if err:
            errors_list[i] = err
            continue
        _, denied = entity_read_scope(entity, allowed_entities)
        if denied:
            errors_list[i] = denied
            continue
        # As in epm_value: the row is queried with the measure that was
        # validated, not the blank it may have arrived with.
        measure = _measure_for(fact_doc, measure)

        normalized[i] = {
            "entity": entity,
            "year": year,
            "periods": periods,
            "account": req.get("account", ""),
            "measure": measure,
            "fact": fact_doc.fact_name,
            "scenario": scenario,
            "dimensions": dimensions,
            "scenario_id": req.get("scenario_id", ""),
            "layer": req.get("layer", ""),
        }

    values = [None] * n
    # Parallel to errors: "no_data" where an actuals cell has no trial balance
    # to read (konsol#401), which the add-in shows as #N/A, not #VALUE!.
    codes_list = [None] * n
    valid = [(i, r) for i, r in enumerate(normalized) if r is not None]
    if valid:
        hierarchy_valid = [(i, r) for i, r in valid if r.get("_hierarchy_mode")]
        legacy_valid = [(i, r) for i, r in valid if not r.get("_hierarchy_mode")]

        def take(indices, sub):
            for j, orig_idx in enumerate(indices):
                values[orig_idx] = sub["values"][j]
                if sub.get("errors") and sub["errors"][j]:
                    errors_list[orig_idx] = sub["errors"][j]
                if sub.get("error_codes") and sub["error_codes"][j]:
                    codes_list[orig_idx] = sub["error_codes"][j]

        if hierarchy_valid:
            h_indices, h_reqs = zip(*hierarchy_valid)
            take(h_indices, batch_query_hierarchy(
                [{k: v for k, v in r.items() if not k.startswith("_")} for r in h_reqs],
                allowed_entities=allowed_entities,
            ))

        if legacy_valid:
            l_indices, l_reqs = zip(*legacy_valid)
            take(l_indices, _batch_query_clickhouse(list(l_reqs)))

    result = {"values": values}
    if any(e is not None for e in errors_list):
        result["errors"] = errors_list
    if any(c is not None for c in codes_list):
        result["error_codes"] = codes_list
    return result


# ---------------------------------------------------------------------------
# Budget Write-Back Endpoints
# ---------------------------------------------------------------------------

def _validate_budget_fields(data):
    """Validate required fields for budget save. Raises on missing."""
    required = ["scenario_id", "data_area_id", "fiscal_year", "main_account", "periods"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        frappe.throw(
            f"Missing required fields: {', '.join(missing)}",
            frappe.ValidationError,
        )
    if not isinstance(data["periods"], list) or not data["periods"]:
        frappe.throw("periods must be a non-empty array", frappe.ValidationError)


def _set_cell(sheet, ident, fiscal_period, amount, dims):
    """Set one period cell on the sheet's Budget Line (account+dims), appending if new."""
    from konsol.epm.budget_grain import find_budget_line

    line = find_budget_line(sheet, ident, dims, append=True)
    line.set(_period_field(fiscal_period), amount)
    return line


def _upsert_budget_line(data):
    """Create/update Budget Line(s) for a budget save payload. Returns sheet name(s).

    Resolves the (auto-created, Open) Budget Cycle, then writes each period into
    its (cycle × entity × layer) sheet — layer is sheet-level now, so a payload
    mixing layers fans out across sheets. Only the supplied periods are touched;
    unspecified months on an existing line are preserved. Layer is canonicalised
    + validated so a sheet's grain can't split on case (e.g. 'Base' vs 'base').
    """
    from konsol.epm.budget_grain import is_valid_layer, normalize_layer

    _assert_budget_write_access(data)
    cycle = _resolve_budget_cycle(data)
    _assert_cycle_open(cycle)
    dims = _budget_dims()
    ident = _line_identity(data, dims)

    by_layer = {}
    for p in data["periods"]:
        layer = normalize_layer(p.get("layer", "base"))
        if not is_valid_layer(layer):
            frappe.throw(
                f"Invalid layer '{layer}'. Allowed: {', '.join(sorted(VALID_LAYERS))}",
                frappe.ValidationError,
            )
        by_layer.setdefault(layer, []).append(p)

    sheet_names = []
    for layer, periods in by_layer.items():
        # One retry to absorb the concurrent-create race on a brand-new sheet.
        for _attempt in range(2):
            sheet = _get_or_create_sheet(cycle["name"], data, layer)
            for p in periods:
                fp = int(p.get("period", p.get("fiscal_period", 0)))
                _set_cell(sheet, ident, fp, float(p.get("amount", 0)), dims)
            try:
                sheet.save()
            except frappe.exceptions.DuplicateEntryError:
                frappe.db.rollback()  # concurrent create won — retry as update
                continue
            sheet_names.append(sheet.name)
            break
    return sheet_names


@frappe.whitelist(methods=["POST"])
def budget_save():
    """Save a budget line into its (cycle, entity, layer) sheet(s).

    The Budget Cycle for (scenario, fiscal_year) is auto-created Open if absent;
    writes are rejected once it is locked.
    """
    data = _get_json_body()
    _validate_budget_fields(data)
    sheets = _upsert_budget_line(data)
    return {"sheets": sheets, "name": sheets[0] if sheets else None}


def _budget_entry_dims():
    """The budget dimension fields on Budget Entry Line (the Published
    in_budget Dimensions schema_apply provisioned), read once per request."""
    from konsol.epm.doctype.budget_entry_line.budget_entry_line import budget_dimension_fields

    return budget_dimension_fields()


def _cell_whole(data, field):
    """A cell's year or period as a whole number, refused by name otherwise."""
    try:
        return int(str(data[field]).strip())
    except (TypeError, ValueError):
        frappe.throw(f"{field} must be a whole number (got {data[field]!r})", frappe.ValidationError)


def _split_signed_amount(value):
    """Excel's signed amount as (debit, credit), the TB's sign convention
    (Q3-a): a positive amount is a debit, a negative one a credit of its
    absolute value. Read exactly, as every intake reads an amount."""
    from konsol.tb_balance_model import NotFinite, read_amount

    try:
        amount = read_amount(value)
    except (NotFinite, ValueError):
        frappe.throw(f"amount must be a finite number (got {value!r})", frappe.ValidationError)
    zero = amount * 0
    return (amount, zero) if amount >= 0 else (zero, -amount)


def _judge_budget_cell(data, dims, debit, credit):
    """Run one cell through konsol.budget_intake, the one entry every budget
    writer calls, as a one-row budget table. Returns (report item, row) when
    the cell is ready; otherwise refuses with every problem the intake found.

    Currency and amount basis come from the request and are never defaulted
    (Q2-c, the TB's declared-currency rule): a request without them is
    refused by the intake, by name.
    """
    from konsol import budget_intake

    given = [d for d in dims if data.get(d)]
    header = ["scenario", "layer", "data_area_id", "fiscal_year", "fiscal_period",
              "main_account", "debit", "credit", "currency"] + given
    row = [data.get("scenario_id"), data.get("layer"), data.get("data_area_id"),
           data.get("fiscal_year"), data.get("fiscal_period"), data.get("main_account"),
           format(debit, "f"), format(credit, "f"), data.get("currency") or ""]
    row += [data[d] for d in given]
    try:
        plan = budget_intake.plan([header, row], form_basis=data.get("amount_basis") or "")
    except ValueError as error:
        frappe.throw(str(error), frappe.ValidationError)
    (item,) = plan["report"]
    if not item["ok"]:
        frappe.throw("\n".join(item["errors"]), frappe.ValidationError)
    ((judged,),) = plan["groups"].values()
    return item, judged


def _entry_line(entry, ident):
    """The entry's line for account + dimension values, or None. A blank
    dimension and an unset one are the same value, as Budget Entry judges."""
    for line in entry.get("lines") or []:
        if all((line.get(k) or "") == v for k, v in ident.items()):
            return line
    return None


def _line_amount(line):
    """A line's signed amount (debit − credit), None when there is no line."""
    if line is None:
        return None
    return float(line.get("debit") or 0) - float(line.get("credit") or 0)


def _refuse_entry_mismatch(entry, item):
    """One entry holds one currency and one amount basis: a cell that states
    another is refused, naming the entry, never written over it."""
    for field, label in (("currency", "currency"), ("amount_basis", "amount basis")):
        held, given = entry.get(field) or "", item[field]
        if held != given:
            frappe.throw(
                f"Budget Entry {entry.name} holds {label} {held!r}; this cell gives {given!r}. "
                f"One entry holds one {label}.",
                frappe.ValidationError,
            )


def _set_entry_line(entry, ident, judged):
    line = _entry_line(entry, ident)
    if line is None:
        line = entry.append("lines", dict(ident))
    line.debit = float(judged["debit"])
    line.credit = float(judged["credit"])
    return line


@frappe.whitelist(methods=["POST"])
def budget_cell_save():
    """Save one Excel budget cell as one Budget Entry Line (konsol#348 X1).

    Designed for EPMSAVE() immediate writes. The cell goes through
    konsol.budget_intake.plan, the one entry every budget writer calls: the
    period must be a declared Regular period of its year (no 1–12 clamp), the
    layer a declared Budget Layer the user may write (no default), and the
    (scenario, year) must have a draft Budget Cycle (none is created, Q6-b);
    the entity, account and dimension access checks run there too.

    Excel sends a signed ``amount``; it is stored as a debit when positive and
    a credit of its absolute value when negative (Q3-a). ``currency`` and
    ``amount_basis`` are required: neither is defaulted.

    The cell finds or creates the Budget Entry for (cycle, entity, layer,
    period) and upserts the one line for its account and dimension values.
    Only that entry is saved; nothing here commits.

    Optional optimistic locking (omit for last-write-wins): ``base_modified``
    is the Budget Entry ``modified`` the client last read; if it is stale the
    write is refused with an HTTP 409 ``conflict`` payload (the line's current
    signed amount, None when the line does not exist, and
    ``current_modified``). A successful save echoes the new ``modified``.
    """
    data = _get_json_body()

    required = ["scenario_id", "data_area_id", "fiscal_year",
                "main_account", "fiscal_period", "amount", "layer"]
    missing = [f for f in required if data.get(f) in (None, "")]
    if missing:
        frappe.throw(
            f"Missing required fields: {', '.join(missing)}",
            frappe.ValidationError,
        )

    _cell_whole(data, "fiscal_year")
    fp = _cell_whole(data, "fiscal_period")
    debit, credit = _split_signed_amount(data["amount"])
    amount = float(debit - credit)

    # The add-in's one `dimensions` argument (konsol#287): checked against the
    # declared budget dimensions and moved onto the fields every check below
    # reads, before any of them runs.
    dims = _budget_entry_dims()
    _apply_budget_dimensions(data, dims)
    hierarchy_err = _hierarchy_without_node_error(data)
    if hierarchy_err:
        frappe.throw(hierarchy_err, frappe.ValidationError)

    node_code = _hierarchy_node_from_req(data)
    if node_code:
        from konsol.hierarchy_query import validate_hierarchy_write
        info, err = validate_hierarchy_write(
            _hierarchy_name_from_req(data),
            node_code,
            fiscal_year=int(data["fiscal_year"]),
            fiscal_period=fp,
        )
        if err:
            frappe.throw(err, frappe.ValidationError)
        given = data.get(info["dimension"])
        if given and given != info["member_code"]:
            frappe.throw(
                f"Dimension '{info['dimension']}' is given as '{given}', but "
                f"node '{node_code}' writes it as '{info['member_code']}'",
                frappe.ValidationError,
            )
        data[info["dimension"]] = info["member_code"]

    # Every check (period, layer, writer role, Cycle, currency, basis, access)
    # runs in budget_intake, after the dimensions and hierarchy are applied.
    item, judged = _judge_budget_cell(data, dims, debit, credit)

    ident = _line_identity(data, dims)
    base_modified = data.get("base_modified")
    entry_key = {
        "cycle": item["cycle"],
        "data_area_id": item["entity"],
        "layer": item["layer"],
        "fiscal_period": item["fiscal_period"],
    }

    # Resolve -> mutate -> save, with one retry. Cells of one entry serialise
    # on the FOR UPDATE lock below. A concurrent create of the same new entry
    # hits a duplicate-key error; the retry then finds it and takes the locked
    # update path.
    for _attempt in range(2):
        name = frappe.db.get_value("Budget Entry", entry_key, "name")

        if name:
            current_modified = frappe.db.get_value(
                "Budget Entry", name, "modified", for_update=True)
            doc = frappe.get_doc("Budget Entry", name)
            if base_modified and str(current_modified) != str(base_modified):
                frappe.local.response.http_status_code = 409
                return {
                    "status": "conflict",
                    "name": name,
                    "fiscal_period": fp,
                    "layer": item["layer"],
                    "your_amount": amount,
                    "current_amount": _line_amount(_entry_line(doc, ident)),
                    "current_modified": str(current_modified),
                }
            _refuse_entry_mismatch(doc, item)
            _set_entry_line(doc, ident, judged)
            doc.save()
            return {"status": "ok", "name": doc.name, "value": amount,
                    "modified": str(doc.modified)}

        # Create path.
        doc = frappe.new_doc("Budget Entry")
        doc.update(entry_key)
        doc.update({"currency": item["currency"], "amount_basis": item["amount_basis"],
                    "source": "Excel"})
        _set_entry_line(doc, ident, judged)
        try:
            doc.insert()
        except frappe.exceptions.DuplicateEntryError:
            frappe.db.rollback()  # concurrent create won — retry as update
            continue
        return {"status": "ok", "name": doc.name, "value": amount,
                "modified": str(doc.modified)}

    frappe.throw(
        frappe._("Could not save budget cell due to concurrent writes; please retry."),
        frappe.ValidationError,
    )


@frappe.whitelist(methods=["POST"])
def budget_save_batch():
    """Save multiple budget lines at once."""
    items = _get_json_body()
    if not isinstance(items, list):
        frappe.throw("Expected a JSON array", frappe.ValidationError)

    results = []
    errors = []
    for i, data in enumerate(items):
        try:
            _validate_budget_fields(data)
            sheets = _upsert_budget_line(data)
            results.append({"name": sheets[0] if sheets else None,
                            "sheets": sheets, "index": i})
            errors.append(None)
        except frappe.PermissionError as e:
            # Surface authorization denials per item (don't bury as a generic
            # failure) so the client can flag exactly which lines were blocked.
            results.append(None)
            errors.append({"index": i, "error": str(e)})
        except Exception:
            frappe.log_error("Budget save failed", frappe.get_traceback())
            results.append(None)
            errors.append({"index": i, "error": "Save failed — check server logs"})

    response = {"results": results}
    if any(e is not None for e in errors):
        response["errors"] = errors
    return response


# ---------------------------------------------------------------------------
# PRD-8: Consolidation Hierarchy API
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_hierarchy_tree(consolidation_group=None, as_of=None):
    """Return consolidation hierarchy as nested JSON tree.

    Uses Frappe's native tree (is_tree=1) with lft/rgt for efficient subtree queries.

    F2: ``ownership_pct`` and ``consolidation_method`` are no longer columns on
    Consolidation Group — ownership is temporal and lives in Ownership Period.
    Selecting them here would have thrown MySQL 1054 on any site where the patch
    had dropped them (and on every fresh install, where they never existed). The
    keys stay in the response, resolved from the period covering ``as_of``
    (default today), so the shape of a whitelisted endpoint does not change;
    they are None for a node with no period, which is a real state now.
    """
    filters = {}
    if consolidation_group:
        root = frappe.get_all(
            "Consolidation Group",
            filters={"consolidation_group": consolidation_group},
            fields=["lft", "rgt"],
            limit=1,
            # The filter matches the root and every member row; the root of
            # the nested set is the smallest lft (konsol#358).
            order_by="lft asc",
        )
        if root:
            filters = [
                ["lft", ">=", root[0].lft],
                ["rgt", "<=", root[0].rgt],
            ]

    docs = frappe.get_all(
        "Consolidation Group",
        filters=filters,
        fields=[
            "name", "consolidation_group", "data_area_id", "entity_name",
            "parent_consolidation_group", "lft", "rgt",
            "reporting_currency", "goodwill_method",
        ],
        order_by="lft asc",
        limit_page_length=0,
    )

    by_name = {d.name: d for d in docs}
    ownership = _ownership_as_of(as_of)

    def build_node(doc):
        period = ownership.get((doc.consolidation_group, doc.data_area_id or ""), {})
        node = {
            "name": doc.name,
            "consolidation_group": doc.consolidation_group,
            "data_area_id": doc.data_area_id,
            "entity_name": doc.entity_name,
            "parent_group": doc.parent_consolidation_group,
            # what this node's PARENT owns of it, on as_of
            "ownership_pct": period.get("ownership_pct"),
            "consolidation_method": period.get("consolidation_method"),
            "children": [],
        }
        for d in docs:
            if d.parent_consolidation_group == doc.name:
                node["children"].append(build_node(d))
        return node

    tree = []
    for d in docs:
        if not d.parent_consolidation_group or d.parent_consolidation_group not in by_name:
            tree.append(build_node(d))

    return {"tree": tree}


def _ownership_as_of(as_of=None):
    """{(group, entity): {ownership_pct, consolidation_method}} on a date.

    Submitted periods only, and only the one whose range covers the date — the
    same rule the warehouse applies. A node with none is absent from the map
    rather than defaulted: "no ownership recorded" and "0%" are different
    answers, and conflating them is the bug F2 removed.
    """
    on = frappe.utils.getdate(as_of or frappe.utils.nowdate())
    found = {}
    for p in frappe.get_all(
        "Ownership Period",
        filters={"docstatus": 1},
        fields=["consolidation_group", "data_area_id", "effective_date",
                "end_date", "ownership_pct", "consolidation_method"],
        order_by="effective_date asc",
        limit_page_length=0,
    ):
        if frappe.utils.getdate(p.effective_date) > on:
            continue
        if p.end_date and frappe.utils.getdate(p.end_date) < on:
            continue
        found[(p.consolidation_group, p.data_area_id or "")] = {
            "ownership_pct": p.ownership_pct,
            "consolidation_method": p.consolidation_method,
        }
    return found


# ---------------------------------------------------------------------------
# Reporting Hierarchy API (management rollups — not legal entities)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_reporting_hierarchy_tree(hierarchy_name=None, as_of=None):
    """Return a management reporting hierarchy as nested JSON, as of a date.

    Each Reporting Hierarchy Member row is one dated tranche of its code
    (konsol#220). ``as_of`` (ISO date, default today) keeps the rows whose
    window covers it. A node's children are the kept rows whose parent_member
    has the node's CODE, so a child linked to an older tranche of its parent
    still sits under the tranche of the date. Each node shows its tranche's
    label and effective_from / effective_to (None while open).
    Legal-entity trees use get_hierarchy_tree (Consolidation Group).
    """
    if not hierarchy_name:
        frappe.throw("hierarchy_name is required")
    if as_of in (None, ""):
        as_of = frappe.utils.today()
    as_of = str(as_of)
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            raise ValueError(as_of)
        date.fromisoformat(as_of)
    except ValueError:
        frappe.throw("as_of must be a date (YYYY-MM-DD).")

    header = frappe.db.get_value(
        "Reporting Hierarchy",
        {"hierarchy_name": hierarchy_name},
        ["name", "dimension", "label", "status"],
        as_dict=True,
    )
    if not header:
        frappe.throw(f"Reporting Hierarchy '{hierarchy_name}' not found")

    members = frappe.get_all(
        "Reporting Hierarchy Member",
        filters={"reporting_hierarchy": header.name},
        fields=[
            "name", "parent_member", "member_code", "member_label", "is_group",
            "effective_from", "effective_to",
        ],
        order_by="member_code asc",
        limit_page_length=0,
    )
    code_of = {m.name: m.member_code for m in members}

    def iso(value):
        return str(value)[:10] if value else None

    # An undated row means "always" (the konsol#220 migrate patch's reading).
    covering = [
        m for m in members
        if (iso(m.effective_from) or "1900-01-01") <= as_of
        and (not m.effective_to or as_of <= iso(m.effective_to))
    ]
    covered_codes = {m.member_code for m in covering}

    def parent_code(doc):
        return code_of.get(doc.parent_member) if doc.parent_member else None

    def build_node(doc, path):
        path = path | {doc.member_code}
        return {
            "name": doc.name,
            "member_code": doc.member_code,
            "member_label": doc.member_label,
            "is_group": bool(doc.is_group),
            "effective_from": iso(doc.effective_from),
            "effective_to": iso(doc.effective_to),
            "children": [
                build_node(child, path)
                for child in covering
                if parent_code(child) == doc.member_code
                and child.member_code not in path
            ],
        }

    # A row whose parent code has no tranche on the date stays visible as a root.
    roots = [
        build_node(m, frozenset())
        for m in covering
        if parent_code(m) not in covered_codes
    ]

    return {
        "hierarchy_name": hierarchy_name,
        "dimension": header.dimension,
        "label": header.label,
        "status": header.status,
        "as_of": as_of,
        "tree": roots,
    }


# ---------------------------------------------------------------------------
# Airbyte Sync Webhook
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True, methods=["POST"])
def airbyte_sync_complete():
    """Webhook endpoint called by Airbyte after sync completes.

    Updates EPM Settings with sync timestamp, status, and row count.
    Publishes realtime event for UI refresh.

    Auth: accepts either a logged-in Frappe session OR a shared secret via
    X-Webhook-Secret header (configured in EPM Settings.webhook_secret).
    """
    settings = frappe.get_single("EPM Settings")

    if frappe.session.user == "Guest":
        secret = frappe.request.headers.get("X-Webhook-Secret", "")
        expected = (settings.get_password("webhook_secret", raise_exception=False) or "")
        # Constant-time comparison to avoid leaking the secret via timing.
        if not expected or not hmac.compare_digest(str(secret), str(expected)):
            frappe.throw("Unauthorized", frappe.AuthenticationError)

    data = _get_json_body()
    settings.last_airbyte_sync_at = now_datetime()
    settings.last_airbyte_sync_status = data.get("status", "Success")
    settings.last_airbyte_sync_rows = int(data.get("rows_synced", 0))
    settings.flags.ignore_permissions = True
    settings.save()

    # Per-connector sync status: if the payload names a connection that maps to
    # a registered Connector, update that connector instead of only the global
    # EPM Settings — Build Governance preflight reads per-connector status.
    connection_id = data.get("connection_id") or data.get("connectionId")
    if connection_id and frappe.db.table_exists("Connector"):
        names = frappe.get_all(
            "Connector",
            filters={"airbyte_connection_id": connection_id},
            pluck="name",
            limit_page_length=0,
        )
        for name in names:
            conn = frappe.get_doc("Connector", name)
            conn.last_sync_at = settings.last_airbyte_sync_at
            conn.last_sync_status = settings.last_airbyte_sync_status
            conn.last_sync_rows = settings.last_airbyte_sync_rows
            conn.flags.ignore_permissions = True
            conn.save()

    frappe.db.commit()

    frappe.publish_realtime(
        "airbyte_sync_complete",
        {
            "status": settings.last_airbyte_sync_status,
            "rows": settings.last_airbyte_sync_rows,
            "timestamp": str(settings.last_airbyte_sync_at),
        },
    )

    return {
        "status": "ok",
        "sync_status": settings.last_airbyte_sync_status,
        "rows": settings.last_airbyte_sync_rows,
    }


@frappe.whitelist()
def connector_health():
    """Per-connector sync health for the operator dashboard.

    Returns one object per connector with derived status, lag, entity counts,
    and last error. Read-gated by the Connector Health doctype (System Manager /
    EPM Admin / EPM Analyst / EPM User). The rows are maintained by the
    ``refresh_connector_health`` scheduler job.
    """
    if not frappe.has_permission("Connector Health", "read"):
        raise frappe.PermissionError("Not permitted to read Connector Health")
    return frappe.get_all(
        "Connector Health",
        fields=[
            "connector", "erp_source", "last_sync_status", "lag_minutes",
            "entities_loaded", "rows_emitted",
            "last_sync_end", "last_error", "checked_at",
        ],
        order_by="lag_minutes desc",
        limit_page_length=0,
    )


#: The governed group exchange rates (konsol#103): the one source of truth for
#: FX rates, published by konsol as TRUE rates (units of to per 1 from).
GOVERNED_FX_TABLE = "epm_staging.group_exchange_rates"
#: The declared calendar (konsol#189): a period's rate applies from its
#: start_date row in EPM Fiscal Year Period. An undeclared (year, period) has
#: no row and is joined out — never guessed month arithmetic.
FISCAL_PERIODS_TABLE = "epm_staging.fiscal_periods"
#: fiscal_periods is TRUNCATE+INSERTed on every EPM Fiscal Year save (#189
#: row 70f); two concurrent resyncs can leave a period twice, which would
#: double every joined rate. Join a one-row-per-period subquery instead of
#: the raw table so a duplicate row is collapsed, never multiplied.
FISCAL_PERIODS_DEDUP_SQL = (
    f"(SELECT fiscal_year, fiscal_period, min(start_date) AS start_date "
    f"FROM {FISCAL_PERIODS_TABLE} GROUP BY fiscal_year, fiscal_period)"
)


@frappe.whitelist()
def fx_rates(from_currency=None, to_currency=None, rate_type=None, as_of=None, fiscal_year=None,
             fiscal_period=None, limit=500):
    """Read-only: the group's governed exchange rates, the ones translation uses.

    One source of truth (decided 13 Sep 2026): the approved Group Exchange
    Rates konsol publishes to ``epm_staging.group_exchange_rates``, each the
    true rate, units of ``to_currency`` per 1 ``from_currency``. The ERP feed is
    not a rate here; it is only an input to Pre-fill from ERP. Login required;
    no write path. Optional filters:

      from_currency / to_currency  ISO codes (e.g. "EUR", "USD")
      rate_type                    "Closing" | "Average"
      fiscal_year / fiscal_period  one year, one period
      as_of                        periods starting on/before this date (YYYY-MM-DD)
      limit                        row cap (1..5000, default 500)

    Filter values are bound as ClickHouse HTTP query parameters: ``{name:Type}``
    in the SQL and ``param_<name>`` on the wire (#175: a bare name is read as a
    setting and the call fails with UNKNOWN_SETTING). Never interpolated; the
    table is a fixed literal and the limit is integer-cast.
    """
    import json

    from konsol.clickhouse import execute

    conds = []
    params = {}
    if from_currency:
        params["fc"] = str(from_currency).upper()
        conds.append("from_currency = {fc:String}")
    if to_currency:
        params["tc"] = str(to_currency).upper()
        conds.append("to_currency = {tc:String}")
    if rate_type:
        params["rt"] = str(rate_type)
        conds.append("rate_type = {rt:String}")
    if fiscal_year not in (None, ""):
        params["fy"] = int(fiscal_year)
        conds.append("fiscal_year = {fy:UInt16}")
    if fiscal_period not in (None, ""):
        params["fp"] = int(fiscal_period)
        conds.append("fiscal_period = {fp:UInt8}")
    if as_of:
        params["asof"] = str(as_of)
        conds.append("fp.start_date <= {asof:Date}")

    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 500
    lim = max(1, min(lim, 5000))

    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    sql = (
        "SELECT from_currency, to_currency, rate_type, fiscal_year, fiscal_period, "
        "toString(fp.start_date) AS period_start, rate, document "
        f"FROM {GOVERNED_FX_TABLE} "
        f"INNER JOIN {FISCAL_PERIODS_DEDUP_SQL} AS fp USING (fiscal_year, fiscal_period)"
        f"{where} "
        "ORDER BY from_currency, to_currency, rate_type, fiscal_year DESC, fiscal_period DESC "
        f"LIMIT {lim} FORMAT JSON"
    )
    raw = execute(sql, {f"param_{k}": v for k, v in params.items()})
    rows = json.loads(raw).get("data", []) if raw else []
    return {"rows": rows, "count": len(rows)}
