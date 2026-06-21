"""Align period_net_amount Measure expression with signed debit−credit net.

Fixtures only seed new sites; existing MariaDB rows may still use
sum(accounting_currency_amount), which regenerate_vars() would write back
into dbt_project.yml on publish.
"""
import frappe


_NEW_EXPRESSION = "sum(debit_amount) - sum(credit_amount)"


def execute():
    if not frappe.db.table_exists("Measure"):
        return
    if not frappe.db.exists("Measure", "period_net_amount"):
        return
    doc = frappe.get_doc("Measure", "period_net_amount")
    if doc.expression == _NEW_EXPRESSION:
        return
    doc.expression = _NEW_EXPRESSION
    doc.save()
    frappe.db.commit()