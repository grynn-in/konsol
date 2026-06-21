"""Budget Line — wide child row of a Budget Sheet.

One row per (main_account, in_budget dimensions) carrying 12 monthly amounts
in ``period_01``..``period_12`` columns. The wide layout mirrors how budgets
are entered in Excel; the sheet explodes it to tall rows on lock for ClickHouse
and D365 (see ``budget_sheet.BudgetSheet._sync_to_clickhouse``).
"""
from frappe.model.document import Document

from konsol.epm.budget_periods import PERIOD_FIELDS  # noqa: F401  (re-export)


class BudgetLine(Document):
    pass
