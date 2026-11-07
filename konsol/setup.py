"""Konsol Setup: what a site has set up, for the Konsol Setup page (konsol#407 phase 4).

``status()`` (GET, EPM Admin or System Manager; anyone else gets
PermissionError) reads the site and returns ``setup_model.summary``: one step
per area, each done / partial / not done with a sentence, a count and the
Desk route where it is done. Modelled on tapr_compliance P72's
``onboarding.status()``.

It re-derives no rule. Each step asks the reader that already holds it:

- fiscal calendar: ``fiscal_calendar.fiscal_period_rows`` (effective status);
- consolidation group: the group nodes with no entity (where consolidation
  reads the reporting currency, ``currency_references._USES``); whether an
  intercompany difference account is needed from ``ic_api.published_count``
  and ``ic_api.declared_none``; the CTA / result account from
  ``signoff_gate.statement_gap`` (the sign-off gate's own gap);
- entities: Active leaf entities, cut to
  ``entity_permissions.allowed_entity_codes`` before any is named;
- chart: ``group_chart.chart_accounts`` (the Published chart),
  ``group_chart_model.apply_defaults`` (the translation method a leaf
  resolves to) and ``intercompany_account.allow_ic_problem`` (the publish
  check of a pairing);
- dimensions: ``dimension_value_model.declared_dimensions``;
- ISO currencies: ``signoff_gate.currency_gaps`` (konsol#330 / PR #384);
- intercompany: ``ic_api.setup_gap`` and ``ic_api.tolerance_gap``, as My work;
- users: ``mywork_api._accountants_without_entities``, as My work;
- rates: ``group_rates.rate_gate`` for the latest period with a submitted
  trial balance, the gate a close applies;
- warehouse: ``clickhouse.check_health`` and
  ``freshness_api.current_freshness``.

It never writes: every read is ``get_all`` / ``count`` / a SELECT, and a GET
request rolls back. A reader that raises turns its own step "not done" with
the error named (``setup_model.unchecked``), so one failing area never hides
the others and never passes silently.
"""
import frappe

from konsol import (clickhouse, dimension_value_model, entity_permissions, fiscal_calendar, group_chart,
                    group_chart_model, group_rates, setup_model)
from konsol.close import freshness_api, ic_api, signoff_gate
from konsol.close.period_name import period_name

#: Who may read the setup status. A test pins the Page's roles to these.
SETUP_ROLES = ("EPM Admin", "System Manager")

_NO_ENTITY = ["is", "not set"]


def _fiscal_calendar():
    return setup_model.fiscal_calendar_step(
        frappe.db.count("EPM Fiscal Year"), fiscal_calendar.fiscal_period_rows(),
        frappe.db.count("Entity Fiscal Calendar"))


def _group_nodes():
    """One dict per group: the values of its nodes with no entity, the first
    one set winning (a group normally has one such node)."""
    merged = {}
    for row in frappe.get_all(
            "Consolidation Group", filters={"data_area_id": _NO_ENTITY},
            fields=["consolidation_group", "reporting_currency", "ic_difference_account", "nci_account"],
            order_by="consolidation_group asc, name asc", limit_page_length=0):
        out = merged.setdefault(row["consolidation_group"], {"consolidation_group": row["consolidation_group"]})
        for field in ("reporting_currency", "ic_difference_account", "nci_account"):
            out[field] = out.get(field) or row.get(field) or ""
    return list(merged.values())


def _consolidation_group():
    ic_configured = ic_api.published_count() > 0 and not ic_api.declared_none()
    gap = signoff_gate.statement_gap()
    return setup_model.consolidation_group_step(_group_nodes(), ic_configured, gap["message"] if gap else None)


def _entities():
    entities = frappe.get_all("Entity", filters={"is_group": 0, "status": "Active"},
                              fields=["name", "functional_currency"], order_by="name asc",
                              limit_page_length=0)
    grouped = set(frappe.get_all("Consolidation Group", filters={"data_area_id": ["is", "set"]},
                                 pluck="data_area_id", limit_page_length=0))
    return setup_model.entities_step(entities, grouped, entity_permissions.allowed_entity_codes())


def _chart():
    # Imported here: the doctype module imports the governed-reference base.
    from konsol.consolidation.doctype.intercompany_account.intercompany_account import (
        allow_ic_problem, intercompany_accounts)

    chart = group_chart.chart_accounts()
    published_leaves = sum(1 for a in chart.values() if not a["is_group"])
    leaves = frappe.get_all("Main Account", filters={"is_group": 0, "status": ["!=", "Inactive"]},
                            fields=["main_account", "status"] + list(group_chart_model.DECLARED_FIELDS),
                            order_by="main_account asc", limit_page_length=0)
    unresolved = [r["main_account"] for r in leaves
                  if not group_chart_model.apply_defaults(r).get("fx_method")]
    ic_problem = allow_ic_problem(sorted(intercompany_accounts()),
                                  {code: a["allow_ic"] for code, a in chart.items()})
    return setup_model.chart_step(published_leaves, unresolved, ic_problem)


def _dimensions():
    rows = frappe.get_all("Dimension",
                          fields=["name", "status", "in_budget", "in_trial_balance", "permission_doctype"],
                          order_by="name asc", limit_page_length=0)
    budget = [r for r in rows if r.get("status") == "Published" and r.get("in_budget")]
    return setup_model.dimensions_step(
        dimension_value_model.declared_dimensions(rows),
        [r["name"] for r in budget if r.get("permission_doctype")],
        [r["name"] for r in budget if not r.get("permission_doctype")])


def _currencies():
    return setup_model.currencies_step(frappe.db.count("ISO Currency"), signoff_gate.currency_gaps())


def _intercompany():
    return setup_model.intercompany_step(ic_api.published_count(), ic_api.declared_none(),
                                         ic_api.tolerance_gap(), ic_api.setup_gap())


def _enabled_holders(role):
    holders = set(frappe.get_all("Has Role", filters={"parenttype": "User", "role": role},
                                 pluck="parent", limit_page_length=0)) - {"Administrator", "Guest"}
    if not holders:
        return set()
    return set(frappe.get_all("User", filters={"name": ["in", sorted(holders)], "enabled": 1},
                              pluck="name", limit_page_length=0))


def _users():
    # Imported here: mywork_api reads the whole close stack.
    from konsol.close import mywork_api
    return setup_model.users_step(len(_enabled_holders("EPM Admin")),
                                  len(_enabled_holders("Entity Accountant")),
                                  len(mywork_api._accountants_without_entities()))


def _latest_tb_period():
    rows = frappe.get_all("Trial Balance Submission", filters={"docstatus": 1},
                          fields=["fiscal_year", "fiscal_period"],
                          order_by="fiscal_year desc, fiscal_period desc", limit_page_length=1)
    return (int(rows[0]["fiscal_year"]), int(rows[0]["fiscal_period"])) if rows else None


def _trial_balance():
    latest = _latest_tb_period()
    return setup_model.trial_balance_step(
        frappe.db.count("Trial Balance Submission", {"docstatus": 1}),
        period_name(*latest) if latest else None)


def _rates():
    latest = _latest_tb_period()
    if latest is None:
        return setup_model.rates_step(None, None)
    return setup_model.rates_step(period_name(*latest), group_rates.rate_gate(*latest))


def _warehouse():
    health = clickhouse.check_health()
    try:
        fresh, error = freshness_api.current_freshness(), None
    except Exception as e:  # noqa: BLE001 — reported as "could not be read", never as fresh
        fresh, error = None, "%s: %s" % (type(e).__name__, e)
    return setup_model.warehouse_step(health, fresh, error)


#: Step key -> reader, in setup_model.STEPS order.
READERS = (
    ("fiscal_calendar", _fiscal_calendar),
    ("consolidation_group", _consolidation_group),
    ("entities", _entities),
    ("chart_of_accounts", _chart),
    ("dimensions", _dimensions),
    ("currencies", _currencies),
    ("intercompany", _intercompany),
    ("users", _users),
    ("trial_balance", _trial_balance),
    ("rates", _rates),
    ("warehouse", _warehouse),
)


def _read(key, reader):
    try:
        return reader()
    except Exception as e:  # noqa: BLE001 — the step says it could not be checked
        return setup_model.unchecked(key, "%s: %s" % (type(e).__name__, e))


@frappe.whitelist(methods=["GET"])
def status():
    """The setup checklist for the site. Read-only."""
    frappe.only_for(SETUP_ROLES)
    return setup_model.summary([_read(key, reader) for key, reader in READERS])
