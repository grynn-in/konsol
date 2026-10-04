"""Statement Commentary API: one POST saves one heading's commentary
(konsol#305 M44; stories 8.3; R1-R6 roles; #305-W4-5 5b; W4-E14).

``save_commentary(fiscal_year, fiscal_period, consolidation_group, heading,
text, modified=None)`` creates or updates the one Statement Commentary
record for the key, through the document's own ``insert()``/``save()`` (no
``ignore_permissions``), so M43's controller rules (the period must be
Open, the heading a Published statement heading, the group a root
Consolidation Group) and its ``commentary_saved`` Close Event apply exactly
as they do on any other path — this module writes neither directly.

``modified`` is the caller's optimistic-lock token (the ``modified`` it last
read). Before anything is written, ``commentary_model.stale_problem`` (M42)
compares it to the record's current token: a mismatch — someone else's later
save, a token sent for a record that no longer exists, or no token for one
that does — is refused and nothing is saved. ``text`` is stripped; a blank
save clears the commentary and is recorded as such (W4-E14).

Writers: the Group Accountant and the Close Lead (``EPM Analyst``,
``EPM Admin``); ``System Manager`` for support (recon J). The Viewer reads
commentary through N51, never saves it; the Entity Accountant has no
commentary role.

No ``**kwargs``: the signature names exactly the six parameters a caller may
send (Frappe drops any other request key, ``frappe.get_newargs``), so a
forged field never reaches the document.
"""
import frappe

from konsol.close import commentary_model
from konsol.close.timefmt import zoned_iso

#: Who may save commentary (recon J: Group Accountant and Close Lead write;
#: System Manager for support; the Viewer and the Entity Accountant do not).
ROLES = ("EPM Admin", "EPM Analyst", "System Manager")
DOCTYPE = "Statement Commentary"


def _name(consolidation_group, fiscal_year, fiscal_period, heading):
    """The M43 autoname for this key (``format:SC-{consolidation_group}-
    {fiscal_year}-{fiscal_period}-{heading}``), built from the caller's own
    inputs so the save targets exactly the record its own key would name.

    ``fiscal_year``/``fiscal_period`` must be whole numbers: a non-numeric
    value (a bad request, never a policy gap) is refused with a message
    naming the bad input, never a raw ``ValueError``/``TypeError`` (U/S9:
    that would surface as an unexplained 500)."""
    try:
        year, period = int(fiscal_year), int(fiscal_period)
    except (TypeError, ValueError):
        frappe.throw(
            "fiscal_year and fiscal_period must be whole numbers: got "
            f"fiscal_year={fiscal_year!r}, fiscal_period={fiscal_period!r}."
        )
    return "SC-%s-%d-%d-%s" % (consolidation_group, year, period, heading)


@frappe.whitelist(methods=["POST"])
def save_commentary(fiscal_year, fiscal_period, consolidation_group, heading, text, modified=None):
    frappe.only_for(("EPM Admin", "EPM Analyst", "System Manager"))
    name = _name(consolidation_group, fiscal_year, fiscal_period, heading)
    text = (text or "").strip()

    if frappe.db.exists(DOCTYPE, name):
        doc = frappe.get_doc(DOCTYPE, name)
        problem = commentary_model.stale_problem(
            modified, str(doc.modified), doc.modified_by, doc.modified)
        if problem:
            frappe.throw(problem)
        doc.text = text
        doc.save(ignore_permissions=False)
    else:
        problem = commentary_model.stale_problem(modified, None, None, None)
        if problem:
            frappe.throw(problem)
        doc = frappe.get_doc({
            "doctype": DOCTYPE,
            "consolidation_group": consolidation_group,
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "heading": heading,
            "text": text,
        })
        doc.insert(ignore_permissions=False)

    # The same full-name read ``statement_api._commentary`` does (one
    # ``User`` read) — ``by`` is never the bare email U9 found here.
    full_name = frappe.db.get_value("User", doc.modified_by, "full_name") or doc.modified_by

    return {
        "name": doc.name,
        "heading": doc.heading,
        "text": doc.text,
        "modified": str(doc.modified),
        "by": full_name,
        "at": zoned_iso(doc.modified, frappe.utils.get_system_timezone()),
    }
