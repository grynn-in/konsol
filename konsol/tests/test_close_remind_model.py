"""konsol#305 story 1.5 (#305-1.5-1): remind_model, pure.

``konsol/close/remind_model.py`` is pure: loaded by path, no frappe or konsol
import. It holds the Remind rules that the endpoint (Y54) and every reader
(Y56, Y57, Y59, Y60) use.

Decisions pinned here:
- C-R1: topics are the closed set ``tb`` / ``ic``.
- C-R2 + #305-Q2-1 (Deepak Pai, 7 Oct 2026): recipients are enabled users,
  other than Administrator and Guest, with a User Permission DIRECTLY on the
  entity. Rejected: Q2-2, users permitted on parent (ancestor) nodes.
- C-R4: the refusals, checked in a fixed order.
- C-R5: the fixed subject text.
- C-R6: "count and last" per (fy, fp, entity, topic); the latest is chosen by
  comparing datetimes, never text (review-w5 S13).
"""
import ast
import importlib.util
import os
from datetime import datetime, timedelta, timezone

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PY = os.path.join(APP_DIR, "close", "remind_model.py")

_spec = importlib.util.spec_from_file_location("remind_model_under_test", MODEL_PY)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

PERIOD = "FY2025 P07"
NO_ONE = ("No one is named for ZZ01: a System Manager gives a user a User Permission "
          "on Entity ZZ01 before it can be reminded.")


def _raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type as e:
        return e
    raise AssertionError("%s not raised" % exc_type.__name__)


def _perm(user, for_value="ZZ01", allow="Entity"):
    return {"user": user, "allow": allow, "for_value": for_value}


def _refusal(entity="ZZ01", topic="tb", status="Open", allowed=None, recipients=("a@x",),
             tb_in=False, period_text=PERIOD):
    return M.refusal(entity, topic, status, allowed, list(recipients), tb_in, period_text)


# --- purity -----------------------------------------------------------------

def test_module_imports_no_frappe():
    with open(MODEL_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in ("frappe", "konsol"), alias.name
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol"), node.module


def test_constants():
    assert M.TOPICS == ("tb", "ic")
    assert M.TOPIC_LABEL == {"tb": "Trial balance", "ic": "Intercompany"}
    assert M.REMIND_ROLES == ("EPM Admin", "EPM Analyst", "System Manager")


# --- recipients (C-R2, #305-Q2-1) ---------------------------------------------

def test_recipients_direct_enabled_sorted_once():
    perms = [
        _perm("zoe@x"),
        _perm("amy@x"),
        _perm("amy@x"),                       # duplicate row: listed once
        _perm("off@x"),                       # disabled
        _perm("Administrator"),
        _perm("Guest"),
        _perm("parent@x", for_value="ZZ-GROUP"),  # ancestor-only: excluded (Q2-1)
        _perm("other@x", for_value="ZZ02"),   # another entity
        _perm("comp@x", allow="Company", for_value="ZZ01"),  # not an Entity permission
    ]
    users = {"zoe@x": {"enabled": 1}, "amy@x": {"enabled": 1}, "off@x": {"enabled": 0},
             "Administrator": {"enabled": 1}, "Guest": {"enabled": 1},
             "parent@x": {"enabled": 1}, "other@x": {"enabled": 1}, "comp@x": {"enabled": 1}}
    assert M.recipients("ZZ01", perms, users) == ["amy@x", "zoe@x"]


def test_recipients_ancestor_only_is_empty():
    # pin #305-Q2-1: a permission on the parent node does not make a recipient.
    perms = [_perm("parent@x", for_value="ZZ-GROUP")]
    assert M.recipients("ZZ01", perms, {"parent@x": {"enabled": 1}}) == []


def test_recipients_unknown_user_raises():
    # never guess whether a user is enabled
    e = _raises(ValueError, M.recipients, "ZZ01", [_perm("ghost@x")], {})
    assert "ghost@x" in str(e)


# --- refusal (C-R4), in order -------------------------------------------------

def test_refusal_none_when_all_clear():
    assert _refusal() is None
    assert _refusal(topic="ic", tb_in=True) is None
    assert _refusal(allowed={"ZZ01"}) is None


def test_refusal_1_unknown_topic_first():
    msg = "Remind about a trial balance (tb) or an intercompany difference (ic)."
    assert _refusal(topic="x", status="Closed", allowed=set(), recipients=(), tb_in=True) == msg
    assert _refusal(topic=None) == msg


def test_refusal_2_period_not_open():
    assert (_refusal(status="Closed", allowed=set(), recipients=(), tb_in=True)
            == "FY2025 P07 is Closed: remind only in an Open period.")
    assert (_refusal(status="Locked", period_text="FY2025 P08")
            == "FY2025 P08 is Locked: remind only in an Open period.")


def test_refusal_3_caller_cannot_see_entity():
    msg = _refusal(allowed={"ZZ02"}, recipients=(), tb_in=True)
    assert msg is not None and "ZZ01" in msg
    assert "trial balance is already in" not in msg
    assert msg != NO_ONE


def test_refusal_4_tb_already_in():
    assert (_refusal(tb_in=True, recipients=())
            == "ZZ01's FY2025 P07 trial balance is already in: nothing to remind.")


def test_refusal_5_no_recipients():
    assert _refusal(recipients=()) == NO_ONE
    assert _refusal(topic="ic", recipients=()) == NO_ONE


# --- subject (C-R5) and link -------------------------------------------------

def test_subject_texts():
    assert (M.subject("Jane Doe", "ZZ01", "tb", PERIOD)
            == "Reminder from Jane Doe: ZZ01's FY2025 P07 trial balance is still missing")
    assert (M.subject("Jane Doe", "ZZ01", "ic", PERIOD)
            == "Reminder from Jane Doe: ZZ01's FY2025 P07 intercompany difference needs attention")


def test_subject_unknown_topic_raises():
    _raises(ValueError, M.subject, "Jane Doe", "ZZ01", "x", PERIOD)


def test_link():
    assert M.link(2025, 7, "tb") == "/close/2025/7/trial-balances"
    assert M.link(2025, 7, "ic") == "/close/2025/7/intercompany"
    _raises(ValueError, M.link, 2025, 7, "x")


# --- summary (C-R6) ----------------------------------------------------------

def _ev(name, at, topic="tb", entity="ZZ01", actor="jane@x", fy=2025, fp=7, detail=None):
    return {"name": name, "fiscal_year": fy, "fiscal_period": fp, "entity": entity,
            "actor": actor, "at": at,
            "detail": {"topic": topic} if detail is None else detail}


def test_summary_counts_and_latest_per_key():
    t0 = datetime(2025, 8, 1, 9, 0)
    events = [
        _ev("CE-1", t0, actor="a@x"),
        _ev("CE-2", t0 + timedelta(hours=2), actor="b@x"),
        _ev("CE-3", t0 + timedelta(hours=1), actor="c@x"),
        _ev("CE-4", t0, topic="ic"),
        _ev("CE-5", t0, fp=8),
    ]
    out = M.summary(events)
    assert out[(2025, 7, "ZZ01", "tb")] == {"count": 3, "last_at": t0 + timedelta(hours=2),
                                            "last_by": "b@x"}
    assert out[(2025, 7, "ZZ01", "ic")]["count"] == 1
    assert out[(2025, 8, "ZZ01", "tb")]["count"] == 1
    assert len(out) == 3
    assert M.summary([]) == {}


def test_summary_latest_by_datetime_across_dst():
    # London, 26 Oct 2025: 01:30 BST (+01:00) then, one hour later, 01:30 GMT (+00:00).
    # As text the first sorts last; as datetimes the second is later.
    first = datetime(2025, 10, 26, 1, 30, tzinfo=timezone(timedelta(hours=1)))
    second = datetime(2025, 10, 26, 1, 30, tzinfo=timezone.utc)
    assert str(first) > str(second)
    for order in ([first, second], [second, first]):
        events = [_ev("CE-%d" % i, at, actor="early@x" if at is first else "late@x")
                  for i, at in enumerate(order)]
        entry = M.summary(events)[(2025, 7, "ZZ01", "tb")]
        assert entry["last_by"] == "late@x"
        assert entry["last_at"] is second


def test_summary_latest_across_dst_fold_same_zone():
    try:
        from zoneinfo import ZoneInfo
        london = ZoneInfo("Europe/London")
    except Exception:  # no tz data on this host: the fixed-offset test above covers it
        return
    first = datetime(2025, 10, 26, 1, 30, tzinfo=london, fold=0)   # BST
    second = datetime(2025, 10, 26, 1, 30, tzinfo=london, fold=1)  # GMT, an hour later
    for order in ([first, second], [second, first]):
        events = [_ev("CE-%d" % i, at, actor="early@x" if at is first else "late@x")
                  for i, at in enumerate(order)]
        assert M.summary(events)[(2025, 7, "ZZ01", "tb")]["last_by"] == "late@x"


def test_summary_unknown_topic_raises_naming_event():
    e = _raises(ValueError, M.summary, [_ev("CE-9", datetime(2025, 8, 1), topic="x")])
    assert "CE-9" in str(e)


def test_summary_missing_topic_raises_naming_event():
    e = _raises(ValueError, M.summary, [_ev("CE-8", datetime(2025, 8, 1), detail={})])
    assert "CE-8" in str(e)


def test_summary_at_not_datetime_raises():
    e = _raises(ValueError, M.summary, [_ev("CE-7", "2025-08-01 09:00:00")])
    assert "CE-7" in str(e)


# --- reminded_text (C-R6) ----------------------------------------------------

def test_reminded_text():
    at = datetime(2025, 10, 6, 14, 5)
    entry = {"count": 2, "last_at": at, "last_by": "jane@x"}
    text = M.reminded_text(entry, {"jane@x": "Jane Doe"}.get,
                           lambda d: d.strftime("%-d %b %H:%M"))
    assert text == "Reminded 2× · last 6 Oct 14:05 by Jane Doe"
    assert entry["last_at"] is at


def test_reminded_text_sender_without_a_name_is_labelled_not_raised():
    # review S3 failure path: a renamed or deleted sender (Close Event.actor is a
    # Data field) used to raise and take down every reader. It is now a label.
    at = datetime(2025, 10, 6, 14, 5)
    fmt = lambda d: d.strftime("%-d %b %H:%M")
    for names in ({}, {"ghost@x": ""}, {"ghost@x": None}):
        entry = {"count": 1, "last_at": at, "last_by": "ghost@x"}
        text = M.reminded_text(entry, names.get, fmt)
        assert text == "Reminded 1× · last 6 Oct 14:05 by ghost@x (name not recorded)", text


# --- sender_name (review S3) ---------------------------------------------------

def test_sender_name_full_name():
    assert M.sender_name("jane@x", {"jane@x": "Jane Doe"}) == "Jane Doe"


def test_sender_name_missing_or_blank_is_labelled_never_blank():
    for names in ({}, {"u@x": ""}, {"u@x": None}, {"u@x": "   "}, {"other@x": "Other"}):
        out = M.sender_name("u@x", names)
        assert out == "u@x (name not recorded)", (names, out)


# --- visible_entries (review S3: the scoping loop copied into four readers) ----

def _two_periods_two_topics_three_entities():
    t0 = datetime(2025, 8, 1, 9, 0)
    events = []
    i = 0
    for fp in (7, 8):
        for topic in ("tb", "ic"):
            for entity in ("ZZ01", "ZZ02", "ZZ03"):
                i += 1
                events.append(_ev("CE-%d" % i, t0 + timedelta(minutes=i), topic=topic,
                                  entity=entity, actor="%s-%s@x" % (entity, topic), fp=fp))
    # ZZ03 has no tb reminder in P08: only ic.
    events = [e for e in events if not (e["fiscal_period"] == 8 and e["entity"] == "ZZ03"
                                        and e["detail"]["topic"] == "tb")]
    return M.summary(events)


def test_visible_entries_keeps_only_the_topic_and_visible_entities():
    summ = _two_periods_two_topics_three_entities()
    out = M.visible_entries(summ, [(2025, 7), (2025, 8)], "tb", {"ZZ01"})
    assert set(out) == {(2025, 7), (2025, 8)}
    for key in out:
        assert set(out[key]) == {"ZZ01"}, out[key]
        assert out[key]["ZZ01"] is summ[(key[0], key[1], "ZZ01", "tb")]
    assert out[(2025, 7)]["ZZ01"]["last_by"] == "ZZ01-tb@x"


def test_visible_entries_every_key_present_even_when_empty():
    summ = _two_periods_two_topics_three_entities()
    out = M.visible_entries(summ, [(2025, 7), (2025, 9)], "tb", {"ZZ01"})
    assert out[(2025, 9)] == {}
    assert set(out) == {(2025, 7), (2025, 9)}  # P08 was not asked for
    assert M.visible_entries({}, [(2025, 7)], "ic", None) == {(2025, 7): {}}
    assert M.visible_entries(summ, [], "tb", None) == {}


def test_visible_entries_none_is_unrestricted_and_empty_set_hides_all():
    summ = _two_periods_two_topics_three_entities()
    out = M.visible_entries(summ, [(2025, 8)], "tb", None)
    assert set(out[(2025, 8)]) == {"ZZ01", "ZZ02"}  # ZZ03 has no tb in P08
    out = M.visible_entries(summ, [(2025, 8)], "ic", None)
    assert set(out[(2025, 8)]) == {"ZZ01", "ZZ02", "ZZ03"}
    assert out[(2025, 8)]["ZZ03"]["last_by"] == "ZZ03-ic@x"
    out = M.visible_entries(summ, [(2025, 7), (2025, 8)], "tb", set())
    assert out == {(2025, 7): {}, (2025, 8): {}}


def test_visible_entries_unknown_topic_raises():
    summ = _two_periods_two_topics_three_entities()
    _raises(ValueError, M.visible_entries, summ, [(2025, 7)], "x", None)
