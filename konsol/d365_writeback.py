"""D365 F&O budget write-back — push approved budget to BudgetRegisterEntries.

ClickHouse (via Frappe) is the source of truth for budgets; D365 is a downstream
*sync target* so its native budget control (PO / expense validation) has the
approved numbers. This is a one-way push.

Wired to the Budget Cycle lock: ``BudgetCycle.on_submit`` enqueues one
``push_budget_sheet`` per sheet (async ``frappe.enqueue``). Gated on
``enable_d365_budget_writeback`` in EPM Settings (off by default).

Every pushed line is tagged ``BudgetModelId = 'EPM-<budget-sheet-name>'`` so
EPM-originated entries are identifiable. Do NOT Airbyte-sync BudgetRegisterEntries
back into ``epm_raw`` — filter on this tag if you must (round-trip prevention).

REPLACE SEMANTICS (implemented; atomicity ASSUMED pending live tenant):
  ``push_replace_batch`` performs an OData ``$batch`` changeset: a pre-flight GET
  (with ``@odata.nextLink`` paging) finds existing entries by BudgetModelId, then
  a single batch request DELETEs them and POSTs the new lines. D365 is *expected*
  to execute a changeset atomically (all-or-nothing); this is not verifiable here
  and is listed under NEEDS-LIVE-TENANT. As a safety net, the batch response is
  scanned for embedded per-operation failures so a partial batch is not recorded
  as Pushed.

  Choice rationale: ``$batch`` changeset over sequential delete+insert because
  it is all-or-nothing at the D365 layer, resolving both the duplicate-on-repush
  risk and the per-line partial-failure risk from the original per-line POST loop.

FISCAL CALENDAR (implemented):
  ``StandardFiscalCalendar(start_month=N)`` maps fiscal period numbers to ISO
  accounting dates for any month-aligned fiscal year (Jan default; Apr, Jul, Oct
  also common). Set ``d365_fiscal_year_start_month`` in EPM Settings.
  ``CustomFiscalCalendar(period_map)`` accepts an explicit period → (year_offset,
  month, day) mapping for 4-4-5 or other non-monthly calendars; pass it directly
  to ``build_entries``.

NEEDS-LIVE-TENANT (cannot verify without a D365 instance):
  - OData field names in ``BudgetRegisterEntries`` (BudgetModelId, LegalEntityId,
    AccountingDate, MainAccountId, AccountingCurrencyAmount, BudgetType).
  - ``LedgerDimensionValues`` attribute names (CostCenter / Department) must match
    the target legal entity's financial-dimension configuration.
  - Entity key fields for DELETE URLs (RecId, dataAreaId) — confirm via
    GET /data/BudgetRegisterEntries/$metadata on the live tenant.
  - ``$batch`` changeset support — verify the endpoint is enabled and that
    ``If-Match: *`` is accepted for DELETE operations.
  - OData ``$filter=BudgetModelId eq '...'`` support on BudgetRegisterEntries.
  - ``fiscal_year_start_month`` in EPM Settings must match the D365 fiscal
    calendar configured for the target legal entity (Ledger → Fiscal calendars).
  - The actual ``POST /data/$batch`` response — D365 returns a multipart body
    with per-operation status codes; parse and surface errors if needed.

``frappe`` is imported lazily inside the functions that need a site so the pure
mapping/auth/client helpers stay unit-testable without a running Frappe site.
"""
import json
import re
import uuid

import requests

TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

# Fields required for a write-back to be attempted.
_REQUIRED = ("resource_url", "tenant_id", "client_id", "client_secret")


# ---------------------------------------------------------------------------
# Fiscal Calendar abstraction
# ---------------------------------------------------------------------------

class FiscalCalendar:
    """Maps (fiscal_year, period) to an ISO accounting date string.

    Subclass for the two supported patterns:
      - ``StandardFiscalCalendar`` — month-aligned, configurable start month.
      - ``CustomFiscalCalendar``   — explicit period→date map (4-4-5, etc.).
    """

    def period_first_day(self, fiscal_year, period):
        raise NotImplementedError


class StandardFiscalCalendar(FiscalCalendar):
    """Month-aligned fiscal calendar with a configurable start month.

    Period N maps to the N-th calendar month of the fiscal year, offset by
    ``start_month``. A January start (``start_month=1``) reproduces the
    original ``_period_first_day`` behaviour exactly.

    Examples::

        StandardFiscalCalendar(1)   FY2024 P1 → 2024-01-01, P12 → 2024-12-01
        StandardFiscalCalendar(4)   FY2024 P1 → 2024-04-01, P9  → 2024-12-01
                                    FY2024 P10 → 2025-01-01, P12 → 2025-03-01
    """

    def __init__(self, start_month=1):
        start_month = int(start_month)
        if not 1 <= start_month <= 12:
            raise ValueError(
                "start_month must be between 1 and 12, got {}".format(start_month)
            )
        self.start_month = start_month

    def period_first_day(self, fiscal_year, period):
        period = max(1, min(12, int(period or 1)))
        offset = self.start_month - 1 + period - 1
        cal_month = offset % 12 + 1
        cal_year = int(fiscal_year) + offset // 12
        return "{:04d}-{:02d}-01".format(cal_year, cal_month)


class CustomFiscalCalendar(FiscalCalendar):
    """Explicit period map for 4-4-5 or other non-monthly fiscal calendars.

    ``period_map``: dict mapping int period number to ``(year_offset, month, day)``.
    ``year_offset`` is added to ``fiscal_year`` (0 = same calendar year, 1 = next).

    Example (4-4-5, UK tax-year style, April start)::

        CustomFiscalCalendar({
            1: (0, 4, 6),   # FY2024 P1 = 6 Apr 2024
            2: (0, 5, 4),   # FY2024 P2 = 4 May 2024
            ...
        })

    NEEDS-LIVE-TENANT: actual D365 fiscal period start dates must be read from
    the tenant (Ledger → Fiscal calendars in D365 F&O); the values above are
    illustrative only.
    """

    def __init__(self, period_map):
        self.period_map = {int(k): v for k, v in period_map.items()}

    def period_first_day(self, fiscal_year, period):
        period = int(period)
        if period not in self.period_map:
            raise ValueError(
                "Period {} not in CustomFiscalCalendar map (keys: {})".format(
                    period, sorted(self.period_map.keys())
                )
            )
        year_offset, month, day = self.period_map[period]
        return "{:04d}-{:02d}-{:02d}".format(int(fiscal_year) + year_offset, month, day)


_DEFAULT_CALENDAR = StandardFiscalCalendar(start_month=1)


def get_fiscal_calendar(cfg=None):
    """Instantiate a FiscalCalendar from a config dict.

    Returns a ``StandardFiscalCalendar`` keyed on ``fiscal_year_start_month``
    (default: 1 = January, i.e. calendar-month periods).

    For 4-4-5 or other non-monthly calendars, construct a ``CustomFiscalCalendar``
    with the explicit period→date map and pass it directly to ``build_entries``.

    NEEDS-LIVE-TENANT: ``fiscal_year_start_month`` (EPM Settings field
    ``d365_fiscal_year_start_month``) must match the D365 fiscal calendar
    configured for the target legal entity.
    """
    start_month = int((cfg or {}).get("fiscal_year_start_month", 1) or 1)
    return StandardFiscalCalendar(start_month=start_month)


# ---------------------------------------------------------------------------
# Config + auth
# ---------------------------------------------------------------------------

def get_config(entity_id=None):
    """Resolve D365 write-back settings from Connector or legacy EPM Settings."""
    from konsol.writeback_config import resolve_d365_writeback_config

    return resolve_d365_writeback_config(entity_id=entity_id)


def require_enabled(cfg):
    """Raise if write-back is disabled or the connection is not fully configured."""
    import frappe

    if not cfg.get("enabled"):
        frappe.throw("D365 budget write-back is disabled in EPM Settings.")
    missing = [k for k in _REQUIRED if not cfg.get(k)]
    if missing:
        frappe.throw(
            "D365 write-back not configured: missing " + ", ".join(missing)
        )


def get_token(cfg):
    """Acquire an Azure AD v2.0 client-credentials token for D365."""
    resp = requests.post(
        TOKEN_URL_TEMPLATE.format(tenant_id=cfg["tenant_id"]),
        data={
            "grant_type": "client_credentials",
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "scope": cfg["resource_url"] + "/.default",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Payload mapping
# ---------------------------------------------------------------------------

def _flt(value):
    """Coerce to float, treating None / blank / non-numeric as 0.0."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def budget_model_id(sheet_name):
    """Deterministic D365 BudgetModelId for a Budget Sheet (idempotency key).

    The model id is the per-sheet grain (entity × layer × cycle), so one push
    replaces exactly that sheet's entries. NOTE: pre-reshape budgets were tagged
    per Budget Input grain (``EPM-BUD-...``); the migration purges those old ids
    before the first sheet push so they don't orphan into a double budget.
    """
    return "EPM-" + str(sheet_name)


def _period_first_day(year, period):
    """ISO date for the first day of a fiscal period (1-12).

    Backward-compatible wrapper around ``StandardFiscalCalendar(start_month=1)``.
    For non-January fiscal years or 4-4-5 calendars, use ``get_fiscal_calendar``
    or construct a ``CustomFiscalCalendar`` and pass it to ``build_entries``.
    """
    return _DEFAULT_CALENDAR.period_first_day(year, period)


def _dimension_values(doc):
    """Canonical EPM dimensions -> D365 financial-dimension display values.

    Only non-empty dimensions are sent. The D365 dimension attribute names
    (CostCenter / Department) must match the target legal entity's dimension
    configuration; validate against the tenant before go-live.
    """
    vals = {}
    if doc.get("dim_cost_center"):
        vals["CostCenter"] = doc.get("dim_cost_center")
    if doc.get("dim_department"):
        vals["Department"] = doc.get("dim_department")
    return vals


def build_entries(sheet, fiscal_year, fiscal_calendar=None):
    """Map a Budget Sheet to a list of BudgetRegisterEntries line payloads.

    The sheet is *wide* — one ``Budget Line`` per (main_account, dimensions)
    carrying 12 monthly columns ``period_01``..``period_12``. This explodes it
    to *tall* D365 entries: one entry per (line, non-zero month). Zero months are
    skipped (no-op in D365). ``fiscal_year`` comes from the sheet's Budget Cycle.
    ``fiscal_calendar``: a ``FiscalCalendar`` for period→date mapping; defaults
    to ``StandardFiscalCalendar(start_month=1)``.

    NEEDS-LIVE-TENANT: OData field names and LedgerDimensionValues attribute
    names must be confirmed against the target legal entity's configuration.
    """
    from konsol.epm.budget_periods import PERIOD_FIELDS

    calendar = fiscal_calendar or _DEFAULT_CALENDAR
    model_id = budget_model_id(sheet.name)
    entries = []
    for line in sheet.lines:
        dims = _dimension_values(line)
        for period, field in enumerate(PERIOD_FIELDS, start=1):
            amount = _flt(line.get(field))
            if not amount:
                continue
            entries.append({
                "BudgetModelId": model_id,
                "LegalEntityId": sheet.data_area_id,
                "AccountingDate": calendar.period_first_day(fiscal_year, period),
                "MainAccountId": line.main_account,
                "AccountingCurrencyAmount": amount,
                "BudgetType": "Original",
                "LedgerDimensionValues": dict(dims),
            })
    return entries


def post_entries(cfg, token, entries):
    """POST each BudgetRegisterEntries line to D365 OData. Returns responses.

    Retained for low-level use and backward compatibility. Production pushes
    should use ``push_replace_batch`` for atomic replace semantics.
    """
    url = cfg["resource_url"] + "/data/BudgetRegisterEntries"
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    results = []
    for entry in entries:
        resp = requests.post(url, json=entry, headers=headers, timeout=60)
        resp.raise_for_status()
        results.append(resp.json() if resp.content else {})
    return results


# ---------------------------------------------------------------------------
# OData $batch replace semantics (atomic delete + insert)
# ---------------------------------------------------------------------------

def fetch_existing_entries(cfg, token, model_id):
    """GET BudgetRegisterEntries filtered by BudgetModelId.

    Returns the OData ``value`` list of entity dicts. Used as a pre-flight
    before the ``$batch`` changeset to identify which records to DELETE.

    Follows ``@odata.nextLink`` to page through ALL matching entries — an
    incomplete delete set would leave orphaned old lines and silently duplicate
    the budget after the new lines are POSTed, the exact failure replace exists
    to prevent.

    NEEDS-LIVE-TENANT:
    - Confirm entity key field names (RecId, dataAreaId) via
      GET /data/BudgetRegisterEntries/$metadata.
    - Verify ``$filter=BudgetModelId eq '...'`` is supported on the entity.
    """
    url = (
        cfg["resource_url"]
        + "/data/BudgetRegisterEntries"
        + "?$filter=BudgetModelId eq '{0}'".format(model_id.replace("'", "''"))
        + "&$select=RecId,dataAreaId"
    )
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
    }
    results = []
    while url:
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        results.extend(payload.get("value", []))
        url = payload.get("@odata.nextLink")  # absolute URL per OData v4, or None
    return results


def _batch_entity_key(entry):
    """Format the OData composite entity key for a BudgetRegisterEntries record.

    NEEDS-LIVE-TENANT: D365 F&O BudgetRegisterEntries entity key field names
    (RecId, dataAreaId) and the composite key syntax must be confirmed against
    the live tenant via the OData $metadata document. RecId is typically an
    Int64; verify it is returned by ``$select=RecId,dataAreaId`` and accepted
    in DELETE URL parentheses.
    """
    try:
        return "dataAreaId='{0}',RecId={1}".format(entry["dataAreaId"], entry["RecId"])
    except KeyError as exc:
        raise KeyError(
            "BudgetRegisterEntries entry missing key field {0}; confirm the "
            "$select key field names against the tenant $metadata".format(exc)
        )


def build_batch_body(batch_boundary, changeset_boundary, delete_keys, entries):
    """Build an OData $batch multipart/mixed request body.

    Design choice — ``$batch`` changeset over sequential delete+insert:
    D365 executes all operations within a changeset atomically. If any DELETE
    or POST fails, D365 rolls back the entire changeset so no partial budget
    state can be committed. This resolves both the duplicate-on-repush risk
    (the DELETE clears the old budget first) and the per-line partial-failure
    risk from the original per-line POST loop.

    NEEDS-LIVE-TENANT:
    - Confirm the ``/data/$batch`` endpoint is enabled.
    - Verify ``If-Match: *`` is accepted for DELETE operations.
    - Some D365 OData versions require ``Content-ID`` headers for referencing
      earlier responses within a changeset; add them if needed.
    """
    lines = []

    lines.append("--" + batch_boundary)
    lines.append("Content-Type: multipart/mixed; boundary=" + changeset_boundary)
    lines.append("")

    for key in delete_keys:
        lines.append("--" + changeset_boundary)
        lines.append("Content-Type: application/http")
        lines.append("Content-Transfer-Encoding: binary")
        lines.append("")
        lines.append("DELETE /data/BudgetRegisterEntries(" + key + ") HTTP/1.1")
        lines.append("If-Match: *")
        lines.append("")

    for entry in entries:
        body = json.dumps(entry)
        lines.append("--" + changeset_boundary)
        lines.append("Content-Type: application/http")
        lines.append("Content-Transfer-Encoding: binary")
        lines.append("")
        lines.append("POST /data/BudgetRegisterEntries HTTP/1.1")
        lines.append("Content-Type: application/json")
        lines.append("")
        lines.append(body)

    lines.append("--" + changeset_boundary + "--")
    lines.append("--" + batch_boundary + "--")

    # Trailing CRLF after the closing boundary is required by RFC 2046; OData
    # $batch parsers (incl. D365) reject a body without it.
    return "\r\n".join(lines) + "\r\n"


# Embedded per-operation status line inside a multipart/$batch response.
_BATCH_STATUS_RE = re.compile(r"HTTP/\d\.\d\s+(\d{3})")


def _raise_on_changeset_errors(resp):
    """Scan a ``$batch`` multipart response for embedded per-operation failures.

    D365 returns HTTP 200/202 for the batch *envelope* even when an operation
    inside the changeset failed, so a partial/failed batch would otherwise be
    recorded as Pushed. Surfaces any embedded non-2xx status. (Changeset
    rollback is assumed; this is the safety net — see module NEEDS-LIVE-TENANT.)
    """
    body = resp.text if isinstance(resp.text, str) else ""
    bad = [s for s in _BATCH_STATUS_RE.findall(body) if not s.startswith("2")]
    if bad:
        raise requests.exceptions.HTTPError(
            "D365 $batch reported operation failures: HTTP " + ", ".join(bad),
            response=resp,
        )


def push_replace_batch(cfg, token, model_id, entries):
    """Atomic replace: delete existing D365 entries then insert new ones in one ``$batch`` changeset.

    Steps:
    1. GET existing ``BudgetRegisterEntries`` by ``BudgetModelId`` (pre-flight;
       outside the changeset so we know which keys to DELETE).
    2. Build a ``$batch`` body: DELETE each existing entry + POST each new entry.
    3. POST the batch to ``/data/$batch`` — D365 executes atomically.

    An empty ``entries`` list with existing records produces a delete-only batch
    (explicit "un-push" / budget retraction).

    NEEDS-LIVE-TENANT: ``$batch`` endpoint availability, entity key format, and
    changeset atomicity behaviour must be validated on the live tenant.
    """
    batch_boundary = "batch_" + uuid.uuid4().hex
    changeset_boundary = "changeset_" + uuid.uuid4().hex

    existing = fetch_existing_entries(cfg, token, model_id)
    delete_keys = [_batch_entity_key(e) for e in existing]

    body = build_batch_body(batch_boundary, changeset_boundary, delete_keys, entries)

    url = cfg["resource_url"] + "/data/$batch"
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "multipart/mixed; boundary=" + batch_boundary,
        "Accept": "multipart/mixed",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
    }
    resp = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=120)
    resp.raise_for_status()
    _raise_on_changeset_errors(resp)  # envelope can be 200 while an op failed
    return resp


def purge_budget_model(cfg, token, model_id):
    """Delete every D365 entry tagged with ``model_id`` (delete-only ``$batch``).

    Used by the Budget Input → Cycle reshape migration to clear pre-reshape
    ``EPM-BUD-*`` ids before the first per-sheet push, so the regrained ids do
    not orphan the old entries into a double budget. Idempotent — a no-op if no
    entries match.
    """
    return push_replace_batch(cfg, token, model_id, [])


# ---------------------------------------------------------------------------
# Error handling + status tracking
# ---------------------------------------------------------------------------

def error_message(exc):
    """Generic, non-sensitive failure message safe to store on Budget Sheet."""
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else "unknown"
        return (
            "D365 rejected the budget (HTTP {0}). Check the posting period is "
            "open and the account / dimensions are valid.".format(status)
        )
    return "Connection error: " + type(exc).__name__


def _set_status(doc, status, error):
    if doc.meta.has_field("d365_writeback_status"):
        doc.db_set("d365_writeback_status", status, update_modified=False)
        doc.db_set("d365_writeback_error", (error or "")[:140], update_modified=False)


# ---------------------------------------------------------------------------
# Lock / unlock integration helpers
# ---------------------------------------------------------------------------

def withdraw_budget_sheet(name):
    """Delete a sheet's D365 entries — used when a Budget Cycle is cancelled.

    Purges every entry tagged with the sheet's ``BudgetModelId`` (delete-only
    ``$batch``) and clears the sheet's write-back status. Defensive: a no-op
    when write-back is disabled for the entity.
    """
    import frappe

    doc = frappe.get_doc("Budget Sheet", name)
    cfg = get_config(entity_id=doc.data_area_id)
    if not cfg.get("enabled"):
        return {"status": "Skipped", "reason": "write-back disabled"}
    token = get_token(cfg)
    purge_budget_model(cfg, token, budget_model_id(name))
    _set_status(doc, "", "")
    return {"status": "Withdrawn", "budget_model_id": budget_model_id(name)}


def enqueue_push_budget_sheet(name):
    """Enqueue an async D365 write-back job for a Budget Sheet.

    Called by ``BudgetCycle.on_submit`` (the lock) once per sheet when
    write-back is enabled. Separated into its own function so it is
    unit-testable without a live Frappe Document instance.
    """
    import frappe

    frappe.enqueue(
        "konsol.d365_writeback.push_budget_sheet",
        queue="long",
        name=name,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def push_budget_sheet(name, force=False):
    """Push one Budget Sheet's budget to D365 using an atomic ``$batch`` changeset.

    Auto-triggered when the parent Budget Cycle is locked (submitted) and
    ``enable_d365_budget_writeback`` is on (via ``frappe.enqueue`` from
    ``BudgetCycle.on_submit``). Can also be called explicitly (e.g.
    ``bench execute konsol.d365_writeback.push_budget_sheet``).

    Replace semantics: ``push_replace_batch`` deletes existing D365 entries
    tagged with this sheet's ``BudgetModelId`` before inserting the new lines,
    making the push idempotent and atomic.

    Re-push guard: if already ``Pushed`` and ``force=False``, the push is skipped.
    Pass ``force=True`` to re-push (deletes then re-inserts in D365).
    """
    import frappe

    doc = frappe.get_doc("Budget Sheet", name)
    cfg = get_config(entity_id=doc.data_area_id)
    require_enabled(cfg)

    if not force and doc.meta.has_field("d365_writeback_status") \
            and doc.get("d365_writeback_status") == "Pushed":
        return {
            "status": "Skipped",
            "reason": "already pushed; pass force=True to re-push (replaces existing D365 entries)",
            "budget_model_id": budget_model_id(name),
        }

    fiscal_year = frappe.db.get_value("Budget Cycle", doc.cycle, "fiscal_year")

    try:
        token = get_token(cfg)
        calendar = get_fiscal_calendar(cfg)
        entries = build_entries(doc, fiscal_year, fiscal_calendar=calendar)
        push_replace_batch(cfg, token, budget_model_id(name), entries)
        _set_status(doc, "Pushed", "")
        return {
            "status": "Pushed",
            "entries": len(entries),
            "budget_model_id": budget_model_id(name),
        }
    except Exception as exc:
        _set_status(doc, "Failed", error_message(exc))
        body = ""
        resp = getattr(exc, "response", None)
        if resp is not None:
            body = "\nD365 response: " + (resp.text or "")[:2000]
        frappe.log_error(
            title="D365 budget write-back failed: " + str(name),
            message=frappe.get_traceback() + body,
        )
        raise
