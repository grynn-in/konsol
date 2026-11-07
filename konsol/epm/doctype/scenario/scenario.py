"""Scenario — defines budget, forecast, and other scenarios."""
import frappe
from frappe import _
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype_after_commit


class Scenario(Document):
    CH_TABLE = "epm_gold.scenario_definitions"
    CH_FIELD_MAP = {
        "scenario_id": "scenario_id",
        "scenario_name": "scenario_name",
        "scenario_type": "scenario_type",
        "is_active": "is_active",
    }

    #: Doctypes holding a fetched copy of scenario_type through ``scenario_id``.
    #: Budget Sheet copies its cycle's, so the cycles count it.
    TYPE_COPIES = ("Budget Cycle", "Budget Annual Input")

    def validate(self):
        """Refuse a type change while a copy of the type exists (#443).

        Budget Cycle, Budget Sheet and Budget Annual Input carry a read-only
        copy of scenario_type, which the Planning sidebar filters on (Deepak
        Pai, 7 Nov 2026, option B; rejected A, C, D). Frappe re-copies it only
        when that row is saved, and never on a submitted cycle, so a changed
        type would list a forecast cycle under Budget. Change it while nothing
        references the scenario, or create a new scenario.
        """
        if self.is_new() or not self.has_value_changed("scenario_type"):
            return
        counts = [(frappe.db.count(dt, {"scenario_id": self.name}), dt) for dt in self.TYPE_COPIES]
        used = [f"{n} {dt}" for n, dt in counts if n]
        if used:
            frappe.throw(
                _("Scenario {0} cannot change its type while {1} reference it: they "
                  "carry a copy of the type. Create a new scenario instead.").format(
                    self.name, " and ".join(used)),
                frappe.ValidationError,
                title=_("Scenario In Use"),
            )

    def on_update(self):
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so
        the full-table re-send put it straight back (#120)."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
