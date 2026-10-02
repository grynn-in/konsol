"""konsol#305 T06b: the backfill patch reads the old records, inserts the
missing Close Events through close_event.record_backfill, and is idempotent.

The patch runs under a stub frappe (the _Site/_call pattern of
test_close_checks_api.py, copied, not imported). close_event is a stub that
records what record_backfill receives and answers period_of from the site;
close_event_backfill_model and close_policy_model are the real pure modules,
loaded by path.
"""
import datetime
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "backfill_close_events.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
CLOSE_DIR = os.path.join(APP_DIR, "close")

APPROVAL_DOCTYPES = (
    "Consolidation Journal", "Business Combination", "Business Disposal",
    "Group Exchange Rate", "Ownership Period", "Historical Equity Rate", "IC Balance",
)
ENTITY_FIELDS = {
    "Ownership Period": "data_area_id", "Historical Equity Rate": "data_area_id",
    "Business Combination": "acquired_entity", "Business Disposal": "disposed_entity",
    "Group Exchange Rate": None, "Consolidation Journal": None, "IC Balance": None,
}
# The fields each stub doctype has (frappe.get_meta(...).has_field).
FIELDS = {
    "Group Exchange Rate": {"fiscal_year", "fiscal_period", "amended_from"},
    "IC Balance": {"fiscal_year", "fiscal_period"},
    "Ownership Period": {"data_area_id", "effective_date", "amended_from"},
    "Trial Balance Submission": {"data_area_id", "uploaded_on_behalf", "amended_from",
                                 "fiscal_year", "fiscal_period"},
    "EPM Fiscal Year": {"fiscal_year", "closing_note"},
}


def _dt(day, hour=9):
    return datetime.datetime(2026, 9, day, hour, 0, 0)


def _v(name, doctype, docname, owner, day, changed=None, row_changed=None):
    data = {"added": [], "changed": changed or [], "removed": [], "row_changed": row_changed or []}
    # Frappe stores Version data indented (measured on live); the patch must parse it.
    return {"name": name, "ref_doctype": doctype, "docname": docname, "owner": owner,
            "creation": _dt(day), "data": json.dumps(data, indent=1)}


SUBMIT = [["docstatus", 0, 1]]


def _site_rows():
    docs = {
        "Group Exchange Rate": [
            {"name": "GER-1", "owner": "a@x", "fiscal_year": 2026, "fiscal_period": 7},
            {"name": "GER-2", "owner": "a@x", "fiscal_year": 2026, "fiscal_period": 7},
        ],
        "IC Balance": [
            {"name": "ICB-1", "owner": "a@x", "fiscal_year": 2026, "fiscal_period": 8},
        ],
        "Ownership Period": [
            {"name": "OP-1", "owner": "a@x", "data_area_id": "ZZE1",
             "effective_date": datetime.date(2026, 7, 1)},
            {"name": "OP-BAD", "owner": "a@x", "data_area_id": "ZZE2",
             "effective_date": datetime.date(2099, 1, 1)},
        ],
        "Trial Balance Submission": [
            {"name": "TB-1", "owner": "acc@x", "data_area_id": "ZZE1", "uploaded_on_behalf": 0,
             "amended_from": None, "fiscal_year": 2026, "fiscal_period": 7},
        ],
        "EPM Fiscal Year": [
            {"name": "2026", "owner": "lead@x", "fiscal_year": 2026,
             "closing_note": "P07 closed on 2026-09-20 by lead@x: July done"},
        ],
    }
    versions = [
        # self-approved: the submitter is the owner; its reason is in a Comment
        _v("V1", "Group Exchange Rate", "GER-1", "a@x", 2, changed=SUBMIT),
        # approved by someone else
        _v("V2", "Group Exchange Rate", "GER-2", "b@x", 3, changed=SUBMIT),
        # a Reject by b (workflow state only), then b approves: b is not a
        # preparer when the workflow state field is read from Workflow
        _v("V3", "IC Balance", "ICB-1", "b@x", 4, changed=[["status", "Pending Approval", "Draft"]]),
        _v("V4", "IC Balance", "ICB-1", "b@x", 5, changed=SUBMIT),
        _v("V5", "Ownership Period", "OP-1", "a@x", 6, changed=SUBMIT),
        _v("V6", "Ownership Period", "OP-BAD", "a@x", 6, changed=SUBMIT),
        _v("V7", "Trial Balance Submission", "TB-1", "acc@x", 10, changed=SUBMIT),
        _v("V8", "EPM Fiscal Year", "2026", "lead@x", 20,
           row_changed=[["periods", 7, "row-p07", [["status", "Open", "Closed"]]]]),
        # the run's own Version: Acknowledged -> Re-sign Needed
        _v("V9", "Assertion Run", "RUN-1", "lead@x", 22,
           changed=[["signoff_status", "Acknowledged", "Re-sign Needed"]]),
    ]
    comments = [
        {"reference_doctype": "Group Exchange Rate", "reference_name": "GER-1", "owner": "a@x",
         "creation": _dt(2, 8), "comment_type": "Comment",
         "content": "Self-approved by a@x under Close Settings (Allowed with reason): month-end rush"},
        {"reference_doctype": "Group Exchange Rate", "reference_name": "GER-2", "owner": "b@x",
         "creation": _dt(2, 10), "comment_type": "Comment", "content": "Rejected: wrong source"},
    ]
    runs = [
        {"name": "RUN-1", "fiscal_year": 2026, "fiscal_period": 7, "signed_off_by": "lead@x",
         "signed_off_at": _dt(21), "signoff_status": "Re-sign Needed", "status": "Warned",
         "override_reason": None, "acknowledgement": "checked the warning",
         "affected_by": "TB-1"},
    ]
    periods = [{"name": "row-p07", "fiscal_year": 2026, "fiscal_period": 7, "period_code": "P07"}]
    workflows = [{"document_type": "IC Balance", "workflow_state_field": "status", "is_active": 1}]
    return docs, versions, comments, runs, periods, workflows


EXPECTED_KINDS = {
    "self_approved": 2,      # GER-1 (with the Comment's reason), OP-1
    "approved": 2,           # GER-2, ICB-1
    "rejected": 1,           # GER-2's Comment
    "tb_submitted": 1,
    "period_closed": 1,
    "signed_off": 1,
    "signoff_voided": 1,
}


class _Site:
    def __init__(self):
        (self.docs, self.versions, self.comments, self.runs, self.periods,
         self.workflows) = _site_rows()
        self.events = []          # Close Events already in the table
        self.inserted = []        # what record_backfill received
        self.calls = []           # every frappe read/write, in order
        self.bad_places = {("Ownership Period", "OP-BAD")}
        self.insert_raises = False
        self.journals = []
        self.commits = 0


def _matches(row, filters):
    for key, cond in (filters or {}).items():
        value = row.get(key)
        if isinstance(cond, (list, tuple)):
            op, arg = cond
            if op == "in":
                if value not in arg:
                    return False
            elif op == "is":
                if (arg == "set") != bool(value):
                    return False
            elif op == "like":
                if not str(value or "").startswith(arg.rstrip("%")):
                    return False
            else:
                raise AssertionError("stub does not know operator %r" % op)
        elif value != cond:
            return False
    return True


class _Dict(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def _frappe(site):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe._dict = _Dict

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    def reload_doc(module, dt, name, **k):
        site.calls.append(("reload_doc", module, dt, name))

    def get_all(doctype, filters=None, fields=None, order_by=None, **k):
        site.calls.append(("get_all", doctype))
        if doctype == "Version":
            rows = site.versions
        elif doctype == "Comment":
            rows = site.comments
        elif doctype == "Assertion Run":
            rows = site.runs
        elif doctype == "Consolidation Journal":
            rows = site.journals
        elif doctype == "Close Event":
            rows = site.events
        elif doctype == "Workflow":
            rows = site.workflows
        elif doctype in site.docs:
            rows = site.docs[doctype]
        else:
            rows = []
        out = []
        for r in rows:
            if _matches(r, filters):
                if doctype in FIELDS:  # a document read asks only for fields it has
                    assert set(fields) <= FIELDS[doctype] | {"name", "owner"}, (doctype, fields)
                out.append(_Dict({f: r.get(f) for f in fields}))
        return out

    def sql(query, values=None, as_dict=False, **k):
        site.calls.append(("sql",))
        assert "tabEPM Fiscal Year Period" in query, query
        return [_Dict(p) for p in site.periods]

    def get_meta(doctype):
        fields = FIELDS.get(doctype, set())
        return types.SimpleNamespace(has_field=lambda f: f in fields)

    def get_doc(doctype, name=None):
        site.calls.append(("get_doc", doctype, name))
        row = next(r for r in site.docs[doctype] if r["name"] == name)
        return _Dict(row, doctype=doctype)

    def commit():
        site.commits += 1

    frappe.throw = throw
    frappe.reload_doc = reload_doc
    frappe.get_all = get_all
    frappe.get_meta = get_meta
    frappe.get_doc = get_doc
    frappe.db = types.SimpleNamespace(sql=sql, commit=commit)
    return frappe


def _close_event(site, frappe):
    ce = types.ModuleType("konsol.close.close_event")
    ce.ENTITY_FIELDS = ENTITY_FIELDS

    def record_backfill(event):
        site.calls.append(("record_backfill",))
        if site.insert_raises:
            raise RuntimeError("insert failed")
        assert event.get("source") == "backfill", event
        site.inserted.append(dict(event))
        site.events.append(dict(event, name="CE-%d" % len(site.events)))
        return "CE-%d" % len(site.events)

    def period_of(doc):
        if (doc.doctype, doc.name) in site.bad_places:
            frappe.throw("Ownership Period %s changes no declared fiscal period: declare the "
                         "period in EPM Fiscal Year first." % doc.name)
        if doc.doctype == "Ownership Period":
            return doc.effective_date.year, doc.effective_date.month
        return int(doc.fiscal_year), int(doc.fiscal_period)

    def entity_of(doc):
        field = ENTITY_FIELDS[doc.doctype]
        return (doc.get(field) or None) if field else None

    ce.record_backfill = record_backfill
    ce.period_of = period_of
    ce.entity_of = entity_of
    return ce


def _load_real(name):
    spec = importlib.util.spec_from_file_location(
        "konsol.close." + name, os.path.join(CLOSE_DIR, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(site, fn, capture=None):
    frappe = _frappe(site)
    close_event = _close_event(site, frappe)
    backfill_model = _load_real("close_event_backfill_model")
    policy_model = _load_real("close_policy_model")
    konsol = types.ModuleType("konsol")
    close = types.ModuleType("konsol.close")
    close.close_event = close_event
    close.close_event_backfill_model = backfill_model
    close.close_policy_model = policy_model
    konsol.close = close
    mods = {
        "frappe": frappe, "konsol": konsol, "konsol.close": close,
        "konsol.close.close_event": close_event,
        "konsol.close.close_event_backfill_model": backfill_model,
        "konsol.close.close_policy_model": policy_model,
    }
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("backfill_close_events_under_test", PATCH_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if capture is not None:
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                result = getattr(module, fn)()
            capture.append(buf.getvalue())
            return result
        return getattr(module, fn)()
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _kinds(events):
    out = {}
    for e in events:
        out[e["kind"]] = out.get(e["kind"], 0) + 1
    return out


def _one(events, kind, ref_name):
    found = [e for e in events if e["kind"] == kind and e["reference_name"] == ref_name]
    assert len(found) == 1, (kind, ref_name, found)
    return found[0]


class _raises:
    """``with _raises(E):`` -- the block must raise E (no pytest fixtures)."""

    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is not None, "expected %s, nothing was raised" % self.exc.__name__
        return issubclass(exc_type, self.exc)



def test_execute_reloads_close_event_first():
    site = _Site()
    _call(site, "execute", capture=[])
    assert site.calls[0] == ("reload_doc", "consolidation", "doctype", "close_event")


def test_execute_inserts_the_expected_kinds_through_record_backfill():
    site = _Site()
    _call(site, "execute", capture=[])
    assert _kinds(site.inserted) == EXPECTED_KINDS
    assert all(e["source"] == "backfill" for e in site.inserted)
    assert site.commits == 0, "the patch runner commits; the patch never does"


def test_self_approval_reason_comes_from_the_comment():
    site = _Site()
    _call(site, "execute", capture=[])
    ger1 = _one(site.inserted, "self_approved", "GER-1")
    assert ger1["reason"] == "month-end rush"
    assert "reason_not_recorded" not in ger1["detail"]
    op1 = _one(site.inserted, "self_approved", "OP-1")
    assert op1["detail"]["reason_not_recorded"]


def test_entity_and_placement_come_from_the_document():
    site = _Site()
    _call(site, "execute", capture=[])
    op1 = _one(site.inserted, "self_approved", "OP-1")
    assert (op1["fiscal_year"], op1["fiscal_period"], op1["entity"]) == (2026, 7, "ZZE1")
    ger2 = _one(site.inserted, "approved", "GER-2")
    assert ger2["entity"] is None  # group-level (W2-9)
    tb = _one(site.inserted, "tb_submitted", "TB-1")
    assert tb["entity"] == "ZZE1"


def test_workflow_state_field_is_read_so_a_reject_is_not_an_edit():
    # Failure path: without Workflow.workflow_state_field, b's Reject
    # counts as an edit and ICB-1 becomes self_approved.
    site = _Site()
    _call(site, "execute", capture=[])
    icb = _one(site.inserted, "approved", "ICB-1")
    assert icb["detail"]["preparers"] == ["a@x"]


def test_period_and_signoff_events_use_their_own_records():
    site = _Site()
    _call(site, "execute", capture=[])
    closed = _one(site.inserted, "period_closed", "2026")
    assert (closed["fiscal_year"], closed["fiscal_period"]) == (2026, 7)
    assert closed["reason"] == "July done"
    signed = _one(site.inserted, "signed_off", "RUN-1")
    assert signed["detail"]["signoff_status"] == "Acknowledged"
    voided = _one(site.inserted, "signoff_voided", "RUN-1")
    assert voided["actor"] == "lead@x"


def test_an_unplaceable_document_is_counted_and_the_rest_still_insert():
    site = _Site()
    out = []
    summary = _call(site, "execute", capture=out)
    assert summary["unplaced"] == {"no declared period (#305-W2-5)": 1}
    assert not [e for e in site.inserted if e["reference_name"] == "OP-BAD"]
    assert _kinds(site.inserted) == EXPECTED_KINDS
    assert "no declared period (#305-W2-5)" in out[0]


def test_an_error_that_is_not_a_refusal_is_not_swallowed():
    # Only a refusal (frappe.ValidationError) is an unplaced document; a
    # caller bug in period_of must stop the patch.
    site = _Site()
    site.docs["Ownership Period"][0]["effective_date"] = None  # .year on None
    with _raises(AttributeError):
        _call(site, "execute", capture=[])
    assert site.inserted == []


def test_second_execute_inserts_nothing():
    site = _Site()
    _call(site, "execute", capture=[])
    first = len(site.inserted)
    assert first > 0
    out = []
    summary = _call(site, "execute", capture=out)
    assert len(site.inserted) == first, "a second run inserted again"
    assert summary["by_kind"] == {}


def test_live_events_set_the_cutoff():
    site = _Site()
    site.events.append({"name": "CE-L", "kind": "tb_submitted", "source": "live",
                        "reference_doctype": "Trial Balance Submission",
                        "reference_name": "TB-X", "at": _dt(4, 12)})
    _call(site, "execute", capture=[])
    assert site.inserted
    assert all(str(e["at"]) < str(_dt(4, 12)) for e in site.inserted), site.inserted


def test_an_insert_that_raises_propagates():
    site = _Site()
    site.insert_raises = True
    with _raises(RuntimeError):
        _call(site, "execute", capture=[])


def test_execute_prints_counts_unplaced_and_not_recoverable():
    site = _Site()
    out = []
    _call(site, "execute", capture=out)
    model = _load_real("close_event_backfill_model")
    for sentence in model.NOT_RECOVERABLE:
        assert sentence in out[0]
    assert "tb_submitted" in out[0]


def test_dry_run_writes_nothing_and_matches_execute():
    site = _Site()
    dry = _call(site, "dry_run")
    assert site.inserted == []
    assert ("reload_doc", "consolidation", "doctype", "close_event") not in site.calls
    assert set(dry) == {"by_kind", "unplaced"}
    assert dry["by_kind"] == EXPECTED_KINDS
    assert dry["unplaced"] == {"no declared period (#305-W2-5)": 1}
    summary = _call(site, "execute", capture=[])
    assert summary["by_kind"] == dry["by_kind"]
    assert summary["unplaced"] == dry["unplaced"]


def test_patch_is_the_last_line_of_patches_txt():
    with open(PATCHES_TXT) as fh:
        lines = [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]
    assert lines[-1] == "konsol.patches.backfill_close_events"
