"""Dimension — config doctype that writes to dbt_project.yml vars."""
import frappe
from frappe.model.document import Document

from konsol.dbt_config import regenerate_vars


class Dimension(Document):
    def on_update(self):
        regenerate_vars()

    def on_trash(self):
        regenerate_vars()
