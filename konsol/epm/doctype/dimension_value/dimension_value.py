"""Dimension Value: read-only virtual doctype, one value of one declared Dimension.

konsol#287 decision 2A (Deepak Pai, 6 Nov 2026). Replaces Budget Cost Center,
which hard-coded one customer's dimension. A document is named
``<dimension>:<value>``; the list holds the distinct non-blank values of every
declared Dimension (Published, ticked in_budget or in_trial_balance), read live
from the warehouse columns named after it. The rules are in
``konsol.dimension_value_model``; this controller holds the frappe and
ClickHouse calls.

A site gates budget writes by a dimension by setting that Dimension's
``permission_doctype`` to ``Dimension Value`` and granting User Permissions on
``<dimension>:<value>`` (``konsol.api._assert_dimension_access``).

A name is found when it parses and its dimension is declared; the value is
not checked against the warehouse, so a grant can be made before the value's
first row arrives (as Budget Cost Center allowed). A name without ``:`` or on
an undeclared dimension raises DoesNotExistError, never a guess.
"""
import frappe
from frappe.model.document import Document

from konsol import dimension_value_model as dvm
from konsol.epm import virtual_permission_target as vpt


def _declared():
    rows = frappe.get_all(
        "Dimension",
        filters={"status": "Published"},
        fields=["name", "status", "in_budget", "in_trial_balance"],
        order_by="name asc",
        limit_page_length=0,
    )
    return dvm.declared_dimensions(rows)


def _table_columns():
    """``{table: set(columns)}`` for the value tables, as ClickHouse reports them."""
    from konsol.clickhouse import execute

    tables = ", ".join(f"'{t}'" for t in dvm.VALUE_TABLES)
    raw = execute(
        "SELECT concat(database, '.', table), name FROM system.columns "
        f"WHERE concat(database, '.', table) IN ({tables}) FORMAT TabSeparated"
    )
    out = {t: set() for t in dvm.VALUE_TABLES}
    for line in (raw or "").splitlines():
        table, _, column = line.partition("\t")
        if table in out:
            out[table].add(column.strip())
    return out


def _rows():
    """Every Dimension Value, sorted. Empty, with an Error Log, when the warehouse fails.

    The picker must not 500 the desk (as vpt.distinct_ch_values); the failure
    is logged, so an empty list is never silent.
    """
    declared = _declared()
    if not declared:
        return []
    try:
        from konsol.clickhouse import execute

        sql = dvm.values_sql(dvm.value_sources(declared, _table_columns()))
        if sql is None:
            return []
        return dvm.parse_rows(execute(sql))
    except Exception:  # noqa: BLE001 - picker must degrade gracefully, logged
        frappe.log_error("Dimension Value fetch failed", frappe.get_traceback())
        return []


def _listed(args):
    args = frappe._dict(args or {})
    try:
        rows = dvm.filter_rows(_rows(), filters=args.get("filters"),
                               or_filters=args.get("or_filters"), txt=args.get("txt"))
    except ValueError as exc:
        frappe.throw(str(exc), frappe.ValidationError)
    return args, rows


class DimensionValue(Document):
    def load_from_db(self):
        resolved = dvm.resolve_name(self.name, _declared())
        if resolved is None:
            # DoesNotExistError takes the message positionally and only a
            # ``doctype`` keyword (frappe/exceptions.py); ``name=`` is a TypeError.
            raise frappe.DoesNotExistError(
                frappe._("{0} {1} not found: a name is <dimension>:<value> on a "
                         "declared Dimension").format(self.doctype, self.name),
                doctype=self.doctype,
            )
        dimension, value = resolved
        # creation and modified exist, empty, so a save reaches the read-only
        # guard rather than failing earlier with an AttributeError.
        super(Document, self).__init__(
            {"name": self.name, "dimension": dimension, "value": value,
             "creation": None, "modified": None, "docstatus": 0})

    def db_insert(self, *args, **kwargs):
        vpt.readonly_guard(self)

    def db_update(self, *args, **kwargs):
        vpt.readonly_guard(self)

    def delete(self, *args, **kwargs):
        vpt.readonly_guard(self)

    @staticmethod
    def get_list(args=None):
        args, rows = _listed(args)
        start = args.get("limit_start") or args.get("start") or 0
        length = args.get("limit_page_length") or args.get("page_length") or 0
        rows = dvm.page(rows, start, length)
        if args.get("as_list"):
            return dvm.as_list(rows, args.get("fields"))
        return [frappe._dict(r) for r in rows]

    @staticmethod
    def get_count(args=None):
        return len(_listed(args)[1])

    @staticmethod
    def get_stats(args=None):
        return vpt.proxy_get_stats(args)
