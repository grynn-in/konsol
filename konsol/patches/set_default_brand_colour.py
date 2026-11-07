"""konsol#415: give an existing site the declared Brand Colour, #0F766E
(brand_model.DEFAULT), when the field is blank. A new site gets it from
after_install; Close Settings fields carry no JSON default by rule."""
import frappe


def execute():
    frappe.reload_doc("consolidation", "doctype", "close_settings")
    from konsol.brand import set_default_if_blank

    set_default_if_blank()
