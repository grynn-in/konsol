"""Pure filter builders for konsol Connections queries (frappe-free)."""

CONSOLIDATION_GROUP_CHILD_DOCTYPES = (
    "Ownership Period",
    "Historical Equity Rate",
    "Consolidation Adjustment",
)

PIPELINE_BUILD_TRIGGER_DOCTYPES = [
    "Consolidation Group",
    "Consolidation Adjustment",
    "Ownership Period",
    "Historical Equity Rate",
    "IC Elimination Rule",
    "IC Balance",
    "Allocation Rule",
    "Allocation Driver",
    "Allocation Run",
]


def consolidation_group_child_filters(group_code, data_area_id=None):
    """Filters for child docs keyed by consolidation group business code."""
    filters = {"consolidation_group": group_code}
    if data_area_id:
        filters["data_area_id"] = data_area_id
    return filters


def allocation_driver_filters(driver_type, source_cost_center=None):
    """Filters matching Allocation Drivers to an Allocation Rule."""
    filters = {"driver_type": driver_type}
    if source_cost_center:
        filters["cost_center"] = source_cost_center
    return filters


def pipeline_build_request_trigger(trigger_doctype, trigger_docname):
    """Return (doctype, [docname]) when a PBR has a trigger, else (None, [])."""
    if trigger_doctype and trigger_docname:
        return trigger_doctype, [trigger_docname]
    return None, []