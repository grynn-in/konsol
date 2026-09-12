"""Entity Fiscal Calendar — which fiscal calendar an ERP legal entity posts against.

konsolidat#146: this was `seeds/entity_fiscal_calendars.csv`. Like the ISO
currency list it had no second writer, so nothing was being clobbered — but it
decides which calendar every GL line is dated into, and that belongs in the app.

Its own doctype rather than a field on Entity, for the same reason Dimension
Mapping is its own doctype: this is a raw-ERP mapping, not a property of the
consolidation scope. The shipped list covers 69 D365 data areas, of which the
warehouse currently sees seven — putting the other 62 into Entity would fill the
consolidation entity master with entities nobody consolidates, while dropping
them would silently date a Chinese entity into 'Fiscal' instead of 'Fiscal_CN'
the day someone loads its ledger.

silver_gl_entries falls back to 'Fiscal' for an entity with no row, so the
mapping only ever has to carry the exceptions — but it carries the defaults too,
because "no row" and "explicitly Fiscal" are worth being able to tell apart.

The key field is `erp_data_area`, NOT `data_area_id`. Every konsol doctype that
names a konsol Entity uses `data_area_id` as a Link to Entity (F1, and
test_entity_links enforces it). This is not that: 61 of the 68 codes here have
no Entity record and never will unless someone consolidates them, so a Link
would reject exactly the mappings worth keeping. Same category as Dimension
Mapping's `source_value` — a raw ERP string, kept as one. The ClickHouse column
is still `data_area_id`, because that is the join key the warehouse uses.
"""
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class EntityFiscalCalendar(Document):
    CH_TABLE = "epm_gold.entity_fiscal_calendars"
    # The warehouse column keeps the name every join uses; the doctype field
    # does not, because it is not a konsol Entity — see the module note.
    CH_FIELD_MAP = {
        "data_area_id": "erp_data_area",
        "fiscal_calendar_id": "fiscal_calendar_id",
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
