"""konsol#305 O54: ownership_change.py — the one frappe-bound reader of an
ownership change's context (story 4.2; C-O4). Not whitelisted.

``context(consolidation_group, entity, fiscal_year, fiscal_period)`` reads
the node's submitted and draft Ownership Periods, the fiscal calendar and
``signoff_gate.latest_signed_runs()`` keys, and returns what
``ownership_change_model.problems`` and ``effect`` need. ``effect_for(doc)``
gives the effect of a saved draft, by its ``supersedes``.

Loaded against a stub frappe, a stub ``konsol.fiscal_calendar`` and a stub
``konsol.close.signoff_gate``. The context is fed to the REAL
``ownership_change_model.py``, loaded by path (the W5b feed-the-real-producer
rule).
"""
import contextlib
import datetime
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
HELPER_PY = os.path.join(CLOSE_DIR, "ownership_change.py")

GROUP = "ECL_GROUP"
LEAF = "ZZ5B4"


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODEL = _load_path("ownership_change_model_for_o54_test",
                   os.path.join(CLOSE_DIR, "ownership_change_model.py"))


def _month_end(y, m):
    nxt = datetime.date(y + m // 12, m % 12 + 1, 1)
    return nxt - datetime.timedelta(days=1)


def _calendar():
    """FY2025 P01-P12 Regular plus P13 Closing, as fiscal_period_rows() gives
    them (dates as date objects, the way the database hands them back)."""
    rows = []
    for m in range(1, 13):
        rows.append({"fiscal_year": 2025, "fiscal_period": m, "period_code": "P%02d" % m,
                     "period_label": "P%02d" % m, "period_type": "Regular",
                     "start_date": datetime.date(2025, m, 1), "end_date": _month_end(2025, m),
                     "quarter": (m - 1) // 3 + 1, "status": "Open"})
    rows.append({"fiscal_year": 2025, "fiscal_period": 13, "period_code": "P13",
                 "period_label": "P13", "period_type": "Closing",
                 "start_date": datetime.date(2025, 12, 31), "end_date": datetime.date(2025, 12, 31),
                 "quarter": 4, "status": "Open"})
    return rows


def _op(name, eff, end=None, pct=100.0, method="full", docstatus=1, group=GROUP, entity=LEAF,
        supersedes=None, superseded_end_date=None):
    return {"name": name, "consolidation_group": group, "data_area_id": entity,
            "effective_date": eff, "end_date": end, "ownership_pct": pct,
            "consolidation_method": method, "docstatus": docstatus,
            "supersedes": supersedes, "superseded_end_date": superseded_end_date}


class _Doc(dict):
    """A saved Ownership Period document: attribute and ``get`` access."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


class _Site:
    def __init__(self, ops=(), signed=()):
        self.ops = [dict(o) for o in ops]
        self.signed = list(signed)
        self.periods = _calendar()
        self.reads = []
        self.writes = []
        self.whitelisted = []


def _matches(row, filters):
    for field, want in (filters or {}).items():
        have = row.get(field)
        if isinstance(want, (list, tuple)):
            op, value = want[0], want[1]
            if op == "is" and value == "not set":
                if have not in (None, ""):
                    return False
            elif op == "in":
                if have not in value:
                    return False
            elif op == "!=":
                if have == value:
                    return False
            else:
                raise AssertionError("the stub does not know filter %r" % (want,))
        elif have != want:
            return False
    return True


def _stubs(site):
    frappe = types.ModuleType("frappe")

    def get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **kw):
        site.reads.append(("get_all", doctype, {"filters": filters, "fields": fields,
                                                "order_by": order_by,
                                                "limit_page_length": limit_page_length}))
        assert doctype == "Ownership Period", doctype
        rows = [r for r in site.ops if _matches(r, filters)]
        return [_Doc({f: r.get(f) for f in fields}) for r in rows]

    def whitelist(*a, **k):
        site.whitelisted.append((a, k))
        return lambda fn: fn

    def _write(*a, **k):
        site.writes.append((a, k))

    frappe.get_all = get_all
    frappe.whitelist = whitelist
    frappe.db = types.SimpleNamespace(set_value=_write, sql=_write, commit=_write)

    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    fiscal_calendar = types.ModuleType("konsol.fiscal_calendar")

    def fiscal_period_rows():
        site.reads.append(("calendar",))
        return [dict(r) for r in site.periods]

    fiscal_calendar.fiscal_period_rows = fiscal_period_rows
    konsol.fiscal_calendar = fiscal_calendar

    close = types.ModuleType("konsol.close")
    close.__path__ = []
    konsol.close = close
    signoff_gate = types.ModuleType("konsol.close.signoff_gate")

    def latest_signed_runs(fields=()):
        site.reads.append(("signed",))
        return {(fy, fp): {"name": "AR-%d-%d" % (fy, fp), "fiscal_year": fy, "fiscal_period": fp}
                for fy, fp in site.signed}

    signoff_gate.latest_signed_runs = latest_signed_runs
    close.signoff_gate = signoff_gate

    return {"frappe": frappe, "konsol": konsol, "konsol.fiscal_calendar": fiscal_calendar,
            "konsol.close": close, "konsol.close.signoff_gate": signoff_gate}


@contextlib.contextmanager
def _installed(stubs):
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class _Helper:
    """The module under test, and a way to call it with the stubs installed
    (``signoff_gate`` is imported lazily, at call time)."""

    def __init__(self, site, load_stubs=None):
        self.site = site
        self.stubs = _stubs(site)
        load_with = dict(self.stubs) if load_stubs is None else load_stubs
        with _installed(load_with):
            self.module = _load_path("ownership_change_under_test", HELPER_PY)

    def __call__(self, fn_name, *args):
        with _installed(self.stubs):
            return getattr(self.module, fn_name)(*args)


def _raises(fn, *args):
    try:
        fn(*args)
    except ValueError as exc:
        return str(exc)
    raise AssertionError("expected ValueError from %r" % (args,))


OPEN_ENDED = _op("OP-1", datetime.date(2025, 1, 1))


# -- context ------------------------------------------------------------------

def test_context_of_one_open_ended_period():
    site = _Site(ops=[OPEN_ENDED], signed=[(2025, 11), (2025, 8)])
    helper = _Helper(site)
    ctx = helper("context", GROUP, LEAF, 2025, 10)

    assert ctx["entity"] == LEAF
    assert ctx["effective_date"] == "2025-10-01"
    assert ctx["current"] == {"name": "OP-1", "effective_date": "2025-01-01", "end_date": None,
                              "ownership_pct": 100.0, "consolidation_method": "full"}
    assert not ctx["later_exists"]
    assert not ctx["pending_exists"]
    assert ctx["signed_keys"] == [(2025, 8), (2025, 11)]
    assert ctx["period_rows"] == site.periods

    # One read of each source, and nothing written.
    kinds = [r[0] for r in site.reads]
    assert kinds.count("get_all") == 1 and kinds.count("calendar") == 1 and kinds.count("signed") == 1
    filters = site.reads[0][2]["filters"]
    assert filters["consolidation_group"] == GROUP and filters["data_area_id"] == LEAF
    assert site.writes == []
    assert site.whitelisted == []


def test_context_feeds_the_real_model():
    site = _Site(ops=[OPEN_ENDED], signed=[(2025, 11), (2025, 8)])
    helper = _Helper(site)
    ctx = helper("context", GROUP, LEAF, 2025, 10)
    change = helper("change", ctx, 80, "full")
    assert change == {"entity": LEAF, "effective_date": "2025-10-01",
                      "ownership_pct": 80, "consolidation_method": "full"}

    assert MODEL.problems(change, ctx["current"], ctx["later_exists"], ctx["pending_exists"],
                          ctx["period_rows"]) == []
    effect = MODEL.effect(change, ctx["current"], ctx["period_rows"], ctx["signed_keys"])
    assert effect["current_ends"] == "2025-09-30"
    assert effect["first_period"] == "FY2025 P10"
    assert effect["resign"] == ["FY2025 P11"]   # P08 is before the change


def test_context_of_a_group_node_matches_a_blank_entity():
    site = _Site(ops=[_op("OP-G", datetime.date(2025, 1, 1), entity=None, group="SUB_GROUP")])
    helper = _Helper(site)
    ctx = helper("context", "SUB_GROUP", "", 2025, 10)
    assert site.reads[0][2]["filters"]["data_area_id"] == ["is", "not set"]
    assert ctx["current"]["name"] == "OP-G"
    # The sentences name the node: a group node is named by its group.
    assert ctx["entity"] == "SUB_GROUP"


def test_context_names_the_later_period_date_and_the_pending_draft():
    site = _Site(ops=[
        _op("OP-1", datetime.date(2025, 1, 1), end=datetime.date(2025, 11, 30)),
        _op("OP-3", datetime.date(2026, 3, 1)),
        _op("OP-2", datetime.date(2025, 12, 1), end=datetime.date(2026, 2, 28)),
        _op("OP-D", datetime.date(2025, 6, 1), pct=60.0, docstatus=0),
        _op("OP-X", datetime.date(2025, 3, 1), docstatus=2),   # cancelled: ignored
        _op("OP-OTHER", datetime.date(2025, 1, 1), entity="ZZOTHER"),  # another node
    ])
    helper = _Helper(site)
    ctx = helper("context", GROUP, LEAF, 2025, 10)
    assert ctx["current"]["name"] == "OP-1"
    assert ctx["current"]["end_date"] == "2025-11-30"
    assert ctx["later_exists"] == "2025-12-01"   # the earliest later start
    assert ctx["pending_exists"] == "OP-D"

    change = helper("change", ctx, 80, "full")
    got = MODEL.problems(change, ctx["current"], ctx["later_exists"], ctx["pending_exists"],
                         ctx["period_rows"])
    assert got == [
        "%s already has an ownership period from 2025-12-01: change or cancel that one first." % LEAF,
        "A change for %s is already awaiting approval (OP-D): edit that draft." % LEAF,
    ]


def test_context_with_no_covering_period_gives_current_none():
    site = _Site(ops=[_op("OP-1", datetime.date(2025, 1, 1), end=datetime.date(2025, 6, 30))])
    ctx = _Helper(site)("context", GROUP, LEAF, 2025, 10)
    assert ctx["current"] is None


def test_context_of_a_non_regular_period_leaves_the_refusal_to_the_model():
    site = _Site(ops=[OPEN_ENDED])
    helper = _Helper(site)
    ctx = helper("context", GROUP, LEAF, 2025, 13)
    assert ctx["effective_date"] == "2025-12-31"
    change = helper("change", ctx, 80, "full")
    got = MODEL.problems(change, ctx["current"], None, None, ctx["period_rows"])
    assert got and got[0].startswith("Ownership changes take effect on the first day of a period")


# -- failure paths: corrupt data and unknown keys raise, never "pick one" -------

def test_two_submitted_periods_covering_the_start_raise():
    site = _Site(ops=[OPEN_ENDED, _op("OP-2", datetime.date(2025, 6, 1))])
    helper = _Helper(site)
    msg = _raises(helper, "context", GROUP, LEAF, 2025, 10)
    assert "OP-1" in msg and "OP-2" in msg


def test_a_period_the_calendar_does_not_hold_raises():
    site = _Site(ops=[OPEN_ENDED])
    msg = _raises(_Helper(site), "context", GROUP, LEAF, 2025, 14)
    assert "FY2025 P14" in msg


def test_two_drafts_are_both_named():
    site = _Site(ops=[OPEN_ENDED,
                      _op("OP-D2", datetime.date(2025, 7, 1), docstatus=0),
                      _op("OP-D1", datetime.date(2025, 8, 1), docstatus=0)])
    ctx = _Helper(site)("context", GROUP, LEAF, 2025, 10)
    assert ctx["pending_exists"] == "OP-D1, OP-D2"


# -- effect_for -------------------------------------------------------------------

def _draft(**kw):
    d = _op("OP-NEW", "2025-10-01", pct=80.0, docstatus=0, supersedes="OP-1",
            superseded_end_date=None)
    d.update(doctype="Ownership Period")
    d.update(kw)
    return _Doc(d)


def test_effect_for_a_draft_equals_the_model_effect():
    site = _Site(ops=[OPEN_ENDED], signed=[(2025, 11)])
    helper = _Helper(site)
    got = helper("effect_for", _draft())
    current = {"name": "OP-1", "effective_date": "2025-01-01", "end_date": None,
               "ownership_pct": 100.0, "consolidation_method": "full"}
    change = {"entity": LEAF, "effective_date": "2025-10-01", "ownership_pct": 80.0,
              "consolidation_method": "full"}
    assert got == MODEL.effect(change, current, site.periods, [(2025, 11)])
    assert got["resign"] == ["FY2025 P11"]
    assert site.writes == []


def test_effect_for_a_desk_draft_without_supersedes_is_none():
    site = _Site(ops=[OPEN_ENDED])
    assert _Helper(site)("effect_for", _draft(supersedes=None)) is None


def test_effect_for_a_missing_or_unsubmitted_predecessor_raises():
    for ops in ([], [_op("OP-1", datetime.date(2025, 1, 1), docstatus=2)],
                [_op("OP-1", datetime.date(2025, 1, 1), docstatus=0)]):
        site = _Site(ops=ops)
        msg = _raises(_Helper(site), "effect_for", _draft())
        assert "OP-1" in msg


def test_effect_for_a_submitted_change_raises():
    site = _Site(ops=[OPEN_ENDED])
    msg = _raises(_Helper(site), "effect_for", _draft(docstatus=1))
    assert "OP-NEW" in msg


# -- structure ------------------------------------------------------------------

def test_signoff_gate_is_imported_lazily():
    """The module loads under a stub ``konsol.close`` that has no
    ``signoff_gate`` (C-X1): it is imported only inside the call."""
    site = _Site(ops=[OPEN_ENDED])
    stubs = _stubs(site)
    no_gate = {k: v for k, v in stubs.items() if k != "konsol.close.signoff_gate"}
    no_gate["konsol.close"] = types.ModuleType("konsol.close")
    no_gate["konsol.close"].__path__ = []
    _Helper(site, load_stubs=no_gate)


def test_the_helper_is_not_whitelisted():
    site = _Site()
    _Helper(site)
    assert site.whitelisted == []
    with open(HELPER_PY) as fh:
        assert "frappe.whitelist" not in fh.read()
