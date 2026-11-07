"""Hand the site's Brand Colour to the Desk (konsol#415).

``boot_session`` puts the derived palette on ``frappe.boot.konsol_brand``;
``public/js/konsol_brand.js`` turns it into CSS. Blank means Frappe's own
colours. A stored value that no longer passes (edited outside the form) is
logged and skipped, never half-applied.
"""
import frappe

from konsol import brand_model


def boot_session(bootinfo):
    # Code ahead of its migrate (the field not yet on the doctype) must not
    # break every Desk boot: no field means Frappe's colours.
    if not frappe.get_meta("Close Settings").has_field("brand_colour"):
        bootinfo.konsol_brand = None
        return
    value = frappe.db.get_single_value("Close Settings", "brand_colour")
    if not value:
        bootinfo.konsol_brand = None
        return
    try:
        bootinfo.konsol_brand = brand_model.palette(value)
    except ValueError as e:
        frappe.log_error(title="Konsol Brand Colour skipped", message=str(e))
        bootinfo.konsol_brand = None


def set_default_if_blank():
    """The declared default, #0F766E, set once on a site whose Brand Colour is
    blank: from after_install on a new site, and from the
    set_default_brand_colour patch on an existing one. A site that clears the
    field later keeps Frappe's colours: neither runs again."""
    if not frappe.db.get_single_value("Close Settings", "brand_colour"):
        frappe.db.set_single_value("Close Settings", "brand_colour", brand_model.DEFAULT)
