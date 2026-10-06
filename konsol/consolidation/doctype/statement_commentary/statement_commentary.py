"""Statement Commentary — one versioned comment per (consolidation group,
fiscal year, fiscal period, heading) (konsol#305 M43; stories 8.3, 10.1;
#305-W4-5 5b, W4-6 6a; W4-E14).

Group-level, never entity-scoped. The four key fields (``consolidation_group``,
``fiscal_year``, ``fiscal_period``, ``heading``) are ``set_only_once`` — the
autoname key — so a correction is a new text on the same record, never a new
key; a second insert for one key is a DuplicateEntryError. Frappe Versions
(``track_changes``) keep every text; this controller writes nothing else to
hold history.

``validate`` refuses a save outside an Open period, on a leaf, undeclared or
unpublished heading, or for a group that is not a root Consolidation Group —
every reason comes from ``commentary_model.save_problems`` (pure; this
controller only reads the inputs it needs). ``on_update`` writes one
``commentary_saved`` Close Event through the audit trail's one writer, on
insert and on every text change — never on an unrelated field save (a plain
re-save changes nothing). No commit, no try/except: a failing event fails
the save, in the caller's own transaction.

Deletion is refused for every role, the same as Close Event (T01b):
commentary is history, not a draft to discard; write a new text instead.

This file never names the Close Event doctype (test_close_event_writer.py).
"""
import frappe
from frappe.model.document import Document

from konsol import fiscal_calendar
from konsol.close import commentary_model

import importlib.util as _importlib_util
import os as _os


def _load_period_name():
    """konsol/close/period_name.py loaded by path (konsol#305 review-w5): the
    one "FY2025 P07" format, reachable even under the host tests' stub
    ``konsol.close`` package."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", "close", "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()


class StatementCommentary(Document):

    def validate(self):
        period = _period(int(self.fiscal_year), int(self.fiscal_period))
        heading_row = frappe.db.get_value(
            "Main Account", self.heading, ["is_group", "status", "account_name"], as_dict=True)
        group_is_root = bool(frappe.db.exists(
            "Consolidation Group",
            {"consolidation_group": self.consolidation_group, "data_area_id": ["is", "not set"]},
        ))
        problems = commentary_model.save_problems(
            period, self.heading, heading_row, self.consolidation_group, group_is_root)
        if problems:
            frappe.throw("; ".join(problems))

    def on_update(self):
        if self.is_new() or self.has_value_changed("text"):
            # Lazy: close_event imports commentary_model and other close
            # modules; a top-level import here would cycle back.
            from konsol.close import close_event

            close_event.record(
                "commentary_saved", int(self.fiscal_year), int(self.fiscal_period),
                "Statement Commentary", self.name,
                detail=commentary_model.event_detail(
                    self.consolidation_group, self.heading, self.heading_name, self.text),
            )

    def on_trash(self):
        frappe.throw("Commentary is history: write a new text instead.", frappe.PermissionError)


def _period(fiscal_year, fiscal_period):
    for row in fiscal_calendar.fiscal_period_rows():
        if int(row["fiscal_year"]) == fiscal_year and int(row["fiscal_period"]) == fiscal_period:
            return row
    frappe.throw(
        "%s is not a declared period: declare it in EPM Fiscal Year." % period_name(
            fiscal_year, fiscal_period))
