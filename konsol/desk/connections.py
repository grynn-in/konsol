"""Custom Connections open-count logic for konsol non-standard links."""
import json

import frappe
from frappe.desk.notifications import _get_linked_document_counts

from konsol.desk.connection_filters import (
    CONSOLIDATION_GROUP_CHILD_DOCTYPES,
    PIPELINE_BUILD_TRIGGER_DOCTYPES,
    allocation_driver_filters,
    consolidation_group_child_filters,
)

_CUSTOM_ONLY_ITEMS = {
    "Measure": frozenset({"Fact Table"}),
    "Consolidation Group": frozenset(CONSOLIDATION_GROUP_CHILD_DOCTYPES),
    "Allocation Rule": frozenset({"Allocation Driver"}),
    "Pipeline Build Request": frozenset(PIPELINE_BUILD_TRIGGER_DOCTYPES),
}

_CUSTOM_PATCHERS = {
    "Measure": "_patch_measure",
    "Consolidation Group": "_patch_consolidation_group",
    "Allocation Rule": "_patch_allocation_rule",
    "Pipeline Build Request": "_patch_pipeline_build_request",
}


def _resolve_items(doctype, items):
    if items is None:
        links = frappe.get_meta(doctype).get_dashboard_data()
        items = [
            item
            for group in links.transactions
            for item in group.get("items", [])
        ]
    elif not isinstance(items, list):
        items = json.loads(items)

    custom = _CUSTOM_ONLY_ITEMS.get(doctype, frozenset())
    return [item for item in items if item not in custom], list(items)


@frappe.whitelist()
def get_open_count(doctype, name, items=None):
    """Drop-in replacement for frappe.desk.notifications.get_open_count."""
    if frappe.flags.in_migrate or frappe.flags.in_install:
        return {"count": []}

    frappe.db.set_execution_timeout(1)

    standard_items, _all_items = _resolve_items(doctype, items)

    try:
        result = _get_linked_document_counts(doctype, name, standard_items)
    except Exception as e:
        if frappe.db.is_statement_timeout(e):
            return {"count": []}
        raise

    patcher_name = _CUSTOM_PATCHERS.get(doctype)
    if patcher_name:
        doc = frappe.get_lazy_doc(doctype, name, check_permission=True)
        globals()[patcher_name](doc, result["count"])

    return result


def _fact_table_names_for_measure(measure_name):
    return frappe.db.sql_list(
        """
        SELECT DISTINCT parent
        FROM `tabFact Table Measure`
        WHERE parenttype = 'Fact Table' AND measure = %s
        ORDER BY parent
        LIMIT 100
        """,
        measure_name,
    )


def _patch_measure(doc, count):
    names = _fact_table_names_for_measure(doc.name)
    _set_internal_link(count, "Fact Table", names)
    _strip_external(count, "Fact Table")


def _patch_consolidation_group(doc, count):
    for child_dt in CONSOLIDATION_GROUP_CHILD_DOCTYPES:
        filters = consolidation_group_child_filters(doc.consolidation_group, doc.data_area_id)
        names = frappe.get_all(child_dt, filters=filters, pluck="name", limit=100, order_by=None)
        _set_internal_link(count, child_dt, names)
        _strip_external(count, child_dt)


def _patch_allocation_rule(doc, count):
    filters = allocation_driver_filters(doc.driver_type, doc.source_cost_center)
    names = frappe.get_all("Allocation Driver", filters=filters, pluck="name", limit=100, order_by=None)
    _set_internal_link(count, "Allocation Driver", names)
    _strip_external(count, "Allocation Driver")


def _patch_pipeline_build_request(doc, count):
    if not doc.trigger_doctype or not doc.trigger_docname:
        return
    _set_internal_link(count, doc.trigger_doctype, [doc.trigger_docname])
    _strip_external(count, doc.trigger_doctype)


def _set_internal_link(count, doctype, names):
    internal = count.setdefault("internal_links_found", [])
    internal[:] = [row for row in internal if row.get("doctype") != doctype]
    internal.append({
        "doctype": doctype,
        "count": len(names),
        "open_count": 0,
        "names": names,
    })


def _strip_external(count, doctype):
    external = count.get("external_links_found", [])
    count["external_links_found"] = [row for row in external if row.get("doctype") != doctype]