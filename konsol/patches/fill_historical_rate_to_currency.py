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
already belongs to another document stops it too: overwriting one tranche
with another would lose a rate. Idempotent: a second run finds every row
filled and writes nothing.

Runs before the model sync (konsol's patches.txt has no sections), so it
reloads the doctype first to have the column.
"""
import frappe

from konsol.epm.budget_grain import digest_name

_BLANK = ["is", "not set"]


def plan(rows, root_currency):
    """Split ``rows`` (each with name, consolidation_group, data_area_id,
    main_account, rate_date, to_currency) into the writes to make and the
    groups that cannot be filled. Pure, so the mapping is testable without a
    site.

    Returns ([(name, to_currency, new_name)], {group: [names]}).
    """
    updates, problems = [], {}
    for row in rows:
        get = row.get if isinstance(row, dict) else lambda k, r=row: getattr(r, k)
        if get("to_currency"):
            continue
        group = get("consolidation_group")
        currency = root_currency.get(group)
        if not currency:
            problems.setdefault(group, []).append(get("name"))
            continue
        # autoname digests str(rate_date); get_all hands back a date object.
        rate_date = get("rate_date")
        rate_date = rate_date.isoformat() if hasattr(rate_date, "isoformat") else rate_date
        new_name = digest_name(
            "HER", [group, get("data_area_id"), get("main_account"), rate_date, currency])
        updates.append((get("name"), currency, new_name))
    return updates, problems


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
    for name, currency, new_name in updates:
        if new_name != name and frappe.db.exists("Historical Equity Rate", new_name):
            frappe.throw(
                f"Historical Equity Rate {name} would be renamed to {new_name}, which "
                "already exists: two rates for one tranche and currency. Cancel or "
                "delete one of them, then migrate again.",
                frappe.ValidationError,
            )
    for name, currency, new_name in updates:
        frappe.db.set_value(
            "Historical Equity Rate", name, "to_currency", currency, update_modified=False)
        if new_name != name:
            frappe.rename_doc(
                "Historical Equity Rate", name, new_name, force=True, show_alert=False)
