"""Consolidation Method Declaration: a child row of Consolidation Group.

konsol#117 pair 1 (option A, Deepak Pai, 7 Nov 2026; task D). The method a
group DECLARES for an entity its ownership links reach by more than one path,
where the links alone do not decide it (design comment section 2). The rules
are in konsol.method_declaration_model; the parent's validate throws them.

Warehouse: ``epm_staging.consolidation_method_declarations`` (task C's DDL),
keyed (consolidation_group, data_area_id, effective_date). The group code is
the parent NODE's, not the row's parent name (``CG-{group}-{entity}``), so the
rows are computed here from the child rows and the nodes, and the table is a
computed staging table like Consolidation Group's own closure: ``resync_staging``
is what the parent queues after its commit and what reconcile_all calls.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import resolve_sync_filters, sync_table

DOCTYPE = "Consolidation Method Declaration"


class ConsolidationMethodDeclaration(Document):
    # The DDL's columns in order, updated_at excepted (ClickHouse stamps it).
    # A blank end_date reaches the INSERT as DEFAULT, which the DDL makes
    # 9999-12-31 (method_declaration_model.OPEN_END).
    CH_STAGING_TABLE = "epm_staging.consolidation_method_declarations"
    CH_STAGING_COLUMNS = [
        "consolidation_group", "data_area_id", "consolidation_method",
        "effective_date", "end_date", "reason",
    ]

    @classmethod
    def build_rows(cls, declarations, group_by_node):
        """The staging rows for ``declarations`` (child rows as dicts, with
        ``parent``) given ``group_by_node`` (node name -> group code). Pure.

        A row whose node is not in the map is skipped: child rows die with
        their parent, so it cannot happen outside a half-applied migrate, and
        a row under a blank group would be read by nobody.
        """
        rows = []
        for d in declarations:
            group = group_by_node.get(d.get("parent"))
            if not group:
                continue
            rows.append([
                group,
                d.get("data_area_id"),
                d.get("consolidation_method"),
                d.get("effective_date"),
                d.get("end_date"),
                d.get("reason"),
            ])
        return rows

    @classmethod
    def resync_staging(cls, force=False):
        """Rebuild the table from every declaration on every node.

        Which rows belong is ``resolve_sync_filters`` (the one answer: the
        parent is not submittable and the child declares nothing, so every
        row). Returns what sync_table wrote, or None when it did not write.
        """
        declarations = frappe.get_all(
            DOCTYPE,
            filters=resolve_sync_filters(DOCTYPE),
            fields=["parent", "idx", "data_area_id", "consolidation_method",
                    "effective_date", "end_date", "reason", "modified"],
            limit_page_length=0,
            order_by="parent asc, idx asc",
        )
        nodes = frappe.get_all(
            "Consolidation Group",
            fields=["name", "consolidation_group"],
            limit_page_length=0,
        )
        rows = cls.build_rows(declarations, {n["name"]: n["consolidation_group"] for n in nodes})
        modified = [d.get("modified") for d in declarations if d.get("modified")]
        return sync_table(
            cls.CH_STAGING_TABLE, cls.CH_STAGING_COLUMNS, rows,
            source_max_modified=max(modified).strftime("%Y-%m-%d %H:%M:%S") if modified else None,
            force=force,
        )
