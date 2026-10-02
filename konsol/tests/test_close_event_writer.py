"""konsol#305 T02a — the writer: `konsol/close/close_event.py`, the one function
that inserts a Close Event (#298 story 10.1, #305-W2-1; amended by #305-W2-5
and #305-W2-9).

- `record(...)` builds a live event (actor = session user, at = now, source
  live, `detail.actor_persona` from the roles), checks it with
  `close_event_model.event_problems` and inserts it through `_insert`.
- `record_backfill(event)` takes a backfill event as read from an old record.
- `_insert` enters the controller's `writing()` and inserts with
  `ignore_permissions`; no commit, no try/except.
- `period_of(doc)` (#305-W2-5): the period an approval belongs to. Ownership
  Period and Historical Equity Rate use the FIRST declared period the record
  affects (`period_status.first_period_affected`), which may be a Closing
  period; none refuses the approval.
- `entity_of(doc)` (#305-W2-9): the entity the trail scopes an event by.

Loaded against a stub frappe (the `_Site` pattern of
test_close_approval_api.py, copied, not imported). The real T01b controller
is loaded by path under a stub `frappe.model.document`, so `writing()` is the
controller's own, and its `before_insert` refusal runs on every stub insert.
"""
import ast
import datetime
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRITER_PY = os.path.join(APP_DIR, "close", "close_event.py")
MODEL_PY = os.path.join(APP_DIR, "close", "close_event_model.py")
PERIOD_MODEL_PY = os.path.join(APP_DIR, "close", "period_model.py")
CONTROLLER_PY = os.path.join(APP_DIR, "consolidation", "doctype", "close_event", "close_event.py")

LEAD = "zz-lead@example.com"
NOW = datetime.datetime(2026, 10, 2, 9, 30, 0)

# Declared periods: (fiscal_year, fiscal_period, start_date, period_type).
PERIODS = (
    (2025, 0, datetime.date(2025, 7, 1), "Opening"),   # listed first on purpose
    (2025, 7, datetime.date(2025, 7, 1), "Regular"),
    (2025, 12, datetime.date(2025, 12, 1), "Regular"),
    (2025, 13, datetime.date(2025, 12, 31), "Closing"),
)


class _Flags(dict):
    def __getattr__(self, n):
        return self.get(n)

    def __setattr__(self, n, v):
        self[n] = v


class _Site:
    def __init__(self, roles=("EPM Admin",), user=LEAD, periods=PERIODS, insert_raises=None):
        self.roles = set(roles)
        self.user = user
        self.periods = list(periods)
        self.insert_raises = insert_raises
        self.inserted = []  # (event dict, writing active, ignore_permissions)
        self.sql_calls = []
        self.commits = 0
        self.flags = _Flags()


def _load_by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frappe(site):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.flags = site.flags
    holder = {}

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    class Document:
        def __init__(self, d=None):
            for k, v in (d or {}).items():
                setattr(self, k, v)
            self._new = True
            self.name = None

        def is_new(self):
            return self._new

        def insert(self, ignore_permissions=False):
            self.before_insert()  # the real controller's refusal
            self.validate()
            if site.insert_raises:
                raise site.insert_raises
            self.name = "CE-%09d" % (len(site.inserted) + 1)
            event = {k: v for k, v in vars(self).items() if not k.startswith("_") and k != "name"}
            site.inserted.append((event, holder["controller"].active(), ignore_permissions))
            self._new = False
            return self

    def get_doc(d, *a, **k):
        assert isinstance(d, dict) and d.get("doctype") == "Close Event", d
        return holder["controller"].CloseEvent(d)

    def sql(query, params=None, as_dict=False, **k):
        site.sql_calls.append((query, dict(params or {})))
        start = params["start"]
        rows = [p for p in site.periods if p[2] == start]
        if "p.period_type = 'Regular'" in query:
            rows = [p for p in rows if p[3] == "Regular"]
        if "ORDER BY (p.period_type <> 'Regular'), y.fiscal_year" in query:
            rows.sort(key=lambda p: (p[3] != "Regular", p[0]))
        return [{"fiscal_year": p[0], "fiscal_period": p[1]} for p in rows]

    def commit():
        site.commits += 1

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.get_doc = get_doc
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.db = types.SimpleNamespace(sql=sql, commit=commit)
    frappe.session = types.SimpleNamespace(user=site.user)
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: NOW
    frappe.utils = utils
    model_pkg = types.ModuleType("frappe.model")
    model_pkg.__path__ = []
    document_mod = types.ModuleType("frappe.model.document")
    document_mod.Document = Document
    model_pkg.document = document_mod
    frappe.model = model_pkg
    return frappe, holder


def _first_period_affected(site):
    def first(date):
        starts = sorted(p[2] for p in site.periods if p[2] >= date)
        return starts[0] if starts else None
    return first


def _load(site):
    """The writer module under the stub site, plus the stub frappe."""
    frappe, holder = _frappe(site)
    pkgs = ["konsol", "konsol.close", "konsol.consolidation", "konsol.consolidation.doctype",
            "konsol.consolidation.doctype.close_event"]
    mods = {n: types.ModuleType(n) for n in pkgs}
    for n in pkgs:
        mods[n].__path__ = []
    period_status = types.ModuleType("konsol.period_status")
    period_status.first_period_affected = _first_period_affected(site)
    mods["konsol"].period_status = period_status
    mods.update({
        "frappe": frappe, "frappe.utils": frappe.utils, "frappe.model": frappe.model,
        "frappe.model.document": frappe.model.document, "konsol.period_status": period_status,
    })
    extra = ["konsol.close.close_event_model", "konsol.close.period_model",
             "konsol.consolidation.doctype.close_event.close_event", "konsol.close.close_event"]
    saved = {n: sys.modules.get(n) for n in list(mods) + extra}
    sys.modules.update(mods)
    try:
        model = _load_by_path("konsol.close.close_event_model", MODEL_PY)
        sys.modules["konsol.close.close_event_model"] = model
        mods["konsol.close"].close_event_model = model
        pmodel = _load_by_path("konsol.close.period_model", PERIOD_MODEL_PY)
        sys.modules["konsol.close.period_model"] = pmodel
        mods["konsol.close"].period_model = pmodel
        controller = _load_by_path("konsol.consolidation.doctype.close_event.close_event", CONTROLLER_PY)
        sys.modules["konsol.consolidation.doctype.close_event.close_event"] = controller
        mods["konsol.consolidation.doctype.close_event"].close_event = controller
        holder["controller"] = controller
        writer = _load_by_path("konsol.close.close_event", WRITER_PY)
        return writer, frappe
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _raises(fn, exc):
    try:
        fn()
    except exc as e:
        return str(e)
    raise AssertionError("expected %s" % exc.__name__)


def _doc(doctype, name="DOC-1", **fields):
    return types.SimpleNamespace(doctype=doctype, name=name, **fields)


# -- record -----------------------------------------------------------------


def test_record_inserts_one_live_event_inside_the_writer():
    site = _Site(roles=("EPM Admin", "EPM Analyst"))
    writer, _ = _load(site)
    name = writer.record("approved", 2025, 7, "IC Balance", "ICB-1", detail={"preparer": "a"})
    assert name == "CE-000000001"
    assert len(site.inserted) == 1
    event, active, ignore_permissions = site.inserted[0]
    assert active is True, "the insert must run inside writing()"
    assert ignore_permissions is True
    assert event["doctype"] == "Close Event"
    assert event["kind"] == "approved"
    assert (event["fiscal_year"], event["fiscal_period"]) == (2025, 7)
    assert (event["reference_doctype"], event["reference_name"]) == ("IC Balance", "ICB-1")
    assert event["actor"] == LEAD
    assert event["at"] == NOW
    assert event["source"] == "live"
    assert event["reason"] is None
    assert event["entity"] is None
    assert event["detail"] == json.dumps({"actor_persona": "close_lead", "preparer": "a"}, sort_keys=True)
    assert site.commits == 0, "the writer never commits; the caller's transaction owns the event"


def test_record_strips_the_reason_and_carries_the_entity():
    site = _Site(roles=("EPM Analyst",))
    writer, _ = _load(site)
    writer.record("self_approved", 2025, 7, "Ownership Period", "OP-1", reason="  on leave  ",
                  entity="ZZ01")
    event = site.inserted[0][0]
    assert event["reason"] == "on leave"
    assert event["entity"] == "ZZ01"
    assert json.loads(event["detail"]) == {"actor_persona": "group_accountant"}


def test_record_refuses_a_rejection_without_a_reason_and_inserts_nothing():
    """Failure path: a malformed event is a caller bug and must be loud."""
    site = _Site()
    writer, frappe = _load(site)
    msg = _raises(lambda: writer.record("rejected", 2025, 7, "IC Balance", "ICB-1"),
                  frappe.ValidationError)
    assert "reason" in msg
    msg = _raises(lambda: writer.record("rejected", 2025, 7, "IC Balance", "ICB-1", reason="   "),
                  frappe.ValidationError)
    assert "reason" in msg
    assert site.inserted == []


def test_record_refuses_an_unknown_kind():
    """Failure path."""
    site = _Site()
    writer, frappe = _load(site)
    msg = _raises(lambda: writer.record("nonsense", 2025, 7), frappe.ValidationError)
    assert "kind" in msg
    assert site.inserted == []


def test_record_lets_an_insert_failure_propagate_uncaught():
    """Failure path: the event and the action share one transaction, so an
    insert that fails must fail the action."""
    boom = RuntimeError("insert failed")
    site = _Site(insert_raises=boom)
    writer, _ = _load(site)
    try:
        writer.record("period_closed", 2025, 7, "EPM Fiscal Year", "2025")
    except RuntimeError as e:
        assert e is boom
    else:
        raise AssertionError("the insert's exception was swallowed")


def test_writing_is_inactive_after_record():
    site = _Site()
    writer, _ = _load(site)
    writer.record("period_closed", 2025, 7, "EPM Fiscal Year", "2025")
    assert writer.close_event_controller.active() is False


def test_the_controller_still_refuses_an_insert_outside_the_writer():
    """The stub insert runs the real controller's before_insert: without the
    writer's `writing()` it is refused."""
    site = _Site()
    writer, frappe = _load(site)
    doc = frappe.get_doc({"doctype": "Close Event", "kind": "approved"})
    _raises(lambda: doc.insert(ignore_permissions=True), frappe.PermissionError)
    assert site.inserted == []


# -- record_backfill --------------------------------------------------------


def _backfill_event(**over):
    event = {
        "kind": "self_approved", "fiscal_year": 2025, "fiscal_period": 7,
        "reference_doctype": "Group Exchange Rate", "reference_name": "GER-1",
        "actor": "old@example.com", "at": datetime.datetime(2025, 8, 3, 10, 0),
        "source": "backfill", "reason": None,
        "detail": {"reason_not_recorded": True, "preparer": "old@example.com"},
    }
    event.update(over)
    return event


def test_record_backfill_inserts_the_event_as_read():
    site = _Site()
    writer, _ = _load(site)
    writer.record_backfill(_backfill_event())
    event, active, _ = site.inserted[0]
    assert active is True
    assert event["source"] == "backfill"
    assert event["actor"] == "old@example.com"
    assert event["at"] == datetime.datetime(2025, 8, 3, 10, 0)
    assert json.loads(event["detail"]) == {"preparer": "old@example.com", "reason_not_recorded": True}


def test_record_backfill_refuses_a_live_source():
    """Failure path."""
    site = _Site()
    writer, frappe = _load(site)
    _raises(lambda: writer.record_backfill(_backfill_event(source="live")), frappe.ValidationError)
    assert site.inserted == []


def test_record_backfill_refuses_a_malformed_event():
    """Failure path: a backfill self-approval with no reason and no
    reason_not_recorded flag."""
    site = _Site()
    writer, frappe = _load(site)
    msg = _raises(lambda: writer.record_backfill(_backfill_event(detail={})), frappe.ValidationError)
    assert "reason" in msg
    assert site.inserted == []


# -- period_of (#305-W2-5) ---------------------------------------------------


def test_period_of_a_doc_with_period_fields():
    writer, _ = _load(_Site())
    for doctype in ("Group Exchange Rate", "IC Balance", "Consolidation Journal",
                    "Trial Balance Submission", "TB Exception"):
        doc = _doc(doctype, fiscal_year="2025", fiscal_period="7")
        assert writer.period_of(doc) == (2025, 7), doctype


def test_period_of_an_ownership_period_is_the_first_period_it_affects():
    """Its date maps to the period starting 2025-07-01; the Regular row wins
    over the Opening row with the same start (listed first)."""
    site = _Site()
    writer, _ = _load(site)
    doc = _doc("Ownership Period", effective_date=datetime.date(2025, 6, 16))
    assert writer.period_of(doc) == (2025, 7)
    doc = _doc("Historical Equity Rate", rate_date=datetime.date(2025, 7, 1))
    assert writer.period_of(doc) == (2025, 7)


def test_period_of_can_be_a_closing_period():
    """#305-W2-5: an Ownership Period from 2025-12-16 first affects P13
    (Closing, from 2025-12-31). Failure path: a Regular-only filter would
    refuse or misplace it."""
    site = _Site()
    writer, _ = _load(site)
    doc = _doc("Ownership Period", effective_date=datetime.date(2025, 12, 16))
    assert writer.period_of(doc) == (2025, 13)
    for query, _params in site.sql_calls:
        assert "p.period_type = 'Regular'" not in query


def test_period_of_refuses_a_date_after_the_last_declared_period():
    """Failure path (#305-W2-5): no declared period refuses the approval."""
    site = _Site()
    writer, frappe = _load(site)
    doc = _doc("Ownership Period", name="OP-9", effective_date=datetime.date(2026, 3, 1))
    msg = _raises(lambda: writer.period_of(doc), frappe.ValidationError)
    assert "declare the period" in msg
    assert "Ownership Period OP-9" in msg
    assert "2026-03-01" in msg


def test_period_of_a_business_combination_and_disposal_delegate():
    writer, _ = _load(_Site())
    bc = _doc("Business Combination",
              _acquisition_period=lambda: {"fiscal_year": 2025, "fiscal_period": 4, "period_code": "P04"})
    assert writer.period_of(bc) == (2025, 4)
    bd = _doc("Business Disposal",
              _disposal_period=lambda: {"fiscal_year": 2025, "fiscal_period": 9, "period_code": "P09"})
    assert writer.period_of(bd) == (2025, 9)


def test_period_of_any_other_doctype_raises_value_error():
    """Failure path: Budget Cycle has fiscal_period too, but is not an
    approval or close document."""
    writer, _ = _load(_Site())
    msg = _raises(lambda: writer.period_of(_doc("Budget Cycle", fiscal_year=2025, fiscal_period=1)),
                  ValueError)
    assert "Budget Cycle" in msg


# -- entity_of (#305-W2-9) ---------------------------------------------------


def test_entity_of_each_approval_doctype():
    writer, _ = _load(_Site())
    assert writer.entity_of(_doc("Ownership Period", data_area_id="ZZ01")) == "ZZ01"
    assert writer.entity_of(_doc("Historical Equity Rate", data_area_id="ZZ02")) == "ZZ02"
    assert writer.entity_of(_doc("Business Combination", acquired_entity="ZZ03")) == "ZZ03"
    assert writer.entity_of(_doc("Business Disposal", disposed_entity="ZZ04")) == "ZZ04"
    for doctype in ("Group Exchange Rate", "Consolidation Journal", "IC Balance"):
        assert writer.entity_of(_doc(doctype, data_area_id="ZZ09",
                                     selling_entity="ZZ05", buying_entity="ZZ06")) is None, doctype


def test_entity_of_covers_every_approval_doctype():
    """The 7 doctypes of close_policy_model.APPROVAL_DOCTYPES, no more."""
    pm = _load_by_path("cpm_for_t02a", os.path.join(APP_DIR, "close", "close_policy_model.py"))
    writer, _ = _load(_Site())
    assert set(writer.ENTITY_FIELDS) == set(pm.APPROVAL_DOCTYPES)


def test_entity_of_any_other_doctype_raises_value_error():
    """Failure path."""
    writer, _ = _load(_Site())
    msg = _raises(lambda: writer.entity_of(_doc("Budget Cycle")), ValueError)
    assert "Budget Cycle" in msg


# -- one writer (P12 lesson: two writers of one table) -----------------------

ALLOWED = {
    "close/close_event.py",
    "consolidation/doctype/close_event/close_event.py",
    "close/trail_api.py",
    "patches/backfill_close_events.py",
    "fiscal_calendar.py",
    "hooks.py",
}
WRITER = "close/close_event.py"
FORBIDDEN = (
    'db.set_value("Close Event"',
    'db.delete("Close Event"',
    'delete_doc("Close Event"',
    'rename_doc("Close Event"',
    "UPDATE `tabClose Event`",
    "DELETE FROM `tabClose Event`",
)
NAMES = ('"Close Event"', "'Close Event'")


def writer_problems(source, rel):
    """Every way ``source`` (the text of konsol/<rel>) breaks the one-writer
    rule, one string per problem."""
    problems = []
    if any(n in source for n in NAMES) and rel not in ALLOWED:
        problems.append(f"{rel} names \"Close Event\" but is not an allowed reader or writer")
    for pattern in FORBIDDEN:
        if pattern in source:
            problems.append(f"{rel} contains {pattern}")
    if rel != WRITER:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return problems + [f"{rel} does not parse"]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = ast.get_source_segment(source, node) or ""
                if ".insert(" in body and any(n in body for n in NAMES):
                    problems.append(f"{rel}:{node.lineno} {node.name} inserts a Close Event")
    return problems


def test_nothing_else_writes_changes_or_deletes_a_close_event():
    bad = []
    for root, dirs, files in os.walk(APP_DIR):
        rel_root = os.path.relpath(root, APP_DIR)
        if rel_root == "tests" or rel_root.startswith("tests" + os.sep):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in ("node_modules", "__pycache__", "public")]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, APP_DIR).replace(os.sep, "/")
            with open(path, encoding="utf-8") as fh:
                bad.extend(writer_problems(fh.read(), rel))
    assert not bad, "\n".join(bad)


def test_the_writer_module_exists_and_inserts():
    """The walk above must have the writer to look at, or it passes empty."""
    with open(WRITER_PY, encoding="utf-8") as fh:
        source = fh.read()
    assert '"Close Event"' in source and ".insert(" in source


def test_the_checker_catches_a_second_writer():
    """Failure path (mirrors test_close_api_contract.py
    test_the_checker_catches_a_bad_endpoint)."""
    bad = writer_problems('frappe.db.set_value("Close Event", n, "reason", "x")\n', "close/trail_api.py")
    assert bad == ['close/trail_api.py contains db.set_value("Close Event"'], bad
    bad = writer_problems('x = "Close Event"\n', "close/signoff_api.py")
    assert len(bad) == 1 and "not an allowed" in bad[0], bad
    source = 'def f():\n    frappe.get_doc({"doctype": "Close Event"}).insert()\n'
    bad = writer_problems(source, "patches/backfill_close_events.py")
    assert len(bad) == 1 and "inserts a Close Event" in bad[0], bad
    assert writer_problems(source, WRITER) == []
    for pattern in FORBIDDEN:
        assert writer_problems(pattern + "\n", "fiscal_calendar.py"), pattern
