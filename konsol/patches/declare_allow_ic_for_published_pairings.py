"""Set allow_ic on every account a Published Intercompany Account names (konsol#293).

konsol#182 decided the split: Intercompany Account is the pairing table and the
source of the intercompany set, and ``Main Account.allow_ic`` is the chart's
precondition for being in it. Until konsol#293 neither half was enforced, so a
site can hold a Published pairing on an account whose chart row says it carries
no intercompany rows. From now on publishing a pairing requires the flag, and
clearing the flag under a live pairing is refused; this patch makes what is
already Published agree, once, before either guard can refuse an existing row.

It only ever SETS the flag, on accounts a live pairing names. It never clears
one: allow_ic means "may carry intercompany rows", which is true of plenty of
accounts that are not paired, and this patch has no basis for withdrawing a
declaration a person made.

Inactive and Draft pairings are ignored — they are not the live set, and
publishing one later runs the guard, which says exactly which accounts need the
flag.

A second run changes nothing. On a site with no Published pairings it is a
no-op and says so, which is the case on the current demo (0 Intercompany
Accounts, measured 27 Sep 2026 — konsol#305 Delivery 2 planning).

patches.txt has no sections, so this runs pre_model_sync: it reloads both
doctypes before any query.
"""
import frappe

from konsol import group_chart_model as M

DOCTYPE = "Main Account"
PAIRING = "Intercompany Account"


def execute():
    frappe.reload_doc("consolidation", "doctype", "intercompany_account")
    frappe.reload_doc("epm", "doctype", "main_account")

    from konsol.consolidation.doctype.intercompany_account.intercompany_account import pair_of

    paired = set()
    for r in frappe.get_all(PAIRING, filters={"status": M.PUBLISHED},
                            fields=["main_account", "counterpart_account"], limit_page_length=0):
        paired.update(pair_of(r.get("main_account"), r.get("counterpart_account")))
    paired.discard("")
    if not paired:
        print("declare_allow_ic_for_published_pairings: no Published Intercompany Accounts, nothing to do")
        return

    # Only the accounts that actually need it, so the count printed is the
    # number of disagreements this patch found rather than the number paired.
    need = frappe.get_all(DOCTYPE, filters={"name": ["in", sorted(paired)], "allow_ic": 0},
                          pluck="name", limit_page_length=0)
    for name in need:
        # db_set, not doc.save(): validate() would re-run every chart rule on a
        # row this patch did not author, and a row that fails an unrelated rule
        # added since it was published must not block the migrate.
        frappe.db.set_value(DOCTYPE, name, "allow_ic", 1, update_modified=False)

    missing = sorted(paired - set(frappe.get_all(
        DOCTYPE, filters={"name": ["in", sorted(paired)]}, pluck="name", limit_page_length=0)))
    print("declare_allow_ic_for_published_pairings: %d account(s) paired, %d given allow_ic%s"
          % (len(paired), len(need),
             (", %d not in the chart at all: %s" % (len(missing), ", ".join(missing))) if missing else ""))
