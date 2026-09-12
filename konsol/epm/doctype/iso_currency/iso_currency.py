"""ISO Currency — the ISO 4217 list the warehouse validates FX codes against.

konsolidat#146: this was `seeds/currencies.csv`, the last reference list the dbt
project owned outright. It had no second writer — no collision like the other
ten seeds — but a list the warehouse validates against belongs in the app, where
it is governed and editable, not in the transform repo.

Frappe's own `Currency` doctype is not the right home: it is autonamed
`field:currency_name`, so its primary key IS the display name. On a stock site
every record has ``currency_name`` set to the code ("JPY", not "Yen"), and
giving one its real name renames the record — a currency called "Netherlands
Antillean Guilder" instead of "ANG". It also has no ISO exponent, only Frappe's
``fraction_units``, which ships as 100 for every currency including the
zero-decimal ones.

So konsol keeps its own list, keyed on the code, and leaves Frappe's Currency
records alone.
"""
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class ISOCurrency(Document):
    CH_TABLE = "epm_gold.currencies"
    CH_FIELD_MAP = {
        "currency_code": "currency_code",
        "currency_name": "currency_name",
        "symbol": "symbol",
        "minor_unit": "minor_unit",
    }

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def after_delete(self):
        """after_delete, NOT on_trash.

        ``sync_doctype`` re-sends the whole table from ``frappe.get_all``, and
        on_trash runs BEFORE the row is removed — so a delete would re-publish
        the row it just deleted and leave it live in the warehouse until
        something else resynced this doctype. Same reasoning as
        GovernedReferenceDocument.after_delete and the Connector registry
        (test_connector_registry, test_dimension_mapping).
        """
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
