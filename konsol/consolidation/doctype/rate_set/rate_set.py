"""Rate Set: the set a governed rate belongs to (konsol#348, S4-Q1 option c,
chosen under the standing rule on 15 Nov 2026; options (a) the Scenario as the
key and (b) blank-means-actuals were rejected: (b) gives a blank key a meaning,
(a) duplicates rates per scenario when a budget and its forecast share one set).

Every Group Exchange Rate and Historical Equity Rate names its set, and the
warehouse carries it as ``rate_set`` on both rate tables. Exactly one set is
flagged Is Actuals: the actuals translation, the close gate, Pre-fill from ERP
and the rates screens read that set (``group_rates.actuals_rate_set``) and
refuse by name while none is flagged; nothing defaults to a set. A budget or
forecast names its own set on its Budget Cycle (S4 K5).

Not a fixture: fixtures are reference data re-imported on every migrate
(test_fixtures_reference_only.py), and a site may rename or re-flag its sets.
A fresh site gets ACTUAL from after_install (``ensure_actual_set``); an
existing site from patches/stamp_rate_set_actual.py, which also stamps every
rate it already holds.

The sets write through to ``epm_staging.rate_sets`` (S4-Q7 a, chosen under the
standing rule on 15 Nov 2026; a dbt var konsol regenerates was rejected as a
second copy of the flag that goes stale): every row, after the commit, on save
and on delete, so the warehouse reads which set is flagged Is Actuals the way
it reads every reference table, and ``reconcile_all`` repairs the table.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype_after_commit

DOCTYPE = "Rate Set"
#: The set a fresh site starts with, flagged Is Actuals. A name, not a rule:
#: a site may rename it or flag another; readers follow the flag.
ACTUALS_NAME = "ACTUAL"
ACTUALS_LABEL = "Actual"
ACTUALS_DESCRIPTION = (
    "The actuals rates: the set the consolidation translates actuals at and the close "
    "gate checks. Created by konsol on install (konsol#348); rename it or flag another "
    "set if your policy differs.")


class RateSet(Document):
    #: Identical to the DDL in konsol.clickhouse._REFERENCE_TABLE_DDL and
    #: konsolidat's clickhouse/init-db.sql; keys in column order.
    CH_TABLE = "epm_staging.rate_sets"
    CH_FIELD_MAP = {
        "rate_set": "rate_set",
        "label": "label",
        "is_actuals": "is_actuals",
    }

    def validate(self):
        self._refuse_second_actuals()

    def on_update(self):
        """Every set reaches the warehouse, after the commit, once per
        transaction (konsol#124)."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so
        the full-table re-send put it straight back (#120)."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def _refuse_second_actuals(self):
        """At most one set is flagged Is Actuals. A locking read, so two flags
        saved in parallel cannot both pass (REPEATABLE READ): the second waits
        on the first's row and then sees it."""
        if not self.is_actuals:
            return
        rows = frappe.db.sql(
            "SELECT name FROM `tabRate Set` WHERE is_actuals = 1 AND name != %s FOR UPDATE",
            (self.name,))
        if rows:
            frappe.throw(
                f"{rows[0][0]} is already the Rate Set flagged Is Actuals: the actuals translation "
                "and the close gate read exactly one set. Clear Is Actuals there first, then flag "
                f"{self.name}.", frappe.ValidationError)


def ensure_actual_set():
    """Create Rate Set ACTUAL, flagged Is Actuals, when the site has no Rate Set
    at all; returns its name, or None when sets exist. Runs from after_install
    and the stamping patch. A site that holds sets, flagged or not, is left
    alone: readers refuse by name while none is flagged, and seeding a flagged
    set beside theirs would decide their policy for them."""
    if frappe.db.count(DOCTYPE):
        return None
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "rate_set": ACTUALS_NAME, "label": ACTUALS_LABEL,
        "is_actuals": 1, "description": ACTUALS_DESCRIPTION,
    })
    doc.insert(ignore_permissions=True)
    return ACTUALS_NAME
