"""Historical Equity Rate.to_currency becomes stored and part of the key (konsol#374).

Decided by Deepak Pai on 7 Nov 2026 (option b): a rate converts into ONE
presentation currency, so a sub-group presenting in EUR under a parent
presenting in USD carries two rates for the same entity and account. Until
now to_currency was a virtual field computed from the rate's group root
(reporting_currency of the Consolidation Group row with no entity, #373).

Every existing rate gets exactly that value written down, and the name the
controller's autoname now gives the (group, entity, account, date,
to_currency) tuple, so nothing about existing data changes except that it is
stored. On the live stack that is 37 rates under one group.

Done at the DB level (set_value + rename_doc), not via the doc lifecycle: the
doctype is submittable, save() rejects a change to a submitted row, and the
on_submit sync must not run per row here. after_migrate's reconcile_all
re-sends the table with the new field map, after ensure_reference_tables has
given epm_staging.historical_equity_rates the column and the key.

A group whose root has no reporting currency stops the migrate, naming the
group and its rates: guessing a currency would invent a rate. A new name that
already belongs to another document, or that two rows would both take, stops
it too, naming the rows: overwriting one tranche with another would lose a
rate. A row that is filled but still carries the old 4-tuple name is renamed
(PR #436 review). Idempotent: a second run finds every row filled and named
by the 5-tuple, and writes nothing.

Runs before the model sync (konsol's patches.txt has no sections), so it
reloads the doctype first to have the column.
"""
import frappe

from konsol.epm.budget_grain import digest_name

_BLANK = ["is", "not set"]


def plan(rows, root_currency):
    """Split ``rows`` (dicts as frappe.get_all returns them, with name,
    consolidation_group, data_area_id, main_account, rate_date, to_currency)
    into the writes to make and the groups that cannot be filled. Pure, so the
    mapping is testable without a site.

    A row needs a write when its To Currency is blank (fill it with the root's
    currency) OR when its name is not the digest of the 5-tuple (rename it):
    PR #436 review, a row filled by hand or by an interrupted run still
    carries the old 4-tuple name, and a blank marker alone never renamed it.
    A row that is filled and correctly named is left alone, which is what
    makes a second run write nothing.

    Returns ([(name, fill, new_name)], {group: [names]}): ``fill`` is the
    currency to write, or None when the row already carries one.
    """
    updates, problems = [], {}
    for row in rows:
        stored = row.get("to_currency")
        group = row.get("consolidation_group")
        currency = stored or root_currency.get(group)
        if not currency:
            problems.setdefault(group, []).append(row.get("name"))
            continue
        # autoname digests str(rate_date); get_all hands back a date object.
        rate_date = row.get("rate_date")
        rate_date = rate_date.isoformat() if hasattr(rate_date, "isoformat") else rate_date
        new_name = digest_name(
            "HER", [group, row.get("data_area_id"), row.get("main_account"), rate_date, currency])
        if stored and new_name == row.get("name"):
            continue
        updates.append((row.get("name"), None if stored else currency, new_name))
    return updates, problems


def duplicate_targets(updates):
    """{new_name: [old names]} for every new name two or more planned updates
    share (PR #436 review): two rows of one tranche that both digest to the
    same name would pass a check against pre-existing documents and make the
    second rename_doc fail mid-loop with Frappe's generic message. Pure."""
    by_target = {}
    for name, _fill, new_name in updates:
        by_target.setdefault(new_name, []).append(name)
    return {t: names for t, names in by_target.items() if len(names) > 1}


def root_currencies():
    """{group code: reporting_currency} for every group root (the row with no entity)."""
    return {
        d.consolidation_group: d.reporting_currency
        for d in frappe.get_all(
            "Consolidation Group",
            filters={"data_area_id": _BLANK},
            fields=["consolidation_group", "reporting_currency"],
            limit_page_length=0,
        )
    }


def execute():
    if not frappe.db.table_exists("Historical Equity Rate"):
        return
    frappe.reload_doc("consolidation", "doctype", "historical_equity_rate")
    rows = frappe.get_all(
        "Historical Equity Rate",
        fields=["name", "consolidation_group", "data_area_id", "main_account",
                "rate_date", "to_currency"],
        limit_page_length=0,
    )
    updates, problems = plan(rows, root_currencies())
    if problems:
        detail = "; ".join(
            f"group '{group}' (no root node with a Reporting Currency): {', '.join(names)}"
            for group, names in sorted(problems.items()))
        frappe.throw(
            "Cannot fill To Currency on these Historical Equity Rates, and will "
            f"not guess one: {detail}. Give each group's root node a Reporting "
            "Currency, or delete the rates, then migrate again.",
            frappe.ValidationError,
        )
    duplicates = duplicate_targets(updates)
    if duplicates:
        detail = "; ".join(
            f"{', '.join(names)} would all be renamed to {new_name}"
            for new_name, names in sorted(duplicates.items()))
        frappe.throw(
            f"Historical Equity Rates collide on their new name: {detail}. They are "
            "the same tranche and currency recorded more than once; cancel or delete "
            "all but one of each, then migrate again.",
            frappe.ValidationError,
        )
    for name, _fill, new_name in updates:
        if new_name != name and frappe.db.exists("Historical Equity Rate", new_name):
            frappe.throw(
                f"Historical Equity Rate {name} would be renamed to {new_name}, which "
                "already exists: two rates for one tranche and currency. Cancel or "
                "delete one of them, then migrate again.",
                frappe.ValidationError,
            )
    for name, fill, new_name in updates:
        if fill:
            frappe.db.set_value(
                "Historical Equity Rate", name, "to_currency", fill, update_modified=False)
        if new_name != name:
            frappe.rename_doc(
                "Historical Equity Rate", name, new_name, force=True, show_alert=False)
