"""Close home dashboard, pure (trial, Deepak Pai, 8 Nov 2026).

Lays out the "Konsol Home Dashboard" block (one Custom HTML Block on the
Konsol workspace) from what the close app's readers already return. No close
rule is decided here; ``home_api`` names each reader:

- a KPI tile is a list of points, one per period of the window, oldest
  first. Its value is the newest point; its delta is against the point
  before. A point with no value is a gap carrying its reason, never 0: a
  sparkline breaks there, and a tile whose newest point is a gap is an empty
  state naming why and linking to the action;
- tile points come from the existing readers' shapes: ``charts_model``'s
  ``tb_progress`` and ``pl_trend`` charts, ``ic_api.get_ic`` results, and
  period states with ``signoff_gate.latest_signed_runs``;
- the headline is ``get_my_work``'s blocking count; the banners are a build
  in progress (``freshness``'s ``pending``) and incomplete setup
  (``setup.status``); the period card reuses ``overview_model``'s close
  status and freshness texts;
- an entity row is that entity's trial balance status for the current
  period (the sign-off gate's expected and missing, and the caller's
  submitted TBs and TB Exceptions) and its intercompany status (the pairs it
  is a side of in ``get_ic``). There is no per-entity sign-off: sign-off is
  per period.

Every route is a ``/desk/`` path (``overview_model._route``); anything else
raises. Imports nothing from frappe; siblings are loaded by path, so the host
tests need no site.
"""
import datetime
import importlib.util
import os
from urllib.parse import quote

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name):
    spec = importlib.util.spec_from_file_location(
        "konsol_close_home_model_" + name, os.path.join(_HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


overview_model = _load_sibling("overview_model")
period_name = overview_model.period_name

OK = "ok"
EMPTY = "empty"
UNAVAILABLE = "unavailable"

#: The Periods selector's choices; anything else is refused.
PERIOD_CHOICES = (3, 6, 12)

MY_WORK = "/desk/my-work"
SETUP = "/desk/konsol-setup"
BUILDS = "/desk/build-approval"
PERIOD_CLOSE = overview_model.PERIOD_CLOSE
TB_UPLOAD = "/desk/trial-balance-upload"

SPARK_WIDTH = 60
SPARK_HEIGHT = 18


def _route(route):
    return overview_model._route(route)


def _key(value):
    return int(value[0]), int(value[1])


def _noun(n, singular, plural=None):
    return "%d %s" % (n, singular if n == 1 else (plural or singular + "s"))


def unavailable(reason):
    """A section whose reader failed: shown as "Unavailable: <reason>"."""
    return {"state": UNAVAILABLE, "reason": str(reason)}


# --- window and sparkline ------------------------------------------------------

def window(states, current, n):
    """The last ``n`` Regular period keys up to and including ``current``,
    oldest first. ``states`` are ``period_model.period_states`` entries."""
    if n not in PERIOD_CHOICES:
        raise ValueError("periods must be one of %s, not %r"
                         % (", ".join(str(c) for c in PERIOD_CHOICES), n))
    current = _key(current)
    keys = sorted({_key(s["key"]) for s in states if _key(s["key"]) <= current})
    return keys[-n:]


def _num(value):
    return ("%.2f" % value).rstrip("0").rstrip(".")


def spark(values, width=SPARK_WIDTH, height=SPARK_HEIGHT):
    """SVG polyline ``points`` strings, one per run of values between gaps
    (None). x spreads the whole series over ``width``; y puts the smallest
    value at the bottom and the largest at the top (a constant series is a
    level line at mid height). No value: no line."""
    values = list(values)
    shown = [v for v in values if v is not None]
    if not shown:
        return []
    low, high = min(shown), max(shown)
    step = width / (len(values) - 1) if len(values) > 1 else 0

    def y(v):
        if high == low:
            return height / 2
        return height - (v - low) * height / (high - low)

    runs, run = [], []
    for i, v in enumerate(values):
        if v is None:
            if run:
                runs.append(" ".join(run))
            run = []
            continue
        run.append("%s,%s" % (_num(i * step), _num(y(v))))
    if run:
        runs.append(" ".join(run))
    return runs


# --- tile -------------------------------------------------------------------------

def _delta_display(delta, prior_label, unit):
    if delta == 0:
        return "No change vs %s" % prior_label
    text = _num(delta) if isinstance(delta, float) else str(delta)
    return "%s%s%s vs %s" % ("+" if delta > 0 else "", text, unit, prior_label)


def tile(title, points, route, higher_is_better, breakdown=None, empty_action=None,
         delta_unit=""):
    """One KPI tile. ``points`` are ``{"label", "value", "display", "note"}``,
    oldest first. ``higher_is_better`` colours the delta (None: neutral).
    ``empty_action`` is ``(label, route)``, the link of the empty state."""
    route = _route(route)
    points = list(points)
    newest = points[-1] if points else None
    prior = points[-2] if len(points) > 1 else None
    out = {"title": title, "route": route, "points": points,
           "spark": spark([p["value"] for p in points]), "breakdown": breakdown,
           "value": None, "display": None, "prior": None, "delta": None,
           "delta_display": None, "delta_tone": None, "empty": None}
    if newest is None or newest["value"] is None:
        action, action_route = empty_action or (None, None)
        out["state"] = EMPTY
        out["empty"] = {"text": (newest or {}).get("note") or "No data for this period",
                        "action": action,
                        "route": _route(action_route) if action_route else None}
        return out
    out["state"] = OK
    out["value"] = newest["value"]
    out["display"] = newest["display"]
    if prior is not None and prior["value"] is not None:
        delta = newest["value"] - prior["value"]
        if isinstance(delta, float):
            delta = round(delta, 2) + 0.0
        out["prior"] = prior["value"]
        out["delta"] = delta
        out["delta_display"] = _delta_display(delta, prior["label"], delta_unit)
        if delta == 0 or higher_is_better is None:
            out["delta_tone"] = "flat"
        else:
            out["delta_tone"] = "good" if (delta > 0) == bool(higher_is_better) else "bad"
    return out


def _point(label, value, display=None, note=None):
    return {"label": label, "value": value,
            "display": display if value is not None else None, "note": note}


# --- points from the existing readers -------------------------------------------

def _series(chart, name):
    for dataset in chart["datasets"]:
        if dataset["name"] == name:
            return dataset["values"]
    raise ValueError("the chart has no %r series" % name)


def tb_points(chart):
    """From ``charts_model.tb_progress``: per period, trial balances in
    (submitted plus declared exceptions) of those owed."""
    sub = _series(chart, "Submitted")
    exc = _series(chart, "Declared exception")
    mis = _series(chart, "Missing")
    out = []
    for label, s, e, m in zip(chart["labels"], sub, exc, mis):
        total = s + e + m
        if not total:
            out.append(_point(label, None, note="No entity owes a trial balance for %s" % label))
        else:
            out.append(_point(label, s + e, "%d / %d" % (s + e, total)))
    return out


def tb_breakdown(chart):
    if not chart["labels"]:
        return None
    s = _series(chart, "Submitted")[-1]
    e = _series(chart, "Declared exception")[-1]
    m = _series(chart, "Missing")[-1]
    return "%d submitted · %s · %d missing" % (s, _noun(e, "exception"), m)


def _pairs(ic, group):
    for g in ic.get("groups") or ():
        if group is None or g.get("consolidation_group") == group:
            yield from g.get("pairs") or ()


def ic_points(keys, reads, group):
    """Per period, the pairs over tolerance from ``get_ic`` (masked to the
    caller), in ``group`` (None: every group). A period not checked is a gap
    naming why; ``reads`` maps a key to the result or ``{"state": "error",
    "message"}``."""
    out = []
    for key in keys:
        key = _key(key)
        label = period_name(*key)
        ic = reads.get(key) or {"state": "error", "message": "intercompany was not read"}
        if ic.get("state") != "checked":
            out.append(_point(label, None, note=ic.get("message") or ic.get("state")))
            continue
        over = sum(1 for p in _pairs(ic, group) if p.get("match_status") == "over_tolerance")
        out.append(_point(label, over, str(over)))
    return out


def ic_breakdown(ic, group):
    if not ic or ic.get("state") != "checked":
        return None
    pairs = list(_pairs(ic, group))
    over = sum(1 for p in pairs if p.get("match_status") == "over_tolerance")
    text = "%d of %s over tolerance" % (over, _noun(len(pairs), "pair"))
    unmatched = len(ic.get("unmatched") or ())
    if unmatched:
        text += " · %d without a partner" % unmatched
    return text


def _date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def days_points(states, signed):
    """Per period, days from its end date to its sign-off
    (``signoff_gate.latest_signed_runs``). Not signed off is a gap."""
    out = []
    for state in states:
        key = _key(state["key"])
        label = period_name(*key)
        run = signed.get(key)
        if run is None:
            out.append(_point(label, None, note="%s is not signed off yet" % label))
        elif not run.get("signed_off_at"):
            out.append(_point(label, None, note="%s has no sign-off date recorded" % label))
        else:
            days = (_date(run["signed_off_at"]) - _date(state["end_date"])).days
            out.append(_point(label, days, _noun(days, "day")))
    return out


def compact(value):
    """1500 -> "1.5K", -2500000 -> "-2.5M", 12 -> "12"."""
    v = float(value)
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= size:
            return "%s%s" % (("%.1f" % (v / size)).rstrip("0").rstrip("."), suffix)
    return _num(v)


def result_points(chart, currency):
    """From ``charts_model.pl_trend``: the net result series (its second)."""
    values = chart["datasets"][1]["values"]
    unit = "%s " % currency if currency else ""
    return [_point(label, float(v), unit + compact(v)) for label, v in zip(chart["labels"], values)]


# --- headline, banners, period card, entities, setup -------------------------------

def headline(my_work):
    """"N items need you": ``get_my_work``'s blocking count."""
    counts = my_work.get("counts")
    if not isinstance(counts, dict) or "blocking" not in counts:
        raise ValueError("My Work returned no blocking count")
    n = int(counts["blocking"])
    text = "Nothing needs you" if n == 0 else (
        "1 item needs you" if n == 1 else "%d items need you" % n)
    detail = "%d to do · %d waiting" % (int(counts.get("todo") or 0),
                                        int(counts.get("waiting") or 0))
    return {"state": OK, "count": n, "text": text, "detail": detail, "route": MY_WORK}


def banners(freshness, setup):
    """The banners that are true now: a consolidation build in progress,
    then setup not finished. ``setup`` None: the caller may not read setup."""
    out = []
    if freshness and freshness.get("state") == "pending":
        n = int(freshness.get("pending") or 0)
        out.append({"kind": "build", "route": BUILDS, "action": "View",
                    "text": "Consolidation build in progress (%s)" % _noun(n, "build")})
    if setup and setup["done"] < setup["total"]:
        out.append({"kind": "setup", "route": SETUP, "action": "Continue",
                    "text": "Setup %d of %d done" % (setup["done"], setup["total"])})
    return out


def period_card(key, state, freshness, waiting):
    key = _key(key)
    text, tone = overview_model.close_status(key, state)
    fresh_text, fresh_tone = overview_model.freshness_card(freshness)
    return {"state": OK, "title": period_name(*key), "status": state.get("status"),
            "close": {"text": text, "tone": tone},
            "freshness": {"text": fresh_text, "tone": fresh_tone, "route": BUILDS},
            "waiting": int(waiting), "route": PERIOD_CLOSE,
            "waiting_route": "/desk/query-report/My%20Work"}


def _ic_by_entity(ic, group):
    """``{entity: "over" | "ok"}`` from the pairs it is a visible side of;
    None when intercompany was not checked."""
    if not ic or ic.get("state") != "checked":
        return None
    out = {}
    for pair in _pairs(ic, group):
        over = pair.get("match_status") == "over_tolerance"
        for side in ("a", "b"):
            if pair.get("masked_" + side):
                continue
            entity = pair.get("entity_" + side)
            if over or out.get(entity) != "over":
                out[entity] = "over" if over else "ok"
    return out


def entity_rows(key, entities, progress, submitted, exceptions, ic, group, allowed):
    """One row per entity the caller may see (``allowed`` None: all): its
    trial balance status (``submitted`` / ``exception`` / ``missing`` /
    ``not_expected``) and intercompany status (``over`` / ``ok`` / ``none``,
    or ``unknown`` when intercompany was not checked). Open rows (a missing
    TB or a pair over tolerance) first, then by code. An expected entity the
    gate counts in with neither a submitted TB nor an exception the caller
    can read raises (as ``charts_model.tb_progress``)."""
    key = _key(key)
    expected = set(progress["expected"])
    missing = set(progress["missing"])
    ic_status = _ic_by_entity(ic, group)
    rows = []
    for entity in entities:
        code = entity["name"]
        if allowed is not None and code not in allowed:
            continue
        if code not in expected:
            tb = "not_expected"
        elif code in missing:
            tb = "missing"
        elif code in submitted:
            tb = "submitted"
        elif code in exceptions:
            tb = "exception"
        else:
            raise ValueError(
                "the sign-off gate counts %s in for %s, but no submitted trial balance or "
                "TB Exception of it can be read" % (code, period_name(*key)))
        ic_state = "unknown" if ic_status is None else ic_status.get(code, "none")
        rows.append({
            "entity": code, "name": entity.get("entity_name") or code, "tb": tb, "ic": ic_state,
            "open": tb == "missing" or ic_state == "over",
            "route": _route("/desk/trial-balance-submission?data_area_id=%s&fiscal_year=%d"
                            "&fiscal_period=%d" % (quote(code, safe=""), key[0], key[1])),
        })
    rows.sort(key=lambda r: (not r["open"], r["entity"]))
    return rows


def setup_pill(setup):
    """``setup.status``'s done of total; hidden once every step is done.
    None: the caller may not read setup."""
    if setup is None:
        return None
    return {"done": setup["done"], "total": setup["total"],
            "hidden": setup["done"] >= setup["total"], "route": SETUP}
