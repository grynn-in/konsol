"""Main Account Category — read-only Virtual DocType (budget permission target).

Proxies the distinct ``main_account_category`` values from the dbt silver layer
(`epm_silver.silver_main_accounts`) live from ClickHouse. Used as a Frappe User
Permission target so a controller can be granted ownership of an account
category (e.g. all of *Travel*) for budget writes. See spec #51 §B and
``konsol.api._assert_account_access``.
"""
import frappe
from frappe.model.document import Document

from konsol.epm import virtual_permission_target as vpt

_SQL = (
    "SELECT DISTINCT main_account_category FROM epm_silver.silver_main_accounts "
    "WHERE main_account_category != '' ORDER BY main_account_category FORMAT TabSeparated"
)


def _values():
    return vpt.distinct_ch_values(_SQL)


class MainAccountCategory(Document):
    # Read-only instance methods defined ON this class (see vpt docstring).
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
