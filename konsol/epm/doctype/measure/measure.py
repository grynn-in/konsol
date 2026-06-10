"""Measure — config doctype for EPM measures.

Saves are pure metadata — no side effects. Run "Apply Schema" to regenerate
dbt vars.
"""
import frappe
from frappe.model.document import Document


class Measure(Document):
    pass
