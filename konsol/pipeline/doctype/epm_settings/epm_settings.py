import frappe
from frappe.model.document import Document

# Fallback when the group consolidation currency hasn't been set yet — matches
# the historical per-Consolidation-Group default.
DEFAULT_CONSOLIDATION_CURRENCY = "USD"


class EPMSettings(Document):
    pass


def get_consolidation_currency():
    """The single group consolidation/reporting currency (#93, Phase 1).

    One source of truth for "what currency does the group report in", replacing
    the free-text reporting_currency repeated (and unenforced) on every
    Consolidation Group row. Returns the EPM Settings value, or the default if
    unset. Consumers (dbt var generation, consolidation models) should adopt
    this instead of reading per-row reporting currency.
    """
    value = frappe.db.get_single_value("EPM Settings", "consolidation_currency")
    return value or DEFAULT_CONSOLIDATION_CURRENCY
