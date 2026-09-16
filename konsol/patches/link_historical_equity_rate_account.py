"""Historical Equity Rate.main_account becomes a Link to Main Account (konsolidat#92).

It was free text that nothing validated. The stored value does not change —
Main Account is named `field:main_account`, so its name IS the bare account code
the warehouse joins on — but a value already in the table that does not match a
Main Account would now be rejected on the next save, and until then it goes on
silently missing the join, which drops that account to the closing rate instead
of the historical one it was given.

So existing values are mapped first:

* a value that matches a Main Account once trimmed and compared
  case-insensitively is rewritten to the spelling Main Account stores
  (` zz1000 ` -> `ZZ1000`), which is the name the Link must hold;
* a blank value is left alone (the field is required, so there should be none,
  and inventing a code for a row that has none would invent a rate);
* anything else stops the migrate with every offending value and the rates that
  carry it named. Guessing which account someone meant would silently move a
  balance onto a different account's historical rate.

Registered after `rekey_historical_equity_rate_to_group_corp`, which rewrites
the same table's keys, so the mapping sees the rows in their final shape.
Idempotent: a second run finds every value already spelled the way Main Account
spells it and writes nothing.
"""
import frappe


def account_codes():
    """The Main Account names a Link may hold, or none before the table exists."""
    if not frappe.db.table_exists("Main Account"):
        return []
    return frappe.get_all("Main Account", pluck="name")


def plan(rows, codes):
    """Split ``rows`` ([(name, main_account)]) into the rewrites to make and the
    values that don't map. Pure, so the mapping is testable without a site.

    Returns ({name: code}, {value: [names]})."""
    stored = {}
    for code in codes:
        stored.setdefault(str(code).strip().casefold(), code)
    updates, unmapped = {}, {}
    for name, value in rows:
        if value is None or not str(value).strip():
            continue
        code = stored.get(str(value).strip().casefold())
        if code is None:
            unmapped.setdefault(value, []).append(name)
        elif code != value:
            updates[name] = code
    return updates, unmapped


def execute():
    if not frappe.db.table_exists("Historical Equity Rate"):
        return
    rows = frappe.db.sql("SELECT name, main_account FROM `tabHistorical Equity Rate`")
    updates, unmapped = plan(rows, account_codes())
    if unmapped:
        detail = "; ".join(
            f"{value!r} on {', '.join(sorted(names))}"
            for value, names in sorted(unmapped.items(), key=lambda kv: str(kv[0])))
        raise ValueError(
            "Historical Equity Rate.main_account is becoming a Link to Main Account "
            f"(konsolidat#92), and these values are not a Main Account: {detail}. "
            "Add the Main Account if the code is real, or set the rate's Main Account "
            "to the code it means — an unmatched key never matches the balance, so the "
            "account silently falls back to the closing rate. Then run the migrate "
            "again. Nothing was changed."
        )
    for name, code in sorted(updates.items()):
        frappe.db.set_value("Historical Equity Rate", name, "main_account", code,
                            update_modified=False)
        print(f"konsolidat#92: Historical Equity Rate {name}: main_account -> {code}")
    if updates:
        frappe.db.commit()
