"""The rules for a Consolidation Method Declaration (konsol#117 pair 1, task D).

Pure: no frappe, no ClickHouse, so the sentences are host-tested. The
controller (ConsolidationGroup._validate_method_declarations) throws what this
returns.

A declaration is the method a group applies to an entity its ownership links
reach by more than one path, where the links alone do not decide it (design
comment section 2: the weakest-link methods disagree, or no path is all-full
and together they sum past 50%). It lives on the group's node without an
entity, beside the group's other policy; it is effective-dated; one method
applies to an entity on any date.
"""
import datetime

#: The Select's options, in order. ``proportional`` is offered and refused
#: (PROPORTIONAL_REFUSAL) until konsol#117 pair 3 decides it.
METHODS = ("full", "proportional", "equity", "none")

#: What the warehouse stores for a blank End Date: the column's DEFAULT in
#: epm_staging.consolidation_method_declarations (clickhouse._REFERENCE_TABLE_DDL).
OPEN_END = "9999-12-31"

#: Ownership Period's #432 sentence (ownership_period.py), for a declaration.
PROPORTIONAL_REFUSAL = (
    "Proportional consolidation is not implemented (konsol#429): the warehouse would "
    "consolidate this declaration as full with NCI, which is not proportional "
    "consolidation. Use full with NCI, or equity."
)


def _date(value):
    """A date from a date, a datetime or an ISO string; None stays None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def _window(row):
    start, end = _date(row.effective_date), _date(row.end_date)
    return f"{row.consolidation_method}, {start} to {end or 'open'}"


def declaration_problems(group, node_entity, rows):
    """Every reason the node's declarations are refused, as sentences.

    ``group`` is the node's group code, ``node_entity`` the entity the node
    carries ('' or None for a group node), ``rows`` the child rows (objects
    with idx, data_area_id, consolidation_method, effective_date, end_date).
    An empty list means the declarations are fine.
    """
    rows = list(rows or [])
    if not rows:
        return []
    if node_entity:
        # Like the intercompany difference settings: the warehouse keys a
        # group's policy on its node without an entity, and a node that also
        # carries an entity is not that node (consolidation_group.py,
        # _validate_ic_difference).
        return [
            f"This node carries entity {node_entity}, and a consolidation method is declared "
            f"on the group's node without an entity (the one its reporting currency is read "
            f"from). Declare it on the node of group {group} that has no entity."]

    problems = []
    by_entity = {}
    for row in rows:
        where = f"Row {row.idx} ({row.data_area_id})"
        if row.consolidation_method not in METHODS:
            problems.append(
                f"{where}: '{row.consolidation_method}' is not a consolidation method "
                f"({', '.join(METHODS)}).")
        elif row.consolidation_method == "proportional":
            problems.append(f"{where}: {PROPORTIONAL_REFUSAL}")
        start, end = _date(row.effective_date), _date(row.end_date)
        if start and end and end < start:
            problems.append(f"{where}: End Date {end} is before Effective Date {start}.")
        if start:
            by_entity.setdefault(row.data_area_id, []).append((start, end, row))

    for entity, windows in by_entity.items():
        windows.sort(key=lambda w: (w[0], w[2].idx))
        for (start_a, end_a, a), (start_b, _end_b, b) in zip(windows, windows[1:]):
            # Sorted by start, so a and b overlap exactly when a is open or
            # a's last day is on or after b's first day.
            if end_a is None or end_a >= start_b:
                problems.append(
                    f"Method declarations for {entity} overlap: row {a.idx} ({_window(a)}) and "
                    f"row {b.idx} ({_window(b)}). One method applies to an entity on any date; "
                    f"end the earlier declaration the day before the later one starts.")
    return problems
