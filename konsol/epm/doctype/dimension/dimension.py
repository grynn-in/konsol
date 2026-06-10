"""Dimension — config doctype for EPM dimensions.

Saves are pure metadata — no side effects. Run "Apply Schema" to regenerate
dbt vars, ClickHouse columns, and Budget Input fields.
"""
import frappe
from frappe.model.document import Document


class Dimension(Document):
    pass
