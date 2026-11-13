"""Cost Allocation Statistic: the driver values for one entity, scenario and
fiscal year (konsol#269 K5a).

Submit is the approval. Decision Q4 (13 Nov 2026, Deepak Pai): cancelling is
held to the period close only when the scenario's scenario_type is 'actual';
a budget or forecast statistic can be withdrawn whatever the period status.
"""
import frappe
from frappe.model.document import Document

from konsol import period_status
from konsol.cost_allocation_model import CostAllocationError, parse_statistic_csv
from konsol.clickhouse import sync_doctype_after_commit
from konsol.epm.doctype.cost_allocation_statistic_value.cost_allocation_statistic_value import (
    CostAllocationStatisticValue,
)

MAX_ROWS = 20000
_SHOWN = 10
_PREFIX = "Cost Allocation Statistic: "


def _listing(items):
    items = list(items)
    text = ", ".join(str(i) for i in items[:_SHOWN])
    return text + (f" and {len(items) - _SHOWN} more" if len(items) > _SHOWN else "")


class CostAllocationStatistic(Document):
    CH_TABLE = "epm_staging.cost_allocation_statistics"
    # CH column -> Frappe field. The warehouse names the entity data_area_id
    # and the scenario scenario_id, as every other staging table does.
    CH_FIELD_MAP = {
        "name": "name",
        "driver": "driver",
        "dimension": "dimension",
        "data_area_id": "entity",
        "scenario_id": "scenario",
        "fiscal_year": "fiscal_year",
    }
    #: child doctype -> its controller; the child is its own table in the
    #: warehouse, keyed (parent, idx).
    CHILD_CONTROLLERS = {"Cost Allocation Statistic Value": CostAllocationStatisticValue}

    # -- lifecycle -----------------------------------------------------------

    def validate(self):
        self.dimension = frappe.db.get_value("Cost Allocation Driver", self.driver, "dimension")
        rows = list(self.get("values") or [])
        if not rows:
            frappe.throw(f"{_PREFIX}add at least one value row.")
        if len(rows) > MAX_ROWS:
            frappe.throw(
                f"{_PREFIX}{len(rows):,} rows; at most {MAX_ROWS:,} are allowed. "
                "Split the statistic by entity, scenario or year.")
        for period in sorted({int(r.fiscal_period or 0) for r in rows}):
            period_status.assert_declared(self.fiscal_year, period)
        found = self._row_problems(rows)
        if found:
            frappe.throw("<br>".join(found))
        self.row_count = len(rows)
        self.total = sum(float(r.value or 0) for r in rows)

    def _row_problems(self, rows):
        found = []
        members = set(frappe.get_all(
            "Dimension Value", filters={"dimension": self.dimension}, pluck="value",
            limit_page_length=0))
        unknown = sorted({r.member for r in rows if r.member not in members})
        if unknown:
            found.append(f"{_PREFIX}not members of dimension {self.dimension}: {_listing(unknown)}.")
        seen, duplicate = set(), []
        for r in rows:
            key = (int(r.fiscal_period or 0), r.member)
            if key in seen and key not in duplicate:
                duplicate.append(key)
            seen.add(key)
        if duplicate:
            found.append(
                f"{_PREFIX}duplicate (period, member) rows: "
                f"{_listing(f'P{p} {m}' for p, m in duplicate)}.")
        negative = [f"P{int(r.fiscal_period or 0)} {r.member}" for r in rows if float(r.value or 0) < 0]
        if negative:
            found.append(f"{_PREFIX}values cannot be negative: {_listing(negative)}.")
        return found

    def before_submit(self):
        """No other submitted statistic may hold a value for the same driver,
        entity, scenario, year, period and member. The check reads with FOR
        UPDATE: under REPEATABLE READ a plain read misses a statistic
        submitted after this transaction's first read."""
        mine = {(int(r.fiscal_period), r.member) for r in self.get("values") or []}
        clashes = frappe.db.sql(
            "SELECT s.name AS name, v.fiscal_period AS fiscal_period, v.member AS member "
            "FROM `tabCost Allocation Statistic` s "
            "JOIN `tabCost Allocation Statistic Value` v "
            "ON v.parent = s.name AND v.parenttype = 'Cost Allocation Statistic' "
            "WHERE s.docstatus = 1 AND s.driver = %(driver)s AND s.entity = %(entity)s "
            "AND s.scenario = %(scenario)s AND s.fiscal_year = %(fiscal_year)s "
            "AND s.name != %(name)s FOR UPDATE",
            {"driver": self.driver, "entity": self.entity, "scenario": self.scenario,
             "fiscal_year": self.fiscal_year, "name": self.name or ""},
            as_dict=True)
        overlaps = [r for r in clashes if (int(r.fiscal_period), r.member) in mine]
        if overlaps:
            frappe.throw(
                f"{_PREFIX}already supplied by a submitted statistic: "
                + _listing(f"{r.name} P{int(r.fiscal_period)} {r.member}" for r in overlaps)
                + ". Cancel it first, or remove those rows.")

    def on_submit(self):
        self._sync()

    def before_cancel(self):
        if frappe.db.get_value("Scenario", self.scenario, "scenario_type") != "actual":
            return
        for period in sorted({int(r.fiscal_period) for r in self.get("values") or []}):
            period_status.assert_open(
                self.fiscal_year, period, action=f"cancel statistic {self.name}")

    def on_cancel(self):
        self._sync()

    def after_delete(self):
        """after_delete, not on_trash (#120)."""
        self._sync()

    # -- warehouse -----------------------------------------------------------

    def _sync(self):
        """Header and values, once, after the commit."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        for doctype, controller in self.CHILD_CONTROLLERS.items():
            sync_doctype_after_commit(doctype, controller.CH_TABLE, controller.CH_FIELD_MAP)


@frappe.whitelist(methods=["POST"])
def load_values_from_file(name):
    """Replace every value row from the attached CSV (K5b). A draft only.
    All or nothing: the file is parsed in full before any row changes, and the
    save re-runs validate (periods, members, duplicates); a refusal there
    leaves the stored document as it was, because the request rolls back."""
    doc = frappe.get_doc("Cost Allocation Statistic", name)
    doc.check_permission("write")
    if doc.docstatus != 0 or doc.status != "Draft":
        frappe.throw(f"{_PREFIX}values can be loaded into a draft only.")
    if not doc.values_file:
        frappe.throw(f"{_PREFIX}attach a CSV file in Values File first.")
    content = frappe.get_doc("File", {"file_url": doc.values_file}).get_content()
    try:
        text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
        rows = parse_statistic_csv(text)
    except UnicodeDecodeError:
        frappe.throw(f"{_PREFIX}the file is not UTF-8 text; save it as CSV UTF-8.")
    except CostAllocationError as e:
        frappe.throw(f"{_PREFIX}the file was not loaded.<br>" + "<br>".join(e.errors))
    doc.set("values", [])
    for r in rows:
        doc.append("values", {"fiscal_period": r["fiscal_period"], "member": r["member"],
                              "value": float(r["value"])})
    doc.save()
