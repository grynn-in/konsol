"""The joint-operation refusal of a deal (konsol#117 pair 3, review finding D).

Pure: no frappe, no ClickHouse. Business Combination and Business Disposal
gather the rows and throw what this returns.

``proportional`` is IFRS 11.20 proportionate consolidation. A business
combination into, or a disposal of an interest in, a joint operation (IFRS
11.21A), and a step from a joint operation to control, are not implemented. A
deal is refused by name when the entity in the group is, on the deal date:

* held ``proportional`` by an Ownership Period, draft or submitted, of any
  owner (a Draft the approval would submit keeps its hand-set method);
* declared ``proportional`` by a Consolidation Method Declaration of the
  group's own node; or
* a joint operation until within ``STEP_DAYS`` days before the deal date (a
  step to control).

This check sees only those rows. dbt's assert_acquisition_accounts_declared
and assert_disposal_accounts_declared are the backstop for anything it cannot
see (a period written outside the form, or a method the links alone imply).
"""
import datetime

#: epm_staging.ownership_periods: a joint-operation period that ended up to
#: this many days before the deal is a step from a joint operation to control.
STEP_DAYS = 31

_TAIL = "is not implemented (IFRS 11.21A). Record the change as an Ownership Period."


def _date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def joint_operation_problem(entity, group, deal_date, deal_phrase, periods, declarations):
    """The refusal sentence, or None.

    ``deal_phrase`` is "a business combination into" or "a disposal of".
    ``periods``: proportional Ownership Periods of the entity in the group,
    docstatus below 2, effective on or before the deal date (name,
    ownership_pct, effective_date, end_date, docstatus). ``declarations``:
    proportional method-declaration rows of the entity (effective_date,
    end_date).
    """
    deal = _date(deal_date)
    step_from = deal - datetime.timedelta(days=STEP_DAYS)
    ended = []
    for row in periods:
        end = _date(row.end_date)
        if end is not None and end < step_from:
            continue
        if end is not None and end < deal:
            ended.append((row, end))
            continue
        draft = ", Draft period" if int(row.docstatus or 0) == 0 else ""
        return (f"{entity} is held as a joint operation ({row.name}, proportional, "
                f"{float(row.ownership_pct):g}%{draft}): {deal_phrase} a joint operation {_TAIL}")
    for row in declarations:
        start, end = _date(row.effective_date), _date(row.end_date)
        if start is not None and start > deal:
            continue
        if end is not None and end < deal:
            continue
        return (f"{entity} is declared a joint operation of {group} (Method Declaration, proportional, "
                f"from {start}): {deal_phrase} a joint operation {_TAIL}")
    if ended:
        row, end = max(ended, key=lambda pair: pair[1])
        return (f"{entity} was a joint operation until {end} ({row.name}, proportional): a step from a "
                f"joint operation to control {_TAIL}")
    return None
