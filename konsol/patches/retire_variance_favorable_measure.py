"""Retire the ``variance_favorable`` Measure and the ``variance_analysis``
Dataset's row for it (konsol#348 S6 KB1; S6-Q4 a, taken under Deepak Pai's
standing rule, 17 Nov 2026; konsolidat#214).

The warehouse column ``gold_variance_analysis.variance_favorable`` (a Bool)
is now ``variance_favourability`` (a String: favourable / unfavourable /
not_applicable). A String cannot be summed, and the Bool never should have
been: ``sum(variance_favorable)`` added flags together. The defaults no longer
ship the Measure or the Dataset row; this patch takes them off a site that
already seeded them (defaults are create-if-missing, konsol#230, so nothing
else would).

1. The ``variance_analysis`` Dataset's child row naming the Measure is
   deleted (the shipped Dataset; a direct row delete, so no Dataset hook
   runs inside the patch).
2. If no other Dataset names the Measure, it is unpublished (status
   Inactive, the transition Measure.unpublish makes) and deleted. Under
   ``frappe.flags.in_patch`` the Measure's own schema apply does not run;
   ``install.regenerate_dbt_vars`` rewrites the dbt vars once at the end of
   the migrate, from the Measures that remain.
3. If a site-made Dataset still names it, the Measure stays and the Datasets
   are logged by name: a site's own semantic model is never edited silently
   (konsol#230). Reading the Measure afterwards errors by name in the
   warehouse (no such column), which is the visible failure, never a 0.

Idempotent: every step is guarded, so a rerun or a fresh site is a no-op.
After this an Excel sheet reading ``variance_favorable`` errors by name (the
Measure is gone); ``variance_favourability`` is a row attribute, not a
Measure.
"""
import frappe

MEASURE = "variance_favorable"
DATASET = "variance_analysis"


def execute():
    if not frappe.db.table_exists("Measure"):
        return
    log = frappe.logger("konsol")

    shipped_rows = frappe.get_all("Dataset Measure", filters={"parent": DATASET, "measure": MEASURE},
                                  fields=["name", "parent"])
    if shipped_rows:
        frappe.db.delete("Dataset Measure", {"parent": DATASET, "measure": MEASURE})
        log.info(f"konsol#348 S6 KB1: removed Measure {MEASURE} from Dataset {DATASET} "
                 f"({len(shipped_rows)} row(s)).")

    if not frappe.db.exists("Measure", MEASURE):
        return

    others = sorted({row.parent for row in frappe.get_all(
        "Dataset Measure", filters={"measure": MEASURE}, fields=["name", "parent"])})
    if others:
        log.warning(f"konsol#348 S6 KB1: Measure {MEASURE} is kept because Dataset(s) "
                    f"{', '.join(others)} still name it; it reads a column the warehouse no longer has "
                    f"(gold_variance_analysis.variance_favourability is a text, not a measure). "
                    "Remove it from those Datasets, then delete the Measure.")
        return

    doc = frappe.get_doc("Measure", MEASURE)
    doc.status = "Inactive"
    doc.save()
    frappe.delete_doc("Measure", MEASURE, force=True, ignore_missing=True)
    log.info(f"konsol#348 S6 KB1: unpublished and deleted Measure {MEASURE} "
             "(gold_variance_analysis.variance_favourability replaces it as a three-state text).")
