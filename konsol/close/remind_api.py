"""The Remind endpoint for the close app (konsol#305 Y54; story 1.5;
#305-1.5-1; #305-Q2-1; C-R1..C-R5).

``remind(fiscal_year, fiscal_period, entity, topic)`` (POST): the Close Lead
or Group Accountant reminds the people named on one entity about one topic
(``tb``: the trial balance is still missing; ``ic``: an intercompany
difference needs attention).

- The rules are ``remind_model``'s: the recipients are the enabled users with
  a User Permission directly on the entity (#305-Q2-1), and the refusals come
  in its order (unknown topic, period not Open, entity not visible, trial
  balance already in, nobody named). There is no throttle (#305-1.5-1).
- Topic ic, once those pass (konsol#305 R53b, #305-R52-2-1): the period's
  intercompany pairs are read through ``ic_api.checked_rows``. Intercompany
  not checked (not configured, not applicable, a warehouse failure) refuses
  with that state's sentence; an entity with no pair over tolerance is
  refused by ``remind_model.ic_refusal``, the rule ``get_ic``'s per-side
  ``can_remind_a``/``_b`` uses.
- Every refusal comes before any write.
- Then, in the request's transaction: one Notification Log per recipient,
  type ``Alert`` (an in-app alert; any other type emails, M6), and after them
  one ``reminder_sent`` event through ``close_event.record``. That event, not
  the Notification Log (purged after 180 days), is what every reminder count
  reads (Y53). No commit and no try/except: a failed insert rolls the whole
  request back.
- The signature names no other key and takes no **kwargs, so a forged
  recipient list, subject or link never arrives.

``close_event`` and ``ic_api`` are imported lazily (the ``ic_api.send_back``
precedent): ``ic_api`` only for topic ic, after the other refusals.
"""
import importlib.util as _importlib_util
import os as _os

import frappe

from konsol import fiscal_calendar
from konsol.close import remind_model
from konsol.entity_permissions import allowed_entity_codes


def _load_period_name():
    """konsol/close/period_name.py loaded by path: the one "FY2025 P07"
    format, reachable even under the host tests' stub ``konsol.close``."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()


def _period(fiscal_year, fiscal_period):
    try:
        return int(fiscal_year), int(fiscal_period)
    except (TypeError, ValueError):
        frappe.throw(f"Fiscal year {fiscal_year!r}, period {fiscal_period!r} is not a period: "
                     "pass the fiscal year and period as whole numbers.")


def _period_row(key):
    for row in fiscal_calendar.fiscal_period_rows():
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            return row
    frappe.throw("%s is not declared: declare it in EPM Fiscal Year." % period_name(*key))


def _recipients(entity):
    """The users named on ``entity`` (remind_model.recipients over the direct
    Entity permissions and those users' enabled flags)."""
    permissions = frappe.get_all(
        "User Permission", filters={"allow": "Entity", "for_value": entity},
        fields=["user", "allow", "for_value"], limit_page_length=0)
    names = sorted({p["user"] for p in permissions})
    users = {}
    if names:
        for u in frappe.get_all("User", filters={"name": ["in", names]},
                                fields=["name", "enabled"], limit_page_length=0):
            users[u["name"]] = {"enabled": u["enabled"]}
    return remind_model.recipients(entity, permissions, users)


def _tb_in(entity, fy, fp):
    """A submitted trial balance, or a submitted TB Exception, for the period."""
    keys = {"data_area_id": entity, "fiscal_year": fy, "fiscal_period": fp, "docstatus": 1}
    return bool(frappe.db.exists("Trial Balance Submission", keys)
                or frappe.db.exists("TB Exception", keys))


@frappe.whitelist(methods=["POST"])
def remind(fiscal_year, fiscal_period, entity, topic):
    """Remind ``entity``'s people about ``topic``. Returns ``{"event",
    "recipients": n}``."""
    # The literal is remind_model.REMIND_ROLES: the A01 contract test reads the
    # roles here as a literal, and test_close_remind_api pins the two equal.
    frappe.only_for(("EPM Admin", "EPM Analyst", "System Manager"))
    key = _period(fiscal_year, fiscal_period)
    row = _period_row(key)
    fy, fp = key
    period_text = period_name(fy, fp)
    allowed = allowed_entity_codes()
    recipients = _recipients(entity)
    tb_in = topic == "tb" and _tb_in(entity, fy, fp)
    refused = remind_model.refusal(entity, topic, row.get("status"), allowed, recipients,
                                   tb_in, period_text)
    if refused:
        frappe.throw(refused)
    if topic == "ic":
        from konsol.close import ic_api  # lazy: only topic ic reads the warehouse

        rows, not_checked = ic_api.checked_rows(fy, fp)
        if not_checked:
            frappe.throw(not_checked + " Nothing was reminded.")
        refused = remind_model.ic_refusal(entity, rows, period_text)
        if refused:
            frappe.throw(refused)

    sender = frappe.session.user
    subject = remind_model.subject(frappe.utils.get_fullname(sender), entity, topic, period_text)
    link = remind_model.link(fy, fp, topic)
    for user in recipients:
        frappe.get_doc({
            "doctype": "Notification Log",
            "subject": subject,
            "for_user": user,
            "from_user": sender,
            "type": "Alert",
            "document_type": "Entity",
            "document_name": entity,
            "link": link,
        }).insert(ignore_permissions=True)

    from konsol.close import close_event  # lazy: the ic_api.send_back precedent

    name = close_event.record("reminder_sent", fy, fp, entity=entity,
                              detail={"topic": topic, "recipients": recipients,
                                      "subject": subject})
    return {"event": name, "recipients": len(recipients)}
