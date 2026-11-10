"""konsol#509: Close Holiday rows on Close Settings move into one Close Holiday
Calendar per calendar year.

Decided on #509 (option D, chosen under Deepak Pai's standing rule of 5 Nov
2026; rejected A keep the flat list, B infer a year from one date, C a years
table in Settings). Holidays are now declared per year, and a year nobody
declared has no due dates. This patch keeps what a site had declared:

- Every Close Holiday row whose parent is Close Settings goes into the
  calendar of its date's year (``no_holidays`` = 0), which is created when
  missing. A date already in that year's calendar is not added twice; an
  existing calendar is never ticked or cleared.
- A year whose calendar already ticks No Holidays This Year is left alone:
  its rows stay on Close Settings (not visible in Desk), the year is printed
  with the command that reruns this patch once the year is unticked, and the
  rerun places them (review of #512). Nothing stops the migrate.
- A site without the Close Holiday table has nothing to move.
- A year's old rows are deleted only after its calendar saved. Nothing is
  lost: placed + already-present + kept = rows found, and the counts print.
- Idempotent: a second run finds no Close Settings rows and does nothing.

A year is declared only by the holidays it already had. A year with no row
stays undeclared and its steps read "Holidays not declared for <year>": the
site declares it, nothing is assumed.
"""
import frappe

CALENDAR = "Close Holiday Calendar"
HOLIDAY = "Close Holiday"
SETTINGS = "Close Settings"


def execute():
    frappe.reload_doc("consolidation", "doctype", "close_holiday_calendar")
    frappe.reload_doc("consolidation", "doctype", "close_settings")

    if not frappe.db.table_exists(HOLIDAY):
        print("move_holidays_to_calendars: no Close Holiday table, nothing to move")
        return
    rows = frappe.get_all(
        HOLIDAY,
        filters={"parent": SETTINGS, "parenttype": SETTINGS},
        fields=["name", "holiday_date", "description"],
        order_by="holiday_date asc",
        limit_page_length=0,
    )
    by_year = {}
    for row in rows:
        by_year.setdefault(row["holiday_date"].year, []).append(row)

    placed = already = created = 0
    moved, kept = [], []
    for year in sorted(by_year):
        if frappe.db.exists(CALENDAR, year):
            calendar = frappe.get_doc(CALENDAR, year)
            if calendar.no_holidays:
                kept += by_year[year]
                print("move_holidays_to_calendars: %d declares No Holidays This Year, so its %d "
                      "Close Settings holiday row(s) stay; untick it, then run: bench --site <site> "
                      "execute konsol.patches.move_holidays_to_calendars.execute"
                      % (year, len(by_year[year])))
                continue
        else:
            calendar = frappe.get_doc({"doctype": CALENDAR, "year": year, "no_holidays": 0})
            created += 1
        have = {row.holiday_date for row in calendar.get("holidays") or []}
        for row in by_year[year]:
            if row["holiday_date"] in have:
                already += 1
                continue
            calendar.append("holidays", {"holiday_date": row["holiday_date"],
                                         "description": row.get("description")})
            have.add(row["holiday_date"])
            placed += 1
        calendar.flags.ignore_permissions = True
        calendar.save()
        moved += by_year[year]

    if moved:
        frappe.db.delete(HOLIDAY, {"name": ["in", [row["name"] for row in moved]]})

    print("move_holidays_to_calendars: %d rows found, %d placed, %d already present, "
          "%d calendars created, %d rows deleted, %d rows kept"
          % (len(rows), placed, already, created, len(moved), len(kept)))
