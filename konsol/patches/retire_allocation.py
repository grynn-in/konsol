"""Retire the deprecated allocation doctypes (konsol#264).

konsol#264 (Deepak Pai, 18 Sep 2026): the cost-allocation feature is REMOVED
entirely — not deprecated in favour of a successor, gone. All four
``epm_staging.allocation_*`` tables are empty on live. The doctype code
(``Allocation Rule`` + its ``Allocation Tier`` child table, ``Allocation
Driver``, ``Allocation Run`` + its dormant per-doc workflow) and the
``konsol/allocation`` package were deleted in rows K2/K3/K6/K7/K8/K9a/K9b of
that removal. This patch is the cutover step those rows left open: delete_doc
on a DocType removes the meta but deliberately leaves the table behind, so an
existing site still carries the four ``tabAllocation *`` tables (and possibly
the workflow doc, if a site ever ran ``install_workflows`` while the
definition was briefly wired up) until this runs. Row K11 removed the
``Allocation`` line from ``modules.txt``, which stops a fresh install from
creating the ``Module Def`` — but does not delete one an already-migrated
site created before the line was removed, so this patch deletes it too,
after the DocTypes that claimed it are gone.

DEVIATION from ``retire_budget_input``'s precedent: that patch throws when it
finds unmigrated data, because Budget Input had a migration target (Budget
Sheet) and a throw there prevents destroying the only copy of live data.
Allocation has no such target — the feature itself is gone, there is nowhere
for these rows to go — so the equivalent parity gate would instead wedge
`bench migrate` permanently on any site that ever held allocation data, with
no way to satisfy it. Instead, this patch counts the rows in each table
before dropping it and logs the counts (guarded by ``table_exists`` so a
fresh install, which never created the table, no-ops cleanly), leaving an
audit trail without blocking anyone. It does NOT throw.

Row K5 extends this patch for the same reason: fixtures
(``konsol/fixtures/*.json``) are force-reimported on every migrate but never
delete a row removed from the JSON. Dropping the allocation Dataset rows
(``headcount``, ``area_sqm``, ``revenue_by_product``, the allocation drivers,
and ``allocated``, the output dataset over ``gold_allocation_tb`` — a model
konsolidat has already deleted) and the two allocation Build Model rows
(``gold_allocation_results``, ``gold_allocation_audit_trail``) from the
fixture files leaves the ``Dataset`` and ``Build Model`` documents those rows
created behind on every site that already migrated them. This is a DB-level
delete (``frappe.db.delete``), not ``frappe.delete_doc``, matching
``rekey_historical_equity_rate_to_group_corp``'s shape: these are plain
documents (not DocTypes), there is no lifecycle behaviour worth running on
the way out, and a direct delete keeps this idempotent and side-effect-free.
The ``Dataset Measure`` / ``Dataset Dimension`` child rows those Datasets
owned are cleaned up the same way, guarded by ``table_exists`` so a fresh
install (which never created the row) no-ops cleanly.

Idempotent: ``ignore_missing=True`` on every ``delete_doc``,
``DROP TABLE IF EXISTS`` on every table, and ``frappe.db.delete`` (a no-op
when the row is already gone) make a rerun a no-op.
"""
import frappe

_RETIRED_DATASETS = ["headcount", "area_sqm", "revenue_by_product", "allocated"]
_RETIRED_BUILD_MODELS = ["gold_allocation_results", "gold_allocation_audit_trail"]


def execute():
    # Audit trail only (see module docstring for why this does not throw):
    # log any residual row counts before the tables are dropped.
    for table in (
        "Allocation Tier",
        "Allocation Driver",
        "Allocation Run",
        "Allocation Rule",
    ):
        if frappe.db.table_exists(table):
            count = frappe.db.count(table)
            frappe.logger().info(
                "retire_allocation: tab{0} had {1} row(s) at retirement "
                "(konsol#264: allocation is removed with no migration "
                "target, so these rows are being dropped, not moved)".format(
                    table, count
                )
            )

    frappe.delete_doc(
        "Workflow", "Allocation Run Workflow", force=True, ignore_missing=True
    )

    # Child table before parent: Allocation Tier is a child table of
    # Allocation Rule.
    frappe.delete_doc(
        "DocType", "Allocation Tier", force=True, ignore_missing=True
    )
    frappe.delete_doc(
        "DocType", "Allocation Rule", force=True, ignore_missing=True
    )
    frappe.delete_doc(
        "DocType", "Allocation Driver", force=True, ignore_missing=True
    )
    frappe.delete_doc(
        "DocType", "Allocation Run", force=True, ignore_missing=True
    )

    # Module Def last: it cannot be removed while a DocType still claims it,
    # so this must come after all four DocType deletions above (konsol#264
    # row K11 — the Allocation line is also gone from modules.txt, but that
    # alone does not delete an already-migrated site's Module Def record).
    frappe.delete_doc(
        "Module Def", "Allocation", force=True, ignore_missing=True
    )

    # delete_doc leaves the data tables in place; drop them explicitly
    # (child table first, matching the delete_doc order above).
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabAllocation Tier`")
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabAllocation Rule`")
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabAllocation Driver`")
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabAllocation Run`")

    # Row K5: the four allocation Dataset rows and the two allocation Build
    # Model rows were removed from the fixture files, but fixtures are
    # force-reimported on every migrate and never delete — so a site that
    # already migrated them still carries the documents those rows created.
    # DB-level delete (see module docstring): plain documents, no lifecycle
    # behaviour worth running, mirrors rekey_historical_equity_rate_to_group_corp.
    if frappe.db.table_exists("Dataset"):
        # Child rows first (Dataset Measure / Dataset Dimension are child
        # tables of Dataset, keyed on `parent`).
        if frappe.db.table_exists("Dataset Measure"):
            frappe.db.delete(
                "Dataset Measure", {"parent": ["in", _RETIRED_DATASETS]}
            )
        if frappe.db.table_exists("Dataset Dimension"):
            frappe.db.delete(
                "Dataset Dimension", {"parent": ["in", _RETIRED_DATASETS]}
            )
        frappe.db.delete("Dataset", {"name": ["in", _RETIRED_DATASETS]})

    if frappe.db.table_exists("Build Model"):
        frappe.db.delete("Build Model", {"name": ["in", _RETIRED_BUILD_MODELS]})

    # The desk navigation is the last thing pointing at the deleted doctypes.
    # Deleting a DocType does not touch the Workspace rows that link to it, so
    # /app/konsolidat threw "DocType Allocation Run not found" on every load
    # and still rendered an "Allocation Runs" tile: measured on konsolidat.local
    # as tabWorkspace Shortcut idx 5 and tabWorkspace Link idx 32/33/34, with
    # 0 rows in tabDocType for all three names. Same shape as the DROP TABLEs
    # above — delete_doc leaves something behind, so the patch clears it.
    #
    # dashboard.py now rebuilds on its own when a linked doctype disappears,
    # which covers a site migrating from here on. This call is for sites that
    # already ran this patch: patches do not rerun, so their workspace would
    # keep the dead links until some unrelated layout change happened to
    # trigger a rebuild.
    #
    # force=True because setup_workspace() only rebuilds when it decides a
    # refresh is needed; and this runs LAST, after the four DocTypes are gone,
    # because _create_workspace filters its entries through _dt() — rebuilding
    # any earlier would put the allocation links straight back.
    from konsol.dashboard import setup_workspace

    setup_workspace(force=True)
