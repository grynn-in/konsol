"""The joint-operation refusal of a deal (konsol#117 pair 3, review findings D, L).

Pure: no frappe, no ClickHouse. Business Combination and Business Disposal
gather the rows and throw what this returns.

``proportional`` is IFRS 11.20 proportionate consolidation. A business
combination into, or a disposal of an interest in, a joint operation (IFRS
11.21A), and a step from a joint operation to control, are not implemented. A
deal is refused by name when the entity in the group is, on the deal date:

* held ``proportional`` by an Ownership Period, draft or submitted, of any
  owner (a Draft the approval would submit keeps its hand-set method);
* declared ``proportional`` by the latest Consolidation Method Declaration
  (effective on or before the deal date, End Date exclusive, as the
  warehouse's ownership_resolution macro reads it) of the group's own node or
  of any ancestor group's node; or
* a joint operation whose window reaches back to ``window_start``, the first
  day of the fiscal period before the deal's (the warehouse's rule in
  assert_acquisition_accounts_declared: gold_entity_ownership resolves a
  period at its first day).

konsol refuses on a declaration even where the warehouse would not need it
(an entity reached by one path): deliberate, the declaration states the
group's intent. This check sees only the rows it is given;
dbt's assert_acquisition_accounts_declared and assert_disposal_accounts_declared
are the backstop for anything it cannot see.
"""
import datetime

_TAIL = "is not implemented (IFRS 11.21A). Record the change as an Ownership Period."


def _date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def joint_operation_problem(entity, deal_date, deal_phrase, step_phrase, window_start, periods, declarations):
    """The refusal sentence, or None.

    ``deal_phrase`` is "a business combination into" or "a disposal of";
    ``step_phrase`` names the step in the document's words. ``window_start``:
    first day of the fiscal period before the deal's (the deal's own when
    none is declared). ``periods``: proportional Ownership Periods of the
    entity in the group, docstatus below 2, effective on or before the deal
    date (name, ownership_pct, effective_date, end_date, docstatus).
    ``declarations``: [(declaring group, rows)], the group's own node first,
    then each ancestor's; rows are all methods declared for the entity
    (consolidation_method, effective_date, end_date).
    """
    deal = _date(deal_date)
    floor = _date(window_start) or deal
    ended = []
    for row in periods:
        end = _date(row.end_date)
        if end is not None and end < floor:
            continue
        if end is not None and end < deal:
            ended.append((row, end))
            continue
        draft = ", Draft period" if int(row.docstatus or 0) == 0 else ""
        return (f"{entity} is held as a joint operation ({row.name}, proportional, "
                f"{float(row.ownership_pct):g}%{draft}): {deal_phrase} a joint operation {_TAIL}")
    for group, rows in declarations:
        asof = [r for r in rows if _date(r.effective_date) is not None and _date(r.effective_date) <= deal]
        if not asof:
            continue
        latest = max(asof, key=lambda r: _date(r.effective_date))
        end = _date(latest.end_date)
        if latest.consolidation_method != "proportional" or (end is not None and end <= deal):
            continue
        return (f"{entity} is declared a joint operation of {group} (Method Declaration, proportional, "
                f"from {_date(latest.effective_date)}): {deal_phrase} a joint operation {_TAIL}")
    if ended:
        row, end = max(ended, key=lambda pair: pair[1])
        return (f"{entity} was a joint operation until {end} ({row.name}, proportional): {step_phrase} {_TAIL}")
    return None
