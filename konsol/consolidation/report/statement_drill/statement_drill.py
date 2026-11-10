"""Statement Drill — the Close app's Numbers drill as a Desk report.

One statement heading of the consolidated statement, for one root
Consolidation Group and period, broken into layers at entity grain
(each entity, eliminations, CTA, top-side journals, deal rows), each with
its accounts and its source document. The breakdown is ``statement_api``'s
own (``get_drill``: same roles, same reads, same signs, its total already
checked against the statement line); this report only lays it out
(``statement_drill_report_model``). Opened from a heading of the
Consolidated Statement report with the same filters.
"""
import frappe

from konsol.close import statement_api, statement_drill_report_model
from konsol.consolidation.report.consolidated_statement.consolidated_statement import _group_code

_REQUIRED = (("fiscal_year", "a fiscal year"), ("fiscal_period", "a fiscal period"),
             ("heading", "a statement heading"))


def execute(filters=None):
    filters = frappe._dict(filters or {})
    entity_basis = filters.get("basis") == "Entity"
    # Basis: Entity (konsol#495) names an entity, never a group.
    required = _REQUIRED + ((("entity", "an entity"),) if entity_basis
                            else (("consolidation_group", "a consolidation group"),))
    for fieldname, what in required:
        if not filters.get(fieldname):
            frappe.throw(f"Choose {what}.")

    if entity_basis:
        payload = statement_api.get_entity_drill(filters.fiscal_year, filters.fiscal_period,
                                                 filters.entity, filters.heading)
        currency = frappe.db.get_value("Entity", filters.entity, "functional_currency")
    else:
        group = _group_code(filters.consolidation_group)
        payload = statement_api.get_drill(filters.fiscal_year, filters.fiscal_period, group,
                                          filters.heading)
        currency = frappe.db.get_value(
            "Consolidation Group", {"consolidation_group": group, "data_area_id": ["is", "not set"]},
            "reporting_currency")
    submissions = _submissions(payload)
    try:
        data = statement_drill_report_model.rows(payload, currency, submissions)
        message = statement_drill_report_model.message(payload, submissions)
    except ValueError as e:
        frappe.throw(str(e))
    return statement_drill_report_model.columns((payload.get("drill") or {}).get("minor_unit")), data, message


def _submissions(payload):
    """The submitted Trial Balance Submissions for the entity rows'
    sources (one read per period the sources name; today always one). The
    model links one only when it is the only submitted one."""
    if payload.get("state") != "ok" or not payload.get("drill"):
        return []
    entities_by_period = {}
    for row in payload["drill"]["rows"]:
        source = row.get("source") or {}
        if source.get("kind") == "tb" and source.get("entity"):
            key = (int(source["fiscal_year"]), int(source["fiscal_period"]))
            entities_by_period.setdefault(key, set()).add(source["entity"])
    out = []
    for (fiscal_year, fiscal_period), entities in sorted(entities_by_period.items()):
        out += frappe.get_all(
            "Trial Balance Submission",
            filters={"docstatus": 1, "data_area_id": ["in", sorted(entities)],
                     "fiscal_year": fiscal_year, "fiscal_period": fiscal_period},
            fields=["name", "data_area_id", "fiscal_year", "fiscal_period"],
            limit_page_length=0)
    return out
