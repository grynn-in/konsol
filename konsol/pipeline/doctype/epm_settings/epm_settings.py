import frappe
from frappe.model.document import Document

# Fallback when no group consolidation currency has been set — matches the
# historical per-Consolidation-Group default.
DEFAULT_CONSOLIDATION_CURRENCY = "USD"


class EPMSettings(Document):
    pass


def get_consolidation_currency():
    """The default group consolidation/reporting currency (#93, Phase 1).

    A single configurable home for "what currency does the group report in",
    instead of nothing. Note the reporting currency is genuinely **per top-level
    group** — a deployment can hold several groups in different currencies (the
    demo has Contoso in USD and AMG in CHF) — so this is the *default/fallback*
    for a group that doesn't pin its own ``reporting_currency``, NOT a blanket
    replacement of the per-group value. A future consumer should resolve
    per-group first and fall back here; collapsing every group onto one global
    currency would break a multi-currency consolidation.

    Returns the EPM Settings value, or the default if unset.
    """
    value = frappe.db.get_single_value("EPM Settings", "consolidation_currency")
    return value or DEFAULT_CONSOLIDATION_CURRENCY
