"""My-work setup gaps: one configuration gap is one item. Pure (konsol#305 A14; stories 1.2, 0.4).

Imports nothing from frappe or konsol; the caller gathers ``facts``:

- ``first_close``: the first close period ``(fiscal_year, fiscal_period)``
  from Close Settings, or None. A key with a 0 part is unset (Close Settings
  Int fields read back as 0) and counts as None.
- ``chart_published``: bool; False when ``group_chart.chart_accounts()`` is
  empty.
- ``frequency_missing``: in-scope entities with no ``reporting_frequency``.
- ``ownership_missing``: in-scope leaves with no ownership period covering the
  period (in scope: Active, not a group).
- ``accountants_without_entities``: enabled Entity Accountants with no Entity
  user permission.

Rules:

- One gap is ONE item that lists everyone affected (#291: never one item per
  entity per month). An empty list gives no item.
- Ids are fixed (``gap:<name>``) and items come in ``GAPS`` order.
- Every item is ``kind "blocking"``, names the owner role that can fix it,
  and its ``action`` opens the Desk page (story 0.4: the Desk only for
  configuration gaps).
- A missing fact raises ValueError: nothing is guessed.
"""

GAPS = ("first_close", "chart", "frequency", "ownership", "accountants")
FACT_KEYS = ("first_close", "chart_published", "frequency_missing", "ownership_missing",
             "accountants_without_entities")


def _declared(first_close):
    if first_close is None:
        return False
    year, period = first_close
    return bool(year) and bool(period)


def _entities(n):
    return f"{n} entity" if n == 1 else f"{n} entities"


def _item(gap, title, detail, owner, desk, entities=(), users=()):
    return {
        "id": f"gap:{gap}",
        "kind": "blocking",
        "title": title,
        "detail": detail,
        "owner": owner,
        "entities": sorted(set(entities)),
        "users": sorted(set(users)),
        "action": {"desk": desk},
    }


def setup_gap_items(facts):
    missing = [k for k in FACT_KEYS if k not in facts]
    if missing:
        raise ValueError(f"setup_gap_items: missing fact(s) {', '.join(missing)}")
    if not isinstance(facts["chart_published"], bool):
        raise ValueError("setup_gap_items: chart_published must be True or False")

    items = []
    if not _declared(facts["first_close"]):
        items.append(_item("first_close", "First close period not declared",
                           "Set the first close period in Close Settings.", "EPM Admin",
                           "/app/close-settings"))
    if not facts["chart_published"]:
        items.append(_item("chart", "Group chart not published",
                           "Publish the Main Accounts of the group chart.", "EPM Admin",
                           "/app/main-account"))
    freq = sorted(set(facts["frequency_missing"] or ()))
    if freq:
        items.append(_item("frequency", f"Reporting frequency missing for {_entities(len(freq))}",
                           ", ".join(freq), "EPM Admin", "/app/entity", entities=freq))
    own = sorted(set(facts["ownership_missing"] or ()))
    if own:
        items.append(_item("ownership", f"Ownership missing for {_entities(len(own))}",
                           ", ".join(own), "EPM Admin", "/app/ownership-period", entities=own))
    users = sorted(set(facts["accountants_without_entities"] or ()))
    if users:
        n = len(users)
        items.append(_item("accountants", f"{n} Entity Accountant{'s' if n > 1 else ''} with no entity",
                           ", ".join(users), "System Manager", "/app/user", users=users))
    return items
