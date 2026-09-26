"""Timezone-aware ISO 8601 for close API datetimes (konsol#305 A16b; stories 0.2, 9.1).

Frappe stores every datetime naive, in the site's system time zone
(``frappe.utils.get_system_timezone()``). A close API that returns a naive
``isoformat()`` string ships a time with no zone, which a client cannot place
on a timeline (measured live 25 Sep 2026: ``get_freshness`` returned
"2026-09-16T17:47:49.238943", and B09b's client now refuses it).

Pure: imports no frappe, and is loaded by path in its tests.
"""
from zoneinfo import ZoneInfo


def zoned_iso(naive_dt, tz_name):
    """``naive_dt`` as ISO 8601 with ``tz_name``'s UTC offset attached, e.g.
    "2026-09-16T17:47:49+01:00". ``None`` stays ``None``. A datetime that is
    already aware is converted to ``tz_name`` (its instant is preserved), not
    relabelled with a new offset."""
    if naive_dt is None:
        return None
    tz = ZoneInfo(tz_name)
    aware = naive_dt.replace(tzinfo=tz) if naive_dt.tzinfo is None else naive_dt.astimezone(tz)
    return aware.isoformat()
