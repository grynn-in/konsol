"""Statement read endpoints for the close app (konsol#305 N51, N52;
stories 8.1 — P&L and BS, Provisional or Signed; 8.2 — line -> entity ->
account -> source in three clicks or fewer; 8.3 — the commentary per line
is in the payload; #305-W4-1, W4-4 (the sign-off label follows the run,
never the statement itself), W4-5 (``can_comment``); W4-E8, W4-E9, W4-E10,
W4-E11, W4-E19).

``get_statement(fiscal_year, fiscal_period, consolidation_group=None)``
(GET) returns one consolidation group's statement for a period: both
sections (``statement_model.statement``, N49), the sign-off label, the
per-heading commentary (M43), the declared-accounts setup gap (N41/N45),
the "entities not included" chip (W4-E10) and whether the caller may add
commentary.

``get_drill(fiscal_year, fiscal_period, consolidation_group, heading)``
(GET, N52) returns one statement heading's amount broken into layers at
entity grain (``drill_model.drill``, N50): the entity layer split by
entity (cut to the caller's scope, W4-E9), and every other layer
(eliminations, CTA, top-side journals, …) as its own row, never under an
entity (D2-5). The top-side row carries the period's postings from
``gold_consolidation_adjustments`` (W4-E11; the TB's own topside rows keep
only ``any(journal_id)``, V03, so the per-journal breakdown comes from the
adjustments table instead). ``consolidation_group`` is required (the
Numbers screen has already chosen one) and ``heading`` must be a
Published heading (``is_group``) of the chart; either failing throws, as
does an unknown group.

``export_statement(fiscal_year, fiscal_period, consolidation_group=None)``
(GET, story 8.5, decision #305-W5-3) sends the same statement and every
heading's drill as a downloaded .xlsx (``statement_export_model``). A
non-ok state is refused with its own message, never an empty file.

Nothing here is ever a silent empty statement:

- several root Consolidation Groups and none named -> ``state
  "choose_group"``, no ClickHouse read (W4-E8); none declared at all is
  the same state, with its own message;
- no Published Main Account -> ``state "no_chart"``, no ClickHouse read;
- a ClickHouse failure (down, or the relation not yet built) ->
  ``state "not_built"`` or ``"error"``, naming the exception;
  ``statement`` stays ``None`` — never an empty one;
- a NULL warehouse amount, or a Balance Sheet heading with no declared
  ``normal_balance`` (``statement_model.STATEMENT_HEADING_SIDE_UNDECLARED``,
  AMENDED 4 Oct #305-W4-2 2a-ii) — ``statement_model.statement`` raises
  ``ValueError`` naming the problem (the heading side error names every
  undeclared heading and what to set). A NULL amount is a warehouse
  failure (``state "error"``); an undeclared heading side is a setup gap,
  not an outage, so it is ``state "setup_gap"`` instead (S5, #305-W4-R41d).
  Either way: never a 500, and never a statement shown with a side or an
  amount silently guessed.

The declared-accounts gap, the sign-off label and ``can_comment`` depend
only on the period (not on the chosen group or the chart), so they are
read once, early, and carried through every state — including
``choose_group`` and ``no_chart``, so the Numbers screen can show them
before a group or a chart exists.

Query count is constant in the number of headings and entities: MariaDB
reads are periods 1, groups 1, declared-accounts (Close Settings 2 +
Main Account <= 1, inside ``signoff_gate.statement_accounts``), the
sign-off run 1, the chart (Main Account) <= 1,
``signoff_gate.in_scope_entities``'s own reads, commentary <= 1, User
<= 1. ClickHouse is read exactly twice, and only when a group is chosen
and the chart is non-empty; otherwise 0.
"""
import frappe

from konsol import fiscal_calendar
from konsol.close import (
    ch_read, drill_model, signoff_gate, statement_export_model, statement_model)
from konsol.close.timefmt import zoned_iso
from konsol.entity_permissions import allowed_entity_codes

#: Who reads the Numbers screen (Close Lead, Group Accountant, Viewer;
#: the Entity Accountant never gets the `numbers` slug, W4-E20).
STATEMENT_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")

#: Who may save commentary (commentary_api.ROLES). Duplicated here only to
#: judge ``can_comment``; ``commentary_api.save_commentary`` is the one
#: writer (#305-W4-5 5b: allowed while Open, signed or not; refused once
#: Closed or Locked).
COMMENT_ROLES = ("EPM Admin", "EPM Analyst", "System Manager")

#: The Published chart, with the BS sides statement_model needs (N49):
#: a blank ``normal_balance`` on a displayed BS heading is a setup gap,
#: never a silent default — see the module docstring.
ACCOUNT_FIELDS = ["name", "account_name", "parent_account", "is_group",
                  "statement_section", "lft", "normal_balance"]

#: W4-E8: the root Consolidation Group rows (no ``data_area_id``).
_ROOT_FILTER = {"data_area_id": ["is", "not set"]}

#: The two warehouse reads (through ``ch_read.rows`` only, X01). Neither
#: ever names a ``dim_*`` column (konsolidat#245: the dimension columns
#: are summed over, not broken out).
#: The sum is aliased ``amt``, not ``amount`` (measured live 4 Oct, N52/N53):
#: ``sum(amount) AS amount`` beside ``countIf(amount IS NULL)`` in the same
#: SELECT list makes ClickHouse substitute the alias into the ``countIf``
#: expression, raising ``Code: 184 ILLEGAL_AGGREGATION`` — the same
#: alias/column collision CLAUDE.md's CYCLIC_ALIASES trap warns about, here
#: surfacing as a different error. ``_tb_row`` renames ``amt`` back to
#: ``amount`` before any caller sees the row (N52's ``_drill_row`` precedent).
_TB_SQL = (
    "SELECT fiscal_year, fiscal_period, main_account, adjustment_type, "
    "sum(amount) AS amt, countIf(amount IS NULL) AS null_rows "
    "FROM epm_gold.gold_fully_consolidated_tb "
    "WHERE consolidation_group = {group:String} AND fiscal_year <= {fy:UInt32} "
    "GROUP BY fiscal_year, fiscal_period, main_account, adjustment_type"
)
_ENTITY_SQL = (
    "SELECT DISTINCT data_area_id FROM epm_gold.gold_fully_consolidated_tb "
    "WHERE consolidation_group = {group:String} AND fiscal_year = {fy:UInt32} "
    "AND fiscal_period = {fp:UInt16} AND adjustment_type = 'entity'"
)

#: N52's entity-grain read for one heading's leaf accounts. The ``cta``
#: branch (rows carrying the literal ``main_account 'CTA'``, never one of
#: the heading's own leaf codes) is added only when the drilled heading
#: holds the declared CTA account (D2-5: the CTA row is never under an
#: entity, so it is never reached through ``main_account IN {accounts}``).
#:
#: The sum is aliased ``amt``, not ``amount`` (measured live 4 Oct, the
#: same ``_TB_SQL`` collision N53 later fixed): ``sum(amount) AS amount``
#: beside ``countIf(amount IS NULL)`` in the same SELECT list makes
#: ClickHouse substitute the alias into the ``countIf`` expression, raising
#: ``Code: 184 ILLEGAL_AGGREGATION`` — the same alias/column collision
#: CLAUDE.md's CYCLIC_ALIASES trap warns about, here surfacing as a
#: different error. ``_drill_row`` renames it back to ``amount`` before
#: ``_tb_row`` (which, after N53, also accepts ``amt`` directly).
_DRILL_SQL = (
    "SELECT fiscal_year, fiscal_period, data_area_id, main_account, adjustment_type, "
    "sum(amount) AS amt, countIf(amount IS NULL) AS null_rows "
    "FROM epm_gold.gold_fully_consolidated_tb "
    "WHERE consolidation_group = {group:String} AND fiscal_year <= {fy:UInt32} "
    "AND (main_account IN {accounts:Array(String)}) "
    "GROUP BY fiscal_year, fiscal_period, data_area_id, main_account, adjustment_type"
)
_DRILL_SQL_WITH_CTA = (
    "SELECT fiscal_year, fiscal_period, data_area_id, main_account, adjustment_type, "
    "sum(amount) AS amt, countIf(amount IS NULL) AS null_rows "
    "FROM epm_gold.gold_fully_consolidated_tb "
    "WHERE consolidation_group = {group:String} AND fiscal_year <= {fy:UInt32} "
    "AND (main_account IN {accounts:Array(String)} OR adjustment_type = 'cta') "
    "GROUP BY fiscal_year, fiscal_period, data_area_id, main_account, adjustment_type"
)
#: The period's own postings for the heading's accounts (W4-E11): the
#: top-side drill row's ``journals`` list, never the row's amount (which
#: still comes from ``_DRILL_SQL``'s TB rows, like every other layer).
_DRILL_JOURNALS_SQL = (
    "SELECT journal_id, adjustment_type, data_area_id, main_account, "
    "sum(net_amount) AS amount, any(description) AS description, "
    "any(posted_by) AS posted_by, any(approved_by) AS approved_by "
    "FROM epm_gold.gold_consolidation_adjustments "
    "WHERE consolidation_group = {group:String} AND fiscal_year = {fy:UInt32} "
    "AND fiscal_period = {fp:UInt16} AND main_account IN {accounts:Array(String)} "
    "GROUP BY journal_id, adjustment_type, data_area_id, main_account"
)


def _period_key(fiscal_year, fiscal_period):
    try:
        return int(fiscal_year), int(fiscal_period)
    except (TypeError, ValueError):
        frappe.throw(f"FY{fiscal_year} P{fiscal_period} is not a period: "
                     "pass the fiscal year and period as whole numbers.")


def _find_period(key, period_rows):
    for row in period_rows:
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            return row
    frappe.throw(
        "FY%d P%02d is not a declared period: declare it in EPM Fiscal Year." % key)


def _tb_row(row):
    """A ``gold_fully_consolidated_tb`` row, JSON-safe and int/float-typed
    (ClickHouse's JSON format may hand back a numeric as a string, E5-P15).
    ``_TB_SQL`` sends the sum under the wire alias ``amt`` (N53: the alias
    cannot be ``amount`` without colliding with the sibling ``countIf``);
    rename it to ``amount`` here, the one place every caller reads from, so
    nothing downstream needs to know the wire alias ever differed."""
    out = dict(row)
    if "amt" in out:
        out["amount"] = out.pop("amt")
    out["fiscal_year"] = int(out["fiscal_year"])
    out["fiscal_period"] = int(out["fiscal_period"])
    out["null_rows"] = int(out.get("null_rows") or 0)
    amount = out.get("amount")
    out["amount"] = None if amount is None else float(amount)
    return out


def _signoff_state(key):
    # Lazy: assertion_run imports close modules (N45 precedent) and this
    # module must still import cleanly with no bench.
    from konsol.consolidation.doctype.assertion_run.assertion_run import (
        RE_SIGN_NEEDED, SIGNED_STATES, latest_close_run)

    run = latest_close_run(*key)
    if run is None:
        return {"state": "provisional", "run": None}
    status = run.get("signoff_status")
    if status in SIGNED_STATES:
        state = "signed"
    elif status == RE_SIGN_NEEDED:
        state = "resign_needed"
    else:
        state = "provisional"
    return {"state": state, "run": run["name"]}


def _commentary(consolidation_group, key):
    """``{heading: {"name", "text", "by", "at", "modified"}}`` (W4-E14,
    W4-E15: commentary attaches only to chart headings). ``modified`` is
    the raw ``str(modified)`` token M44's stale-edit check compares."""
    rows = frappe.get_all(
        "Statement Commentary",
        filters={"consolidation_group": consolidation_group,
                 "fiscal_year": key[0], "fiscal_period": key[1]},
        fields=["name", "heading", "text", "modified", "modified_by"],
        limit_page_length=0,
    )
    users = {}
    actors = sorted({r["modified_by"] for r in rows if r.get("modified_by")})
    if actors:
        users = {
            u["name"]: u.get("full_name") or u["name"]
            for u in frappe.get_all(
                "User", filters={"name": ["in", actors]},
                fields=["name", "full_name"], limit_page_length=0)
        }
    tz = frappe.utils.get_system_timezone()
    out = {}
    for row in rows:
        out[row["heading"]] = {
            "name": row["name"],
            "text": row.get("text") or "",
            "by": users.get(row.get("modified_by"), row.get("modified_by")),
            "at": zoned_iso(row["modified"], tz),
            "modified": str(row["modified"]),
        }
    return out


def _not_included(key, entity_rows):
    """W4-E10: the close's in-scope entities minus the entity layer's
    entities for the period. A scoped caller's hidden entities are
    counted, never named (W4-E9's display courtesy, applied here too)."""
    in_scope = set(signoff_gate.in_scope_entities(*key))
    got = {r["data_area_id"] for r in entity_rows if r.get("data_area_id")}
    missing = sorted(in_scope - got)
    allowed = allowed_entity_codes()
    if allowed is None:
        visible, hidden = missing, 0
    else:
        allowed = set(allowed)
        visible = [e for e in missing if e in allowed]
        hidden = len(missing) - len(visible)
    return {"count": len(missing), "entities": visible, "hidden": hidden}


def _error_message(exc):
    names = sorted(ch_read.error_names(exc))
    return type(exc).__name__ + (" (%s)" % ", ".join(names) if names else "")


def _heading_of(code, accounts):
    """The chart heading (``parent_account``) of a declared leaf code, or
    ``None`` when undeclared — N52's own copy of drill_model's private
    helper, since this module reads the chart to decide which SQL to run
    before any drill row exists to call it on."""
    if not code:
        return None
    return (accounts.get(code) or {}).get("parent_account")


def _leaf_codes(heading, accounts):
    """Every Published leaf under ``heading``, for the ``{accounts:
    Array(String)}`` bind in N52's two heading-scoped ClickHouse reads."""
    return sorted(
        code for code, entry in accounts.items()
        if not entry.get("is_group") and entry.get("parent_account") == heading
    )


def _sql_array(codes):
    """ClickHouse's external representation of an ``Array(String)`` query
    parameter: a literal array string (measured live 4 Oct — ``requests``
    otherwise repeats the key once per element, which ClickHouse cannot
    parse as an array: ``CANNOT_READ_ARRAY_FROM_TEXT``)."""
    return "[" + ",".join("'%s'" % c.replace("\\", "\\\\").replace("'", "\\'") for c in codes) + "]"


def _drill_row(row):
    """``_DRILL_SQL``'s row, renamed from its ``amt`` alias (see the SQL's
    own comment) to ``_tb_row``'s expected ``amount`` key."""
    renamed = dict(row)
    renamed["amount"] = renamed.pop("amt", None)
    return _tb_row(renamed)


def _journal_row(row):
    """A ``gold_consolidation_adjustments`` row, JSON-safe and renamed to
    ``drill_model.drill``'s expected ``net_amount`` key (the SQL sums
    ``net_amount`` but aliases the column ``amount`` on the wire, the same
    convention as every other ClickHouse read here)."""
    out = dict(row)
    amount = out.pop("amount", None)
    out["net_amount"] = None if amount is None else float(amount)
    return out


def _line_for_heading(stmt, heading):
    """The real statement line for ``heading`` (R31: the drill's total is
    checked against this, never a hand-built number)."""
    for section in stmt["sections"]:
        for line in section["lines"]:
            if line.get("heading") == heading:
                return line
    frappe.throw(f"{heading} has no statement line (a bug).")


def _drill_keys(period_rows, key, section):
    """The N48 key set for ``heading``'s basis: the current period alone
    for Profit and Loss, every declared key up to it (cumulative, Opening
    included) for a Balance Sheet heading — N49's own window, reused
    (``statement_model._keys_up_to``) rather than re-derived."""
    if section == statement_model.PL:
        return {key}
    return statement_model._keys_up_to(period_rows, key)


@frappe.whitelist(methods=["GET"])
def get_statement(fiscal_year, fiscal_period, consolidation_group=None):
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    return _statement(fiscal_year, fiscal_period, consolidation_group)[0]


def _statement(fiscal_year, fiscal_period, consolidation_group):
    """``(payload, reads)``: ``get_statement``'s payload, and — on ``ok``
    only, else ``None`` — the reads it was built from (``key``,
    ``period_rows``, ``accounts``, ``declared``, ``tb_rows``), so
    ``export_statement`` drills every heading without reading them again.
    The caller has already run ``frappe.only_for``."""
    key = _period_key(fiscal_year, fiscal_period)
    period_rows = fiscal_calendar.fiscal_period_rows()
    period_row = _find_period(key, period_rows)

    groups = frappe.get_all(
        "Consolidation Group", filters=_ROOT_FILTER,
        fields=["consolidation_group", "reporting_currency"], limit_page_length=0,
    )
    group_by_name = {g["consolidation_group"]: g for g in groups}

    chosen, group_note = None, None
    state, message = "ok", None
    if consolidation_group:
        if consolidation_group not in group_by_name:
            frappe.throw(f"{consolidation_group} is not a consolidation group.")
        chosen = consolidation_group
    elif len(groups) == 1:
        chosen = groups[0]["consolidation_group"]
        group_note = "The only consolidation group."
    elif groups:
        state, message = "choose_group", "Choose a consolidation group."
    else:
        state, message = (
            "choose_group",
            "No consolidation group is declared yet: create one in Consolidation Group.",
        )

    reporting_currency = (
        group_by_name[chosen].get("reporting_currency") if chosen else None
    )

    # Independent of the group/chart choice above (N41/N45, #305-W4-4,
    # #305-W4-5): read once, carried through every state.
    declared = signoff_gate.statement_accounts()
    signoff = _signoff_state(key)
    roles = set(frappe.get_roles(frappe.session.user))
    can_comment = bool(roles & set(COMMENT_ROLES)) and period_row.get("status") == "Open"

    result = {
        "period": {
            "fiscal_year": key[0], "fiscal_period": key[1],
            "code": period_row.get("period_code"), "status": period_row.get("status"),
            "period_type": period_row.get("period_type"),
        },
        "groups": groups,
        "consolidation_group": chosen,
        "group_note": group_note,
        "reporting_currency": reporting_currency,
        "state": state,
        "message": message,
        "signoff": signoff,
        "statement": None,
        "gap": declared.get("gap"),
        "not_included": None,
        "commentary": {},
        "can_comment": can_comment,
    }
    if state != "ok":
        return result, None

    accounts = frappe.get_all(
        "Main Account", filters={"status": "Published"},
        fields=ACCOUNT_FIELDS, limit_page_length=0,
    )
    if not accounts:
        result["state"] = "no_chart"
        result["message"] = "Publish the group chart (Main Account) first."
        return result, None
    accounts_map = {a["name"]: a for a in accounts}

    try:
        tb_rows = [
            _tb_row(r) for r in ch_read.rows(_TB_SQL, {"group": chosen, "fy": key[0]})
        ]
        entity_rows = ch_read.rows(
            _ENTITY_SQL, {"group": chosen, "fy": key[0], "fp": key[1]})
    except Exception as e:  # noqa: BLE001 — any failure means "can't say", never 0 rows
        result["state"] = "not_built" if ch_read.not_built(e) else "error"
        result["message"] = _error_message(e)
        return result, None

    try:
        stmt = statement_model.statement(tb_rows, accounts_map, period_rows, key, declared)
    except ValueError as e:
        # A NULL warehouse amount is a warehouse failure ("error"); a BS
        # heading with no declared normal_balance
        # (STATEMENT_HEADING_SIDE_UNDECLARED) is a setup gap, not an
        # outage, so it gets its own state (S5) — never a 500, never a
        # guessed statement either way.
        message = str(e)
        is_setup_gap = message.startswith(statement_model.STATEMENT_HEADING_SIDE_UNDECLARED)
        result["state"] = "setup_gap" if is_setup_gap else "error"
        result["message"] = message
        return result, None

    result["statement"] = stmt
    result["not_included"] = _not_included(key, entity_rows)
    result["commentary"] = _commentary(chosen, key)
    reads = {"key": key, "period_rows": period_rows, "accounts": accounts_map,
             "declared": declared, "tb_rows": tb_rows}
    return result, reads


@frappe.whitelist(methods=["GET"])
def get_drill(fiscal_year, fiscal_period, consolidation_group, heading):
    # Literal tuple, not the STATEMENT_ROLES constant: the AST contract
    # checker (test_close_api_contract.py) requires ast.literal_eval on
    # only_for's argument, the same reason get_statement repeats it.
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key = _period_key(fiscal_year, fiscal_period)
    period_rows = fiscal_calendar.fiscal_period_rows()
    period_row = _find_period(key, period_rows)

    groups = frappe.get_all(
        "Consolidation Group", filters=_ROOT_FILTER,
        fields=["consolidation_group", "reporting_currency"], limit_page_length=0,
    )
    group_by_name = {g["consolidation_group"]: g for g in groups}
    if not consolidation_group or consolidation_group not in group_by_name:
        frappe.throw(f"{consolidation_group} is not a consolidation group.")

    accounts = frappe.get_all(
        "Main Account", filters={"status": "Published"},
        fields=ACCOUNT_FIELDS, limit_page_length=0,
    )
    accounts_map = {a["name"]: a for a in accounts}

    entry = accounts_map.get(heading)
    if entry is None or not entry.get("is_group"):
        frappe.throw(f"{heading} is not a statement heading of the group chart.")

    declared = signoff_gate.statement_accounts()
    cta_heading = _heading_of(declared.get("cta_account"), accounts_map)
    leaf_codes = _leaf_codes(heading, accounts_map)

    result = {
        "period": {
            "fiscal_year": key[0], "fiscal_period": key[1],
            "code": period_row.get("period_code"), "status": period_row.get("status"),
            "period_type": period_row.get("period_type"),
        },
        "consolidation_group": consolidation_group,
        "heading": heading,
        "state": "ok",
        "message": None,
        "drill": None,
    }

    drill_sql = _DRILL_SQL_WITH_CTA if heading == cta_heading else _DRILL_SQL
    accounts_param = _sql_array(leaf_codes)
    try:
        tb_rows = [
            _tb_row(r) for r in ch_read.rows(_TB_SQL, {"group": consolidation_group, "fy": key[0]})
        ]
        drill_rows = [
            _drill_row(r) for r in ch_read.rows(
                drill_sql, {"group": consolidation_group, "fy": key[0], "accounts": accounts_param})
        ]
        journal_rows = [
            _journal_row(r) for r in ch_read.rows(
                _DRILL_JOURNALS_SQL,
                {"group": consolidation_group, "fy": key[0], "fp": key[1], "accounts": accounts_param})
        ]
    except Exception as e:  # noqa: BLE001 — any failure means "can't say", never 0 rows
        result["state"] = "not_built" if ch_read.not_built(e) else "error"
        result["message"] = _error_message(e)
        return result

    try:
        stmt = statement_model.statement(tb_rows, accounts_map, period_rows, key, declared)
        line = _line_for_heading(stmt, heading)
        keys = _drill_keys(period_rows, key, entry.get("statement_section"))
        allowed = allowed_entity_codes()
        drill = drill_model.drill(
            drill_rows, journal_rows, accounts_map, heading, keys, declared, allowed, line)
    except ValueError as e:
        # A NULL warehouse amount, an undeclared BS heading side, or a
        # drill/statement total mismatch: a visible error state, never a
        # 500, never a silently wrong breakdown.
        result["state"] = "error"
        result["message"] = str(e)
        return result

    result["drill"] = drill
    return result


def _filename(period, group):
    """``numbers-FY2025P07-G1.xlsx``: the fiscal year and period (never the
    ``period_code``, which live is "P07" alone, measured 6 Oct) and the
    group, any character outside ``[A-Za-z0-9_-]`` replaced by ``_`` (a
    group name is free text; a header value must not carry quotes or
    separators)."""
    import re

    group = re.sub(r"[^A-Za-z0-9_-]", "_", str(group))
    return "numbers-FY%dP%02d-%s.xlsx" % (period["fiscal_year"], period["fiscal_period"], group)


def _statement_headings(stmt):
    return [line["heading"] for section in stmt["sections"]
            for line in section["lines"] if line.get("kind") == "heading"]


def _drill_reads(payload, reads):
    """``(drill_rows, journal_rows)`` for EVERY statement heading at once:
    ONE entity-grain read and ONE journals read over every Published leaf
    under a heading (never one read per heading) — ``get_drill``'s own SQL,
    with the CTA branch when the CTA heading is on the statement."""
    key, accounts = reads["key"], reads["accounts"]
    group = payload["consolidation_group"]
    headings = _statement_headings(payload["statement"])
    leaf_codes = sorted({code for heading in headings for code in _leaf_codes(heading, accounts)})
    cta_heading = _heading_of(reads["declared"].get("cta_account"), accounts)
    drill_sql = _DRILL_SQL_WITH_CTA if cta_heading in headings else _DRILL_SQL
    accounts_param = _sql_array(leaf_codes)
    drill_rows = [
        _drill_row(r) for r in ch_read.rows(
            drill_sql, {"group": group, "fy": key[0], "accounts": accounts_param})
    ]
    journal_rows = [
        _journal_row(r) for r in ch_read.rows(
            _DRILL_JOURNALS_SQL,
            {"group": group, "fy": key[0], "fp": key[1], "accounts": accounts_param})
    ]
    return drill_rows, journal_rows


def _all_drills(payload, reads, drill_rows, journal_rows):
    """``{heading: drill_model.drill(...)}`` for every statement heading.
    ``drill_model.drill`` keeps only its own heading's leaves (and the CTA
    rows only for the CTA heading), so each heading is handed the shared
    rows — what ``get_drill`` would read for it alone. Raises
    ``ValueError`` on a drill/statement mismatch or a NULL amount."""
    key, accounts, declared = reads["key"], reads["accounts"], reads["declared"]
    stmt = payload["statement"]
    allowed = allowed_entity_codes()
    drills = {}
    for heading in _statement_headings(stmt):
        keys = _drill_keys(reads["period_rows"], key, accounts[heading].get("statement_section"))
        drills[heading] = drill_model.drill(
            drill_rows, journal_rows, accounts, heading, keys, declared, allowed,
            _line_for_heading(stmt, heading))
    return drills


@frappe.whitelist(methods=["GET"])
def export_statement(fiscal_year, fiscal_period, consolidation_group=None):
    """The Numbers statement as a downloaded .xlsx (konsol#305 story 8.5,
    decision #305-W5-3): ``get_statement``'s payload and every heading's
    drill, written by ``statement_export_model.workbook``. Same roles and
    entity scope as ``get_statement``/``get_drill``. A non-ok statement
    (``choose_group``, ``no_chart``, ``not_built``, ``error``,
    ``setup_gap``) or a failed drill is refused with the server's own
    sentence — never an empty file. ClickHouse is read four times
    (``get_statement``'s two, one drill read, one journals read), whatever
    the number of headings."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    payload, reads = _statement(fiscal_year, fiscal_period, consolidation_group)
    if payload["state"] != "ok":
        frappe.throw(payload["message"])
    try:
        drill_rows, journal_rows = _drill_reads(payload, reads)
    except Exception as e:  # noqa: BLE001 — a warehouse failure refuses, never a partial file
        frappe.throw(_error_message(e))
    try:
        drills = _all_drills(payload, reads, drill_rows, journal_rows)
    except ValueError as e:
        frappe.throw(str(e))
    content = statement_export_model.workbook(payload, drills)
    frappe.response["type"] = "binary"
    frappe.response["filename"] = _filename(payload["period"], payload["consolidation_group"])
    frappe.response["filecontent"] = content
