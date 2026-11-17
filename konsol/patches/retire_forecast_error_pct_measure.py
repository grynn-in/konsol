"""Retire the ``forecast_error_pct`` measure from the accuracy Datasets
(konsol#561 review M2).

Read through ``K.EPM``, ``forecast_error_pct`` averaged the rows'
percentages, unweighted, and a period with none read as 0. That is not
error / |actual|, so the measure is gone from the shipped seed: Excel
computes the % in the sheet as ``forecast_error / ABS(actual_amount)``, and
the Forecast Accuracy report shows it per row.

Datasets and Measures are site-owned and seeded create-if-missing
(konsol#230), so a site seeded earlier keeps the row and the Measure. This
patch removes the ``forecast_error_pct`` row from the two shipped accuracy
Datasets, except where the site made it the Dataset's default measure (the
read would then fail): that Dataset is left alone and named in the migrate
log. It then deletes the Measure only if no Dataset names it any more and it
still holds the shipped expression; a site's own use of the name is kept.

Idempotent: once removed, there is nothing left to remove.
"""
import json

import frappe

MEASURE = "forecast_error_pct"
SHIPPED_EXPRESSION = "avg(forecast_error_pct)"
DATASETS = ("forecast_accuracy", "forecast_accuracy_group")


def execute():
    for name in DATASETS:
        _retire_from_dataset(name)
    _retire_measure()


def _retire_from_dataset(name):
    if not frappe.db.exists("Dataset", name):
        return
    if not frappe.db.count("Dataset Measure",
                           {"parent": name, "parenttype": "Dataset", "measure": MEASURE}):
        return
    row = frappe.db.get_value("Dataset", name, ["measures", "default_measure"], as_dict=True)
    if row.default_measure == MEASURE:
        print(f"konsol#561: Dataset {name} has {MEASURE} as its default measure; left as the "
              "site set it. That measure averages percentages and is not error / |actual|: "
              "set another default measure, then remove it.")
        return
    frappe.db.delete("Dataset Measure",
                     {"parent": name, "parenttype": "Dataset", "measure": MEASURE})
    # The hidden JSON api.py reads, kept in step with the child rows as
    # Dataset._sync_json_fields keeps it on save.
    measures = [m for m in json.loads(row.measures or "[]") if m != MEASURE]
    frappe.db.set_value("Dataset", name, "measures", json.dumps(sorted(measures)),
                        update_modified=False)
    print(f"konsol#561: removed {MEASURE} from Dataset {name}; Excel computes the error % "
          "as forecast_error / ABS(actual_amount)")


def _retire_measure():
    if not frappe.db.exists("Measure", MEASURE):
        return
    if frappe.db.count("Dataset Measure", {"measure": MEASURE}):
        return
    if frappe.db.get_value("Measure", MEASURE, "expression") != SHIPPED_EXPRESSION:
        return
    frappe.db.delete("Measure", {"name": MEASURE})
    print(f"konsol#561: deleted the unused Measure {MEASURE}")
