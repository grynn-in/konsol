"""Budget Cost Center — read-only Virtual DocType (budget permission target).

Proxies the distinct cost-center values from the dbt silver layer
(`epm_silver.silver_financial_dimensions` where ``dimension_name = 'CostCenter'``)
live from ClickHouse. Used as a Frappe User Permission target so a department
owner can be granted their cost center(s) for budget writes — the confirmed
finer-than-account ownership case (a shared Travel account booked by multiple
departments). See spec #51 §B and ``konsol.api._assert_dimension_access``.

Named "Budget Cost Center" (not "Cost Center") to avoid colliding with the
ERPNext doctype of that name when both apps are installed.
"""
import frappe
from frappe.model.document import Document

from konsol.epm import virtual_permission_target as vpt

# The D365 financial-dimension name for cost center == dim_cost_center's
# source_column. silver_financial_dimensions is the generic (dimension, value)
# source, so this pattern extends to any permission-controlled dimension.
_SQL = (
    "SELECT DISTINCT dimension_value FROM epm_silver.silver_financial_dimensions "
    "WHERE dimension_name = 'CostCenter' AND dimension_value != '' "
    "ORDER BY dimension_value FORMAT TabSeparated"
)


def _values():
    return vpt.distinct_ch_values(_SQL)


class BudgetCostCenter(Document):
    def load_from_db(self):
        vpt.load_value(self)

    def db_insert(self, *args, **kwargs):
        vpt.readonly_guard(self)

    def db_update(self, *args, **kwargs):
        vpt.readonly_guard(self)

    def delete(self, *args, **kwargs):
        vpt.readonly_guard(self)

    @staticmethod
    def get_list(args=None):
        return vpt.proxy_get_list(_values(), args)

    @staticmethod
    def get_count(args=None):
        return vpt.proxy_get_count(_values(), args)

    @staticmethod
    def get_stats(args=None):
        return vpt.proxy_get_stats(args)
