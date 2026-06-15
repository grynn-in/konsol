"""D365 F&O budget write-back — push approved budget to BudgetRegisterEntries.

ClickHouse (via Frappe) is the source of truth for budgets; D365 is a downstream
*sync target* so its native budget control (PO / expense validation) has the
approved numbers. This is a one-way push.

NOT yet wired to the Budget Input approval workflow — call ``push_budget_input``
explicitly (e.g. ``bench execute``). Wiring the workflow transition is a
follow-up.

Every pushed line is tagged ``BudgetModelId = 'EPM-<budget-input-name>'`` so
EPM-originated entries are identifiable. Do NOT Airbyte-sync BudgetRegisterEntries
back into ``epm_raw`` — filter on this tag if you must (round-trip prevention).

GO-LIVE FOLLOW-UPS (required before wiring to the approval workflow; none are
verifiable without a D365 tenant):
- **Idempotency is NOT guaranteed yet.** A plain OData POST *appends* —
  ``BudgetModelId`` is a grouping tag, not an upsert key — so a second push
  duplicates the budget. ``push_budget_input`` therefore guards against an
  accidental re-push (skips if already ``Pushed`` unless ``force=True``). True
  replace semantics need a delete-by-``BudgetModelId`` step or a ``$batch``
  changeset.
- **Per-line POST is not atomic.** If line K fails, lines 0..K-1 are already
  committed in D365. A ``$batch`` changeset would make the push all-or-nothing.
- **AccountingDate assumes fiscal period == calendar month** (period N ->
  ``YYYY-NN-01``) and fiscal year == calendar year. Wrong for non-January or
  4-4-5 fiscal calendars — resolve the real period start from the D365 fiscal
  calendar before go-live.

``frappe`` is imported lazily inside the functions that need a site so the pure
mapping/auth helpers stay unit-testable without a running Frappe site.
"""
import requests

TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

# Fields required for a write-back to be attempted.
_REQUIRED = ("resource_url", "tenant_id", "client_id", "client_secret")


def get_config():
    """Read D365 write-back connection settings from EPM Settings."""
    import frappe

    s = frappe.get_single("EPM Settings")
    return {
        "enabled": bool(getattr(s, "enable_d365_budget_writeback", 0)),
        "resource_url": (getattr(s, "d365_resource_url", "") or "").rstrip("/"),
        "tenant_id": getattr(s, "d365_tenant_id", "") or "",
        "client_id": getattr(s, "d365_client_id", "") or "",
        "client_secret": s.get_password("d365_client_secret", raise_exception=False) or "",
    }


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


def _flt(value):
    """Coerce to float, treating None / blank / non-numeric as 0.0."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def budget_model_id(budget_input_name):
    """Deterministic D365 BudgetModelId for a Budget Input (idempotency key)."""
    return "EPM-" + str(budget_input_name)


def _period_first_day(year, period):
    """ISO date for the first day of a fiscal period (1-12).

    Assumes fiscal period == calendar month and fiscal year == calendar year.
    See the module docstring's go-live follow-ups: resolve the real period start
    from the D365 fiscal calendar for non-calendar fiscal years before go-live.
    """
    month = max(1, min(12, int(period or 1)))
    return "{0:04d}-{1:02d}-01".format(int(year), month)


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


def build_entries(doc):
    """Map a Budget Input doc to a list of BudgetRegisterEntries line payloads.

    One line per period row. Lines with a zero amount are skipped (no-op in
    D365). The exact OData field names below follow the documented
    BudgetRegisterEntries entity; confirm against the target tenant's data
    entity before go-live.
    """
    model_id = budget_model_id(doc.name)
    dims = _dimension_values(doc)
    entries = []
    for row in doc.periods:
        amount = _flt(row.amount)
        if not amount:
            continue
        entries.append({
            "BudgetModelId": model_id,
            "LegalEntityId": doc.data_area_id,
            "AccountingDate": _period_first_day(doc.fiscal_year, row.fiscal_period),
            "MainAccountId": doc.main_account,
            "AccountingCurrencyAmount": amount,
            "BudgetType": "Original",
            "LedgerDimensionValues": dict(dims),
        })
    return entries


def post_entries(cfg, token, entries):
    """POST each BudgetRegisterEntries line to D365 OData. Returns responses."""
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


def error_message(exc):
    """Generic, non-sensitive failure message safe to store on Budget Input."""
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


def push_budget_input(name, force=False):
    """Push one Budget Input's budget to D365 (manual entry point).

    Not auto-triggered by the approval workflow. Records status/error on the
    Budget Input when those fields exist.

    Re-push guard: a plain OData POST appends (no upsert-by-BudgetModelId), so a
    second push would double the budget. If this Budget Input is already
    ``Pushed`` we skip unless ``force=True`` is passed explicitly. See the module
    docstring for the replace-semantics follow-up needed before go-live.
    """
    import frappe

    cfg = get_config()
    require_enabled(cfg)
    doc = frappe.get_doc("Budget Input", name)

    if not force and doc.meta.has_field("d365_writeback_status") \
            and doc.get("d365_writeback_status") == "Pushed":
        return {
            "status": "Skipped",
            "reason": "already pushed; pass force=True to re-push (will append in D365)",
            "budget_model_id": budget_model_id(name),
        }

    try:
        token = get_token(cfg)
        entries = build_entries(doc)
        post_entries(cfg, token, entries)
        _set_status(doc, "Pushed", "")
        return {
            "status": "Pushed",
            "entries": len(entries),
            "budget_model_id": budget_model_id(name),
        }
    except Exception as exc:
        _set_status(doc, "Failed", error_message(exc))
        # Log the D365 response body server-side (never to the doc field) so an
        # operator can see the real rejection reason; the stored error stays
        # generic/non-sensitive.
        body = ""
        resp = getattr(exc, "response", None)
        if resp is not None:
            body = "\nD365 response: " + (resp.text or "")[:2000]
        frappe.log_error(
            title="D365 budget write-back failed: " + str(name),
            message=frappe.get_traceback() + body,
        )
        raise
