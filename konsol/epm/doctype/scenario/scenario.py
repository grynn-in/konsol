"""Scenario — defines budget, forecast, and other scenarios."""
import frappe
from frappe import _
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype_after_commit


def locked_scenario_type(scenario):
    """The Scenario's type, read under a share lock (review of #460).

    A Budget Cycle or Budget Annual Input copies the type from this read, so a
    copy taken while ``Scenario.validate`` is changing the type waits for that
    change to commit or roll back, then copies the committed value. MariaDB
    here is REPEATABLE READ; a locking read sees the latest committed row."""
    rows = frappe.db.sql(
        "SELECT scenario_type FROM `tabScenario` WHERE name = %s LOCK IN SHARE MODE",
        (scenario,),
    )
    return rows[0][0] if rows else None


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
        # Locking reads (review of #460; CLAUDE.md: a check-then-write needs
        # one). The row lock makes a concurrent copy (locked_scenario_type)
        # wait; each locking COUNT sees rows committed after this
        # transaction's first read, which a plain count would miss.
        #
        # Accepted risk (re-review of #460): a save of an existing cycle holds
        # its own row (Frappe's check_if_latest, for_update) and then share-locks
        # this Scenario, while this save holds the Scenario row (also taken by
        # Frappe before validate) and then counts the cycles. The two can
        # deadlock; MariaDB aborts one with 1213, and nothing stale is written.
        # It needs an existing cycle, which is exactly when this change is
        # refused anyway. The lock order cannot be swapped: Frappe takes the
        # Scenario row before validate runs.
        frappe.db.sql(
            "SELECT scenario_type FROM `tabScenario` WHERE name = %s FOR UPDATE", (self.name,))
        counts = [
            (frappe.db.sql(
                f"SELECT COUNT(*) FROM `tab{dt}` WHERE scenario_id = %s FOR UPDATE",
                (self.name,))[0][0], dt)
            for dt in self.TYPE_COPIES
        ]
        used = [f"{n} {dt}{'' if n == 1 else 's'}" for n, dt in counts if n]
        if used:
            frappe.throw(
                _("Scenario {0} cannot change its type while it is used by {1}: each "
                  "keeps a copy of the type. Create a new scenario instead.").format(
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
