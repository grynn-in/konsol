"""My Work as a board (konsol#419; option C, Deepak Pai, 7 Nov 2026).

``get_board`` (GET) is ``mywork_api.get_my_work`` laid out as cards
(``mywork_report_model.board``). It reads nothing else: the roles
(``frappe.only_for``), the persona, the entity scope and the items are
get_my_work's own, so the board can show nothing the list would not. Nothing
is stored; a card moves when the work behind it is done. Read by the Custom
HTML Block "Konsol My Work Board" on the "My Work" workspace.
"""
import frappe

from konsol.close import mywork_api, mywork_report_model


@frappe.whitelist(methods=["GET"])
def get_board():
    """``{columns: [{kind, label, cards}], message, list_view}``."""
    # get_my_work's own roles (literal: the close API contract test reads it).
    frappe.only_for(("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager"))
    payload = mywork_api.get_my_work()
    try:
        return mywork_report_model.board(payload)
    except ValueError as e:
        frappe.throw(str(e))
