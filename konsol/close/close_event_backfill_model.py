"""Close Event backfill, pure (konsol#305 T06a, #298 story 10.1, #305-W2-1).

Turns plain rows that the backfill patch (T06b) reads -- Versions, Comments,
sign-off fields, journal fields -- into backfill Close Events. Drops what the
log already holds, and counts, under a named reason, everything it cannot
place. It decides nothing about the database: every input is a plain dict.

Decisions applied:
- #305-W2-7 (Deepak Pai, 2 Oct 2026): a historic self-approval is written
  honestly, as ``self_approved`` with ``detail.reason_not_recorded = True``
  when no self-approval Comment holds its reason. Rejected: backfilling only
  TB and sign-off events.
- #305-W2-14: the preparers are the owner plus everyone who edited the draft
  before the submit (``close_policy_model.preparers``), and the kind is
  ``close_event_model.approval_kind(preparers, approver)``.
- #305-W2-8: a 1->2 Version of an approval document is ``approval_cancelled``.
  The 2->0 Version (an amendment's first save) is not an event.
- #305-W2-5: placement is the patch's job (``close_event.period_of``); this
  model takes it in ``placements`` and counts a None under
  ``UNPLACED_NO_PERIOD``.
- T06c (found by T06b, 2 Oct): a sign-off or Re-sign Needed mark recorded
  only in the Versions of an Assertion Run that no longer exists is counted
  under ``UNPLACED_DELETED``, never dropped silently. The patch passes those
  Versions in as ``orphan_versions``; the model never sees the deleted run
  itself (there is none).
- konsol#305 R01f: an Ownership Period's 0->1 Version is backfilled as
  ``approved`` with ``detail.exempt == "derived"``, never ``self_approved``,
  when its docname is in ``derived_ownership_periods`` -- matching the live
  writer's named exemption (self_approval.py:111-115) for an Ownership
  Period a Business Combination submitted under
  ``frappe.flags.from_business_combination``
  (business_combination.py:426-465). The patch feeds the set from the
  Business Combination's own stored link (``ownership_period``); nothing is
  guessed from the Ownership Period side, which carries no such field.

Inputs (all plain):
- a Version: ``{name, ref_doctype, docname, owner, creation, data}``; ``data``
  is the JSON text Frappe stores (indented) or a dict.
- ``docs[(doctype, name)]``: ``{owner, entity?, data_area_id?,
  uploaded_on_behalf?, amended_from?, reason?}``; for an EPM Fiscal Year also
  ``fiscal_year`` and ``closing_note``. A missing key means the document was
  deleted.
- ``periods[row_name]``: ``(fiscal_year, fiscal_period, period_code)`` of an
  EPM Fiscal Year period row (the code matches the closing-note subject).
- ``placements[(doctype, name)]``: ``(fiscal_year, fiscal_period)``; None
  (no declared period) or a string (another reason) for an unplaced one.
- ``state_fields``: ``{doctype: workflow state field}``. A doctype absent
  from it has no active workflow, so every field change is an edit.

Loads ``close_event_model`` and ``close_policy_model`` by sibling path, so it
imports nothing from frappe or konsol.
"""
import datetime
import importlib.util
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name):
    spec = importlib.util.spec_from_file_location(
        "close_event_backfill_" + name, os.path.join(_HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


close_event_model = _sibling("close_event_model")
close_policy_model = _sibling("close_policy_model")

BACKFILL = close_event_model.BACKFILL

UNPLACED_NO_PERIOD = "no declared period (#305-W2-5)"
UNPLACED_ROW_GONE = "period row no longer exists"
UNPLACED_DELETED = "document deleted"

#: What the old records cannot give back (E10-P4). The patch prints these.
NOT_RECOVERABLE = (
    "Period and year status changes made before EPM Fiscal Year tracked changes "
    "(14 Sep 2026, 8340df3), or made by db.set_value, left no Version and are not in the trail.",
    "The reason for a close or lock with no closing-note line is not recorded "
    "(a note is optional there).",
    "The on-behalf flag of trial balances from before the field existed is blank.",
    "The signed state before a Re-sign Needed mark, and who made the mark and when, "
    "are unknown where no Version holds them: the events carry \"unknown\" and "
    "reason_not_recorded, and the void is dated at the signature.",
    "Approvals of a doctype with no tracked Version are not in the trail.",
)

_TB = "Trial Balance Submission"
_TBE = "TB Exception"
_FY = "EPM Fiscal Year"
_JOURNAL = "Consolidation Journal"
_RUN = "Assertion Run"
_APPROVAL_KINDS = ("approved", "self_approved")
_STATUS_VERB = {"Closed": "closed", "Locked": "locked", "Open": "reopened"}
_SIGNED_STATES = ("Signed Off", "Acknowledged", "Overridden")
_RE_SIGN_NEEDED = "Re-sign Needed"
_UNKNOWN = "unknown"

_SELF_APPROVAL_RE = re.compile(
    r"^Self-approved by (?P<user>.+?) under Close Settings \((?P<policy>[^)]*)\): (?P<reason>.*)$",
    re.DOTALL)
_REJECTED_RE = re.compile(r"^Rejected: (?P<reason>.*)$", re.DOTALL)
_NOTE_LINE_RE = re.compile(
    r"^(?P<subject>\S+) (?P<verb>closed|locked|reopened) on (?P<date>\d{4}-\d{2}-\d{2}) "
    r"by (?P<user>\S+?): (?P<text>.*)$")


# --- helpers -----------------------------------------------------------

def _at(value):
    """A time as a comparable string ("YYYY-MM-DD HH:MM:SS[.ffffff]")."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value.isoformat()
    return str(value).replace("T", " ", 1)


def _data(version):
    data = version.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise ValueError(
                "Version %s (%s %s) holds data that is not JSON; it cannot be backfilled."
                % (version.get("name"), version.get("ref_doctype"), version.get("docname")))
    if data is not None and not isinstance(data, dict):
        raise ValueError("Version %s holds data that is not a JSON object." % version.get("name"))
    return data or {}


def _changed(data, field):
    """``(old, new)`` of ``field`` in a Version's ``changed``, or None."""
    for entry in data.get("changed") or ():
        if entry and entry[0] == field:
            return entry[1], entry[2]
    return None


def _docstatus(data):
    pair = _changed(data, "docstatus")
    return (int(pair[0] or 0), int(pair[1] or 0)) if pair else None


def _count(unplaced, reason):
    unplaced[reason] = unplaced.get(reason, 0) + 1


def _placement(placements, key):
    """``((fy, fp), None)`` or ``(None, reason)``. A key the patch did not
    place at all is a caller bug and raises."""
    if key not in placements:
        raise ValueError("No placement was given for %s %s." % key)
    value = placements[key]
    if value is None:
        return None, UNPLACED_NO_PERIOD
    if isinstance(value, str):
        return None, value
    return (int(value[0]), int(value[1])), None


def _event(kind, period, actor, at, reference=None, reason=None, detail=None, entity=None):
    return {
        "kind": kind,
        "fiscal_year": period[0],
        "fiscal_period": period[1],
        "entity": entity or None,
        "reference_doctype": reference[0] if reference else None,
        "reference_name": reference[1] if reference else None,
        "actor": actor,
        "at": at,
        "reason": (reason or "").strip() or None,
        "detail": detail or None,
        "source": BACKFILL,
    }


def _sorted(events):
    return sorted(events, key=lambda e: _at(e["at"]))


def _merge(into, other):
    for reason, n in other.items():
        into[reason] = into.get(reason, 0) + n


# --- comments --------------------------------------------------------------

def reasons_from_comments(comments):
    """``{(ref_doctype, ref_name, owner): [(creation, reason), ...]}`` from
    the self-approval Comments (``close_policy_model.self_approval_note``),
    in creation order. Other Comments are ignored."""
    reasons = {}
    for c in sorted(comments or (), key=lambda c: _at(c.get("creation"))):
        match = _SELF_APPROVAL_RE.match((c.get("content") or "").strip())
        if match:
            key = (c.get("reference_doctype"), c.get("reference_name"), c.get("owner"))
            reasons.setdefault(key, []).append((c.get("creation"), match.group("reason").strip()))
    return reasons


def events_from_rejections(comments, docs, placements):
    """``(events, unplaced)``: each "Rejected: <reason>" Comment
    (approval_api.reject) as a ``rejected`` event by its owner, at its time."""
    events, unplaced = [], {}
    for c in comments or ():
        match = _REJECTED_RE.match((c.get("content") or "").strip())
        if not match:
            continue
        key = (c.get("reference_doctype"), c.get("reference_name"))
        doc = docs.get(key)
        if doc is None:
            _count(unplaced, UNPLACED_DELETED)
            continue
        period, why = _placement(placements, key)
        if period is None:
            _count(unplaced, why)
            continue
        events.append(_event(
            "rejected", period, c.get("owner"), c.get("creation"), key,
            reason=match.group("reason"), entity=doc.get("entity"),
            detail={"preparer": doc.get("owner")}))
    return _sorted(events), unplaced


def _reason_for(reasons, key, at):
    """The self-approval reason the approver left on the document: the
    latest Comment at or before the submit's Version, else the first after."""
    found = (reasons or {}).get(key) or []
    before = [r for t, r in found if _at(t) <= _at(at)]
    if before:
        return before[-1]
    after = [r for t, r in found if _at(t) > _at(at)]
    return after[0] if after else None


# --- closing note ----------------------------------------------------------

def reasons_from_closing_note(note):
    """The lines ``"<subject> <verb> on <YYYY-MM-DD> by <user>: <text>"``
    (epm_fiscal_year._append_note) as ``(subject, verb, date, user, text)``."""
    lines = []
    for line in (note or "").splitlines():
        match = _NOTE_LINE_RE.match(line.strip())
        if match:
            lines.append(tuple(match.group(g) for g in ("subject", "verb", "date", "user", "text")))
    return lines


def _note_text(lines, used, subject, verb, date, user):
    for i, line in enumerate(lines):
        if i not in used and line[:4] == (subject, verb, date, user):
            used.add(i)
            return line[4]
    return None


# --- versions -----------------------------------------------------------

def events_from_versions(versions, docs, periods, placements, approval_doctypes,
                         state_fields=None, reasons=None, derived_ownership_periods=None):
    """``(events, unplaced)`` from Versions:

    - 0->1 of an approval document: ``approval_kind(preparers, version.owner)``,
      the preparers being the owner, the editors before that Version, and
      that Version's own owner too when its ``data`` also carries a field
      edit alongside the ``docstatus`` change
      (``close_policy_model.submit_carries_edit``, konsol#305 R01a/R01b: the
      backfilled rule for a 0->1 Version matches the live rule for a
      submitting request); 1->2: ``approval_cancelled``;
      an Ownership Period named in ``derived_ownership_periods`` is
      ``approved`` with ``detail.exempt == "derived"`` instead, whatever its
      preparers are (konsol#305 R01f, matching self_approval.py:111-115);
    - 0->1 / 1->2 of a Trial Balance Submission: ``tb_submitted`` /
      ``tb_cancelled``; of a TB Exception: ``tb_exception_declared`` /
      ``tb_exception_cancelled``;
    - an EPM Fiscal Year ``periods`` row's status change: ``period_<verb>``;
      the year's own: ``year_<verb>`` (period 0).

    ``reasons`` is ``reasons_from_comments``'s map. ``derived_ownership_periods``
    is a set/container of Ownership Period docnames (the patch's read of
    ``Business Combination.ownership_period``); absent or empty, nothing is
    exempted.
    """
    approval_doctypes = tuple(approval_doctypes)
    state_fields = state_fields or {}
    derived_ownership_periods = derived_ownership_periods or ()
    if isinstance(derived_ownership_periods, str):
        raise TypeError("derived_ownership_periods must be a set of docnames, not a string")
    parsed = [(v, _data(v)) for v in versions or ()]
    by_doc = {}
    for v, data in sorted(parsed, key=lambda p: _at(p[0].get("creation"))):
        by_doc.setdefault((v.get("ref_doctype"), v.get("docname")), []).append((v, data))

    events, unplaced = [], {}
    note_lines, note_used = {}, {}
    for key, rows in by_doc.items():
        doctype = key[0]
        for index, (v, data) in enumerate(rows):
            if doctype == _FY:
                _fiscal_year_events(v, data, docs.get(key), periods, events, unplaced,
                                    note_lines, note_used, key)
                continue
            pair = _docstatus(data)
            kind = _document_kind(doctype, pair, approval_doctypes)
            if kind is None:
                continue
            doc = docs.get(key)
            if doc is None:
                _count(unplaced, UNPLACED_DELETED)
                continue
            period, why = _placement(placements, key)
            if period is None:
                _count(unplaced, why)
                continue
            actor, at = v.get("owner"), v.get("creation")
            if kind == "approval":
                earlier = [{"owner": e.get("owner"), "data": d} for e, d in rows[:index]]
                preparers = close_policy_model.preparers(
                    doc.get("owner"), earlier, state_fields.get(doctype))
                if close_policy_model.submit_carries_edit(data, state_fields.get(doctype)):
                    preparers = preparers | {actor}
                exempt = ("derived" if doctype == "Ownership Period"
                          and key[1] in derived_ownership_periods else None)
                events.append(_approval_event(
                    key, doc, preparers, actor, at, period, reasons, exempt=exempt))
            elif kind == "approval_cancelled":
                events.append(_event(kind, period, actor, at, key, entity=doc.get("entity"),
                                     detail={"preparer": doc.get("owner")}))
            elif kind == "tb_submitted":
                events.append(_event(kind, period, actor, at, key, entity=doc.get("data_area_id"),
                                     detail={"on_behalf": doc.get("uploaded_on_behalf") or "",
                                             "replaces": doc.get("amended_from") or None}))
            elif kind == "tb_exception_declared":
                reason = (doc.get("reason") or "").strip()
                events.append(_event(kind, period, actor, at, key, reason=reason,
                                     entity=doc.get("data_area_id"),
                                     detail=None if reason else {"reason_not_recorded": True}))
            else:
                events.append(_event(kind, period, actor, at, key, entity=doc.get("data_area_id")))
    return _sorted(events), unplaced


def _document_kind(doctype, pair, approval_doctypes):
    if pair not in ((0, 1), (1, 2)):
        return None
    submit = pair == (0, 1)
    if doctype in approval_doctypes:
        return "approval" if submit else "approval_cancelled"
    if doctype == _TB:
        return "tb_submitted" if submit else "tb_cancelled"
    if doctype == _TBE:
        return "tb_exception_declared" if submit else "tb_exception_cancelled"
    return None


def _approval_event(key, doc, preparers, approver, at, period, reasons, exempt=None):
    """``exempt`` (konsol#305 R01f): the named exemption the live writer would
    have used (currently only ``"derived"``, self_approval.py:111-115). A set
    ``exempt`` forces ``kind == "approved"`` and carries ``detail.exempt``,
    whatever ``preparers``/``approver`` say -- the live hook never judges a
    self-approval under a named exemption, so the backfill records no reason
    and no ``reason_not_recorded`` flag either."""
    kind = "approved" if exempt else close_event_model.approval_kind(preparers, approver)
    detail = {"preparer": doc.get("owner"), "preparers": sorted(preparers)}
    if exempt:
        detail["exempt"] = exempt
    reason = None
    if kind == "self_approved":
        reason = _reason_for(reasons, (key[0], key[1], approver), at)
        if not reason:
            detail["reason_not_recorded"] = True
    return _event(kind, period, approver, at, key, reason=reason,
                  entity=doc.get("entity"), detail=detail)


def _verb(version, value):
    if value not in _STATUS_VERB:
        raise ValueError("Version %s sets an unknown fiscal status %r." % (version.get("name"), value))
    return _STATUS_VERB[value]


def _fiscal_year_events(v, data, doc, periods, events, unplaced, note_lines, note_used, key):
    year_pair = _changed(data, "status")
    row_changes = []
    for entry in data.get("row_changed") or ():
        if not entry or entry[0] != "periods":
            continue
        for field, old, new in entry[3] or ():
            if field == "status" and old != new:
                row_changes.append((entry[2], old, new))
    if year_pair is not None and year_pair[0] == year_pair[1]:
        year_pair = None
    if year_pair is None and not row_changes:
        return
    if doc is None:
        # One count per event the Version would have given.
        for _ in range(len(row_changes) + (year_pair is not None)):
            _count(unplaced, UNPLACED_DELETED)
        return
    if key not in note_lines:
        note_lines[key] = reasons_from_closing_note(doc.get("closing_note"))
        note_used[key] = set()
    lines, used = note_lines[key], note_used[key]
    fiscal_year = int(doc.get("fiscal_year"))
    actor, at = v.get("owner"), v.get("creation")
    date = _at(at)[:10]

    year_text = None
    if year_pair is not None:
        verb = _verb(v, year_pair[1])
        year_text = _note_text(lines, used, "FY%d" % fiscal_year, verb, date, actor)
        moved = [periods[r][2] for r, _, _ in row_changes if r in periods]
        events.append(_event(
            "year_" + verb, (fiscal_year, 0), actor, at, key, reason=year_text,
            detail=_with_flag({"from": year_pair[0], "periods_moved": moved},
                              verb, year_text)))
    for row_name, old, new in row_changes:
        if row_name not in periods:
            _count(unplaced, UNPLACED_ROW_GONE)
            continue
        fy, fp, code = periods[row_name]
        verb = _verb(v, new)
        detail = {"period_code": code, "from": old}
        if year_pair is not None:
            detail["via"] = "year"
            text = year_text
        else:
            text = _note_text(lines, used, code, verb, date, actor)
        events.append(_event("period_" + verb, (int(fy), int(fp)), actor, at, key,
                             reason=text, detail=_with_flag(detail, verb, text)))


def _with_flag(detail, verb, text):
    """A reopen needs a reason; one the note does not hold is flagged. A
    close or lock without a note has none, and that is not a gap."""
    if verb == "reopened" and not text:
        detail["reason_not_recorded"] = True
    return detail


# --- sign-offs -------------------------------------------------------------

def events_from_signoffs(runs, orphan_versions=None):
    """``(events, unplaced)`` from Assertion Runs with ``signed_off_by``:
    ``signed_off`` for each, and ``signoff_voided`` for one now "Re-sign
    Needed".

    A run is ``{name, fiscal_year, fiscal_period, signed_off_by,
    signed_off_at, signoff_status, status, override_reason, acknowledgement,
    affected_by, versions}``; ``versions`` are the run's own Versions
    (``{name, owner, creation, data}``), which may be empty.

    ``orphan_versions`` (T06c) are Assertion Run Versions whose run no
    longer exists: the patch cannot attach them to any run, so this model
    never builds an event from them. Each one whose ``signoff_status``
    Version entry moves into a signed state or into "Re-sign Needed" is
    counted under ``UNPLACED_DELETED`` instead of being dropped without a
    count. A transition into any other state (for example away from a
    signed state) is not a sign-off or a void, so it is not counted.
    """
    events = []
    for run in runs or ():
        if not run.get("signed_off_by"):
            continue
        ref = (_RUN, run.get("name"))
        period = (int(run["fiscal_year"]), int(run["fiscal_period"]))
        reason = (run.get("override_reason") or run.get("acknowledgement") or "").strip() or None
        state = run.get("signoff_status")
        mark = None
        if state not in _SIGNED_STATES:
            mark = _resign_mark(run)
            state = mark[0] if mark else _UNKNOWN
        detail = {"signoff_status": state, "run_status": run.get("status")}
        if state == _UNKNOWN or (state in close_event_model.SIGNOFF_NEEDS_REASON and not reason):
            detail["reason_not_recorded"] = True
        events.append(_event("signed_off", period, run["signed_off_by"], run.get("signed_off_at"),
                             ref, reason=reason, detail=detail))
        if run.get("signoff_status") == _RE_SIGN_NEEDED:
            voided = (run.get("affected_by") or "").strip()
            vdetail = {}
            if mark:
                actor, at = mark[1], mark[2]
            else:
                actor, at = _UNKNOWN, run.get("signed_off_at")
                vdetail.update(actor_not_recorded=True, at_not_recorded=True)
            if not voided:
                vdetail["reason_not_recorded"] = True
            events.append(_event("signoff_voided", period, actor, at, ref, reason=voided,
                                 detail=vdetail or None))
    unplaced = {}
    for v in orphan_versions or ():
        pair = _changed(_data(v), "signoff_status")
        if pair and (pair[1] in _SIGNED_STATES or pair[1] == _RE_SIGN_NEEDED):
            _count(unplaced, UNPLACED_DELETED)
    return _sorted(events), unplaced


def _resign_mark(run):
    """``(signed state before, actor, at)`` from the run's last Version
    that moved it from a signed state to Re-sign Needed, or None."""
    found = None
    for v in sorted(run.get("versions") or (), key=lambda v: _at(v.get("creation"))):
        pair = _changed(_data(v), "signoff_status")
        if pair and pair[1] == _RE_SIGN_NEEDED and pair[0] in _SIGNED_STATES:
            found = (pair[0], v.get("owner"), v.get("creation"))
    return found


# --- journals -------------------------------------------------------------

def events_from_journals(journals, existing, reasons=None):
    """Approval events from submitted Consolidation Journals' ``approved_by``
    and ``approved_at``, for a journal whose approval no Version already gave.

    ``existing`` is the events produced so far. A journal is skipped when any
    approval event (``approved`` or ``self_approved``) already names it, so
    one approval is never written twice under two kinds.
    """
    done = {(e.get("reference_doctype"), e.get("reference_name"))
            for e in existing or () if e.get("kind") in _APPROVAL_KINDS}
    events = []
    for j in journals or ():
        if not j.get("approved_by") or (_JOURNAL, j.get("name")) in done:
            continue
        key = (_JOURNAL, j.get("name"))
        preparers = close_policy_model.preparers(j.get("owner"), [])
        events.append(_approval_event(
            key, {"owner": j.get("owner")}, preparers, j["approved_by"], j.get("approved_at"),
            (int(j["fiscal_year"]), int(j["fiscal_period"])), reasons))
    return _sorted(events)


# --- dedupe and the whole plan ---------------------------------------------

def event_key(event):
    """The identity an event is matched on for an idempotent re-run."""
    return (event.get("kind"), event.get("reference_doctype"), event.get("reference_name"),
            _at(event.get("at")))


def new_events(candidates, existing, cutoff):
    """The candidates to insert, sorted by ``at``: those whose ``event_key``
    is not in ``existing`` (keys of the Close Events already written; ``at``
    may be a datetime or a string), and earlier than ``cutoff`` (the earliest
    live event's ``at``, or None). From the cutoff on, live events are the
    record."""
    seen = {(k[0], k[1], k[2], _at(k[3])) for k in existing or ()}
    limit = _at(cutoff) if cutoff is not None else None
    kept = []
    for event in _sorted(candidates or ()):
        key = event_key(event)
        if key in seen or (limit is not None and key[3] >= limit):
            continue
        seen.add(key)
        kept.append(event)
    return kept


def backfill(versions, docs, periods, placements, comments, runs, journals, existing,
             cutoff, approval_doctypes, state_fields=None, orphan_run_versions=None,
             derived_ownership_periods=None):
    """``(events, unplaced)``: every source above, then ``new_events``."""
    reasons = reasons_from_comments(comments)
    unplaced = {}
    from_versions, u = events_from_versions(
        versions, docs, periods, placements, approval_doctypes, state_fields, reasons,
        derived_ownership_periods)
    _merge(unplaced, u)
    rejected, u = events_from_rejections(comments, docs, placements)
    _merge(unplaced, u)
    signoffs, u = events_from_signoffs(runs, orphan_run_versions)
    _merge(unplaced, u)
    candidates = (from_versions + rejected + signoffs
                  + events_from_journals(journals, from_versions, reasons))
    return new_events(candidates, existing, cutoff), unplaced
