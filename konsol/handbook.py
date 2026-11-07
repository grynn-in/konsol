"""The Konsol Handbook: the product guide and the site's own pages (konsol#407 phase 3).

Handbook option C (Deepak Pai, 7 Nov 2026): the product guide is the markdown
in ``konsol/handbook_pages/`` and changes with every deploy; a site's own
pages are ``Konsol Guide Page`` records. ``get_handbook`` returns both for
the "Konsol Handbook" Custom HTML Block (konsol.desk_blocks). The rules are
``konsol.handbook_model``'s.

Every page, product or site, is rendered with Frappe's ``md_to_html`` and
sanitised by ``handbook_model.sanitize`` before it leaves the server; the
block writes nothing else as HTML.

Site pages are read with ``frappe.get_list``, so the caller's permissions
apply: an editor (EPM Admin, System Manager) reads every page, anyone else a
published one only (``guide_page_conditions`` and ``has_guide_page_permission``,
wired in hooks.py). The handbook shows published pages only, to editors
too; an editor previews a draft in its form.
"""
import os

import frappe
from frappe.utils import md_to_html

from konsol import handbook_model as M

PAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "handbook_pages")


@frappe.whitelist(methods=["GET"])
def get_handbook(audience=None):
    """The product pages and the caller's readable published site pages,
    for ``audience`` (blank: every page). ``handbook_model.assemble``'s shape."""
    frappe.only_for(M.READER_ROLES)
    site = []
    if frappe.has_permission(M.DOCTYPE, "read"):
        site = frappe.get_list(
            M.DOCTYPE, filters={"published": 1}, fields=list(M.SITE_FIELDS),
            order_by="sort_order asc, title asc", limit_page_length=0)
    return M.assemble(
        M.load_product_pages(PAGES_DIR), site, frappe.get_roles(), audience or None,
        render=md_to_html, can_edit=frappe.has_permission(M.DOCTYPE, "write"))


def guide_page_conditions(user=None):
    """permission_query_conditions for Konsol Guide Page."""
    return M.site_page_condition(frappe.get_roles(user))


def has_guide_page_permission(doc, user=None, permission_type=None):
    """has_permission for Konsol Guide Page: exactly True or False."""
    return M.may_read_site_page(frappe.get_roles(user), doc.get("published"))
