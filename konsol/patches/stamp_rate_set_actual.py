"""Every existing Group Exchange Rate and Historical Equity Rate is stamped with
the Rate Set flagged Is Actuals (konsol#348, S4-Q1 option c, 15 Nov 2026).

Before this, every governed rate was an actuals rate; now each names its set.
The rates a site already holds are exactly its actuals, so they are stamped
with the flagged set:

* No Rate Set at all (every site upgrading from before S4): ACTUAL is created
  and flagged (``rate_set.ensure_actual_set``), then used.
* Sets exist and one is flagged: that one is used.
* Sets exist and none is flagged, with rates still blank: the migrate stops
  and names the gap. Guessing a set would decide the site's policy.

Historical Equity Rate is named by the digest of its key tuple, which now ends
in the set, so each stamped tranche is renamed to the digest of its new tuple
(as patches/fill_historical_rate_to_currency.py did for the currency): a
second rate for the same tranche and set must still collide on the name. Done
at the DB level (set_value + rename_doc): the doctype is submittable and
save() rejects a change to a submitted row. after_migrate's reconcile_all
re-sends both tables with the new column (the group rates by the publish's
full swap, the historical rates by the sync's full replace), after
ensure_reference_tables has given each table the column.

Idempotent: a second run finds no blank row and writes nothing. Runs before
the model sync (patches.txt has no sections), so it reloads the three
doctypes first. Counts are printed.
"""
import frappe

from konsol.epm.budget_grain import digest_name

GER, HER, RATE_SET = "Group Exchange Rate", "Historical Equity Rate", "Rate Set"
_BLANK = ["is", "not set"]
_HER_FIELDS = ["name", "consolidation_group", "data_area_id", "main_account", "rate_date",
               "to_currency", "rate_set"]


def plan_her(rows, rate_set):
    """[(name, fill, new_name)] for the Historical Equity Rate rows that need a
    write: ``fill`` is ``rate_set`` for a blank row (None when stamped already),
    ``new_name`` the digest of the 6-tuple. A row stamped and named by it is
    left alone, which is what makes a second run write nothing. Pure."""
    updates = []
    for row in rows:
        stored = row.get("rate_set")
        value = stored or rate_set
        rate_date = row.get("rate_date")
        rate_date = rate_date.isoformat() if hasattr(rate_date, "isoformat") else rate_date
        new_name = digest_name("HER", [row.get("consolidation_group"), row.get("data_area_id"),
                                       row.get("main_account"), rate_date, row.get("to_currency"), value])
        if stored and new_name == row.get("name"):
            continue
        updates.append((row.get("name"), None if stored else value, new_name))
    return updates


def duplicate_targets(updates):
    """{new_name: [old names]} where two planned updates share a new name:
    the same tranche and set recorded twice. Pure."""
    by_target = {}
    for name, _fill, new_name in updates:
        by_target.setdefault(new_name, []).append(name)
    return {t: names for t, names in by_target.items() if len(names) > 1}


def _flagged_set(blank_count):
    """The one Rate Set flagged Is Actuals, creating ACTUAL on a site with no
    set; refuses by name when sets exist but none is flagged and ``blank_count``
    rates still need a set."""
    from konsol.consolidation.doctype.rate_set.rate_set import ensure_actual_set

    flagged = frappe.get_all(RATE_SET, filters={"is_actuals": 1}, pluck="name", order_by="name asc")
    if not flagged and frappe.db.count(RATE_SET) == 0:
        flagged = [ensure_actual_set()]
    if len(flagged) != 1:
        frappe.throw(
            f"{blank_count} governed rate(s) carry no Rate Set, and "
            + (f"{', '.join(flagged)} are all flagged Is Actuals"
               if flagged else "no Rate Set is flagged Is Actuals")
            + ". Flag exactly one Rate Set as Is Actuals (the set the actuals translation reads), "
            "then migrate again. Nothing was stamped.", frappe.ValidationError)
    return flagged[0]


def execute():
    for name in ("rate_set", "group_exchange_rate", "historical_equity_rate"):
        frappe.reload_doc("consolidation", "doctype", name)
    blank_ger = frappe.db.count(GER, {"rate_set": _BLANK})
    her_rows = frappe.get_all(HER, filters={"rate_set": _BLANK}, fields=_HER_FIELDS, limit_page_length=0)
    if not blank_ger and not her_rows:
        print("konsol#348 rate sets: every Group Exchange Rate and Historical Equity Rate names its set; "
              "nothing to stamp")
        return
    rate_set = _flagged_set(blank_ger + len(her_rows))
    updates = plan_her(her_rows, rate_set)
    duplicates = duplicate_targets(updates)
    if duplicates:
        detail = "; ".join(f"{', '.join(names)} would all be renamed to {new_name}"
                           for new_name, names in sorted(duplicates.items()))
        frappe.throw(
            f"Historical Equity Rates collide on their new name: {detail}. They are the same tranche "
            "recorded more than once; cancel or delete all but one of each, then migrate again.",
            frappe.ValidationError)
    for name, _fill, new_name in updates:
        if new_name != name and frappe.db.exists(HER, new_name):
            frappe.throw(
                f"Historical Equity Rate {name} would be renamed to {new_name}, which already exists: "
                "two rates for one tranche and set. Cancel or delete one of them, then migrate again.",
                frappe.ValidationError)
    if blank_ger:
        frappe.db.sql(
            "UPDATE `tabGroup Exchange Rate` SET rate_set = %s WHERE IFNULL(rate_set, '') = ''",
            (rate_set,))
    for name, fill, new_name in updates:
        if fill:
            frappe.db.set_value(HER, name, "rate_set", fill, update_modified=False)
        if new_name != name:
            frappe.rename_doc(HER, name, new_name, force=True, show_alert=False)
    print(f"konsol#348 rate sets: {blank_ger} Group Exchange Rate row(s) and {len(her_rows)} "
          f"Historical Equity Rate row(s) stamped {rate_set}; {len(updates)} historical rate(s) renamed "
          "to the digest of their new key")
