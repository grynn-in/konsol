"""Ownership-scope model, pure (konsol#305 G01; stories 2.3 #289, W2-2).

One in-scope rule, held once instead of twice. ``signoff_gate._frequencies``
and ``mywork_api._covered``/``_ownership_scope`` each held a copy of the same
computation: an entity is "covered" at a period's start when a submitted
(``docstatus 1``) Ownership Period names it (``data_area_id``) with an
``effective_date`` on or before the start, and an ``end_date`` that is blank
or on or after the start.

- ``covers(row, start_date) -> bool``: the single-row predicate.
- ``covered(ownership_rows, start_date) -> set``: the covered entity codes.
- ``in_scope(leaves, covered_set) -> set``: the leaves that are covered.
- ``uncovered_with_tb(tb_entities, covered_set) -> set``: the #289 set — a
  submitted TB with no covering ownership.

Imports nothing from frappe or konsol.
"""
import datetime


def _date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and value:
        return datetime.date.fromisoformat(value[:10])
    return None


def covers(row, start_date):
    """Whether one Ownership Period row covers ``start_date``.

    ``row`` is a dict with ``data_area_id``, ``effective_date`` and
    ``end_date``, already ``docstatus 1``. A blank or None ``data_area_id``
    never covers. Dates may arrive as ``date``, ``datetime`` or an ISO
    string. ``start_date`` is never guessed: ``None`` raises ``ValueError``.
    """
    if start_date is None:
        raise ValueError("start_date is required")
    start = _date(start_date)
    if not row.get("data_area_id"):
        return False
    effective = _date(row.get("effective_date"))
    if effective is None or effective > start:
        return False
    end = _date(row.get("end_date"))
    return end is None or end >= start


def covered(ownership_rows, start_date):
    """The set of entity codes a submitted Ownership Period covers at ``start_date``."""
    if start_date is None:
        raise ValueError("start_date is required")
    return {row["data_area_id"] for row in ownership_rows if covers(row, start_date)}


def in_scope(leaves, covered_set):
    """The ``leaves`` (an iterable of codes, a dict's keys included) that are in ``covered_set``."""
    return {code for code in leaves if code in covered_set}


def uncovered_with_tb(tb_entities, covered_set):
    """The #289 set: entities with a submitted TB and no covering ownership."""
    return {code for code in tb_entities if code not in covered_set}
