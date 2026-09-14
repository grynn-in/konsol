"""Declare an EPM Fiscal Year for every fiscal year in use (konsol#189).

Until now a (fiscal_year, fiscal_period) pair was implied: documents carry it
and Period Status rows hold its status. This patch collects every pair in use,
asks fiscal_migration_model.plan() what to declare, and writes it: one
Monthly (12) calendar year per fiscal year not yet declared, with the Period
Status statuses carried onto the matching period rows.

patches.txt has no sections, so this runs pre_model_sync: it reloads the two
new doctypes (child first) before any query. Any conflict stops the patch
before it writes anything. A second run plans nothing.

The collecting, planning and writing live in fiscal_calendar
(plan_fiscal_years, throw_conflicts, apply_fiscal_year_plan), shared with
the declare_years_in_use endpoint.
"""
import importlib.util
import os

import frappe

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    """A konsol module loaded by path, as the fiscal models load siblings."""
    spec = importlib.util.spec_from_file_location(
        "_create_fiscal_years_" + name, os.path.join(_APP, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execute():
    # Child first, then parent, before anything reads either table.
    frappe.reload_doc("epm", "doctype", "epm_fiscal_year_period")
    frappe.reload_doc("epm", "doctype", "epm_fiscal_year")

    calendar = _load("fiscal_calendar")
    result, conflicts, existing = calendar.plan_fiscal_years()
    calendar.throw_conflicts(conflicts, retry="migrate again")
    calendar.apply_fiscal_year_plan(result, existing)

    print("create_fiscal_years: %d fiscal year(s) created, %d period status(es) moved"
          % (len(result["create"]), len(result["moves"])))
