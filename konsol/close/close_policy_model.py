"""Close policy model, pure: the declared policies and their setup gaps
(konsol#305-D2-3, D2-9).

- ``self_approval``: whether the person who prepared a document in
  ``APPROVAL_DOCTYPES`` may approve it themselves. Blocked, or Allowed with
  reason. Blank ("") is undeclared.
- ``rate_move_threshold``: how far a group rate may move from the previous
  approved rate or an ERP quote before a Reason for Change is needed. It is a
  Close Settings Percent field (50 means 50%); a Frappe Percent reads back 0
  when unset, so 0 is undeclared. This is the same convention as the first
  close Ints (signoff_model.py:20-21).

Neither policy is ever guessed: an undeclared value is refused where it would
change behaviour, and reported as a setup gap, never defaulted.

Imports nothing from frappe or konsol.
"""
import json

BLOCKED = "Blocked"
ALLOWED_WITH_REASON = "Allowed with reason"
SELF_APPROVAL_POLICIES = (BLOCKED, ALLOWED_WITH_REASON)

# The doctypes whose submit is a Close Lead's approval of work someone else
# can prepare (konsol#305-D2-3). The Consolidation Journal replaced
# Consolidation Adjustment here in the row that made it submittable (J03).
APPROVAL_DOCTYPES = (
    "Consolidation Journal",
    "Business Combination",
    "Business Disposal",
    "Group Exchange Rate",
    "Ownership Period",
    "Historical Equity Rate",
    "IC Balance",
)
# Excluded, and why:
# - Trial Balance Submission: an Entity Accountant's own submit, not an
#   approval of someone else's work.
# - TB Exception: the Close Lead declares it and submits it in one call
#   (signoff_api.declare_tb_exception, signoff_api.py:322, insert then submit
#   at :341-342), so the owner is always the submitter.
# - Build Approval: not submittable; its "Approve" is a state change at
#   docstatus 0.
# - Budget Cycle: a budget doctype, outside the close.

# The roles approval_api.approve admits (approval_api.py:23). A submit by
# someone holding at least one of these is an approval, for the build
# auto-approve rule (konsol#305-D2-10). Administrator holds every role
# (frappe.get_roles), so an Administrator submit counts too; there is no
# exemption (accepted 27 Sep).
APPROVER_ROLES = ("EPM Admin", "System Manager")

SELF_APPROVAL_UNDECLARED = "self_approval_undeclared"
RATE_MOVE_UNDECLARED = "rate_move_undeclared"

_SELF_APPROVAL_MESSAGE = (
    "Declare the self-approval policy (Blocked, or Allowed with reason) in "
    "Close Settings (the Close Lead or System Manager)."
)
RATE_MOVE_MESSAGE = (
    "Declare the rate move threshold in Close Settings (the Close Lead or "
    "System Manager): a group rate that moves more than it from the previous "
    "approved rate or the ERP quote needs a Reason for Change."
)


def move_fraction(pct):
    """``pct`` (a Close Settings Percent, e.g. 50) as a fraction (0.5), or
    None when undeclared (``pct`` is None or 0)."""
    if not pct:
        return None
    return pct / 100.0


def settings_problems(self_approval, rate_move_threshold):
    """Problems that refuse a Close Settings save. Blank and 0 are allowed:
    that is undeclared, reported elsewhere as a gap and never defaulted."""
    problems = []
    if self_approval and self_approval not in SELF_APPROVAL_POLICIES:
        problems.append(
            "Unknown self-approval policy %r; expected blank, %s."
            % (self_approval, " or ".join(SELF_APPROVAL_POLICIES))
        )
    if rate_move_threshold and rate_move_threshold < 0:
        problems.append("The rate move threshold cannot be negative.")
    return problems


def policy_gaps(self_approval, rate_move_threshold):
    """Setup gaps blocking whatever needs these policies declared (sign-off,
    My work). Returns a list of ``{"code", "message"}`` in a fixed order:
    self-approval, then rate move. ``[]`` when both are declared."""
    gaps = []
    if not self_approval:
        gaps.append({"code": SELF_APPROVAL_UNDECLARED, "message": _SELF_APPROVAL_MESSAGE})
    if not rate_move_threshold:
        gaps.append({"code": RATE_MOVE_UNDECLARED, "message": RATE_MOVE_MESSAGE})
    return gaps


# konsol#305-W3-7 (C16): Close Settings ``intercompany_declaration``. Blank
# is undeclared (intercompany is expected, or not set up yet); the only
# declared value says this group has no intercompany. No default.
INTERCOMPANY_NONE = "None in this group"
INTERCOMPANY_DECLARATIONS = (INTERCOMPANY_NONE,)


def intercompany_declaration_problems(declaration, published):
    """Problems that refuse a Close Settings save of ``declaration``, given
    ``published``, the count of Published Intercompany Accounts (an int >= 0;
    anything else raises ValueError). Blank or None is undeclared: ``[]``."""
    if isinstance(published, bool) or not isinstance(published, int) or published < 0:
        raise ValueError(
            "The count of Published Intercompany Accounts must be an int >= 0, not %r."
            % (published,))
    if not declaration:
        return []
    if declaration not in INTERCOMPANY_DECLARATIONS:
        return ["Unknown intercompany declaration %r; expected blank or %s."
                % (declaration, " or ".join(INTERCOMPANY_DECLARATIONS))]
    if declaration == INTERCOMPANY_NONE and published > 0:
        return ["%d Intercompany Account(s) are Published, so this group has "
                "intercompany: make them Inactive before declaring none." % published]
    return []


_CHILD_ROW_KEYS = ("added", "removed", "row_changed")


def _version_data(version):
    data = version.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise ValueError(
                "The Version by %s holds data that is not JSON; it cannot be "
                "read to decide who prepared the document." % version.get("owner")
            )
    return data or {}


def _is_edit(data, state_field):
    """True when a Version's ``data`` is a draft edit (#305-W2-14, W2-P4):
    no ``docstatus`` change, and either a change to a field other than the
    workflow ``state_field`` or a child row added, removed or changed."""
    changed = data.get("changed") or []
    fields = [c[0] for c in changed]
    if "docstatus" in fields:
        return False
    if any(f != state_field for f in fields):
        return True
    return any(data.get(k) for k in _CHILD_ROW_KEYS)


def preparers(owner, versions, state_field=None):
    """Who prepared a document (konsol#305-W2-14, which changes D2-3): its
    ``owner``, plus the owner of every Version that edits the draft.

    ``versions`` are the document's Version rows as ``{"owner", "data"}``;
    ``data`` is a dict or the JSON text Frappe stores. ``state_field`` is the
    doctype's workflow state field (``status`` on the journal, Business
    Combination and Business Disposal workflows), or None without a workflow.

    Not edits: a submit (0->1), a cancel (1->2), an amendment's first save
    (2->0), and a change to the workflow state field alone (a Reject, a Send
    for Approval). Order does not matter: anyone other than the owner edited
    after the owner's insert. Returns a frozenset.
    """
    if not owner:
        raise ValueError("A document's preparers need its owner; the owner is blank.")
    result = {owner}
    for version in versions or ():
        if _is_edit(_version_data(version), state_field):
            result.add(version.get("owner"))
    return frozenset(result)


def submit_carries_edit(diff, state_field=None):
    """True when the submitting save itself edits the draft (#305-W2-14,
    review M1, konsol#305 R01a), so its submitter is a preparer.

    ``diff`` is Frappe's diff of the pending save
    (``frappe.core.doctype.version.version.get_diff``: the dict the Version
    written after the submit will hold), or None when nothing changed. Unlike
    ``_is_edit`` on a saved Version, the ``docstatus`` change is expected here
    and ignored, as is the workflow ``state_field`` (a workflow Approve sets
    it). Any other field changed, or any child row added, removed or changed,
    is an edit.
    """
    if not diff:
        return False
    for change in diff.get("changed") or ():
        if change[0] != "docstatus" and change[0] != state_field:
            return True
    return any(diff.get(k) for k in _CHILD_ROW_KEYS)


def self_approval_problem(policy, preparers, user, doctype, name, reason=None, exempt=None):
    """None, or the sentence that refuses ``user`` approving a document they
    prepared (``user`` is in ``preparers``, see ``preparers()``), under the
    declared ``self_approval`` ``policy`` (konsol#305-D2-3, R5, #305-W2-14).
    ``preparers`` is a set; a ``str`` raises TypeError, because membership in
    a string would match a substring. The rules, in order:

    1. ``user`` not in ``preparers``, or a non-empty ``exempt`` reason (the caller's own,
       e.g. "derived" for a Business Combination's Ownership Period, or
       "system" for a patch/install/migrate) — always passes.
    2. Undeclared policy (blank or None) refuses, naming the Close Settings
       gap (P02's ``SELF_APPROVAL_UNDECLARED`` message), prefixed with who
       prepared what.
    3. Blocked always refuses, whether or not a reason is supplied.
    4. Allowed with reason and a blank (or whitespace-only) reason refuses.
    5. Allowed with reason and a reason — passes.
    6. Any other value refuses (fail closed; P03b).

    This model does not read frappe flags; it only honours a non-empty
    ``exempt`` its caller already decided.
    """
    if isinstance(preparers, str):
        raise TypeError(
            "self_approval_problem: pass the set of preparers, not the string %r."
            % preparers
        )
    if user not in preparers or exempt:
        return None
    if not policy:
        return "%s prepared %s %s; %s" % (user, doctype, name, _SELF_APPROVAL_MESSAGE)
    if policy == BLOCKED:
        return (
            "Close Settings blocks self-approval: %s prepared %s %s, "
            "so another Close Lead must approve it." % (user, doctype, name)
        )
    if policy == ALLOWED_WITH_REASON:
        if not (reason or "").strip():
            return (
                "Close Settings allows self-approval only with a reason: "
                "approve %s %s through konsol.close.approval_api.approve "
                "with a reason, or ask another Close Lead to approve it." % (doctype, name)
            )
        return None
    return (
        "Close Settings holds an unknown self-approval policy %r; declare "
        "Blocked or Allowed with reason before %s %s can be self-approved."
        % (policy, doctype, name)
    )


def self_approval_note(policy, user, reason):
    """The Comment text left on a document self-approved under Close
    Settings (Allowed with reason)."""
    return "Self-approved by %s under Close Settings (%s): %s" % (user, policy, reason)


def approval_build_reason(doctype, name, method, user, roles):
    """The sentence recorded on an auto-approved Build Approval when the
    trigger that requested the build was itself an approval (konsol#305-D2-10),
    or None when the normal risk rules apply.

    "An approval" means all three:
      - ``method == "on_submit"``;
      - ``doctype`` is one of ``APPROVAL_DOCTYPES``, so it is someone
        approving work that could be someone else's (a Reverse/cancel is
        not; nor is a save of a doctype that is never submitted, such as
        Consolidation Group or IC Elimination Rule; nor Trial Balance
        Submission, an Entity Accountant's own submit, excluded above);
      - ``roles`` meets ``APPROVER_ROLES``: the roles ``approval_api.approve``
        admits, so an EPM Analyst's own submit never counts.

    Everything else returns None; the caller falls back to the normal risk
    rules.
    """
    if method != "on_submit":
        return None
    if doctype not in APPROVAL_DOCTYPES:
        return None
    if not set(roles or ()) & set(APPROVER_ROLES):
        return None
    return "Auto-approved: %s approved %s %s (konsol#305-D2-10)." % (user, doctype, name)
