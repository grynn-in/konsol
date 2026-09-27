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

BLOCKED = "Blocked"
ALLOWED_WITH_REASON = "Allowed with reason"
SELF_APPROVAL_POLICIES = (BLOCKED, ALLOWED_WITH_REASON)

# The doctypes whose submit is a Close Lead's approval of work someone else
# can prepare (konsol#305-D2-3). The Consolidation Journal (rows J01...) joins
# this list by adding one line when it replaces Consolidation Adjustment.
APPROVAL_DOCTYPES = (
    "Consolidation Adjustment",
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

SELF_APPROVAL_UNDECLARED = "self_approval_undeclared"
RATE_MOVE_UNDECLARED = "rate_move_undeclared"

_SELF_APPROVAL_MESSAGE = (
    "Declare the self-approval policy (Blocked, or Allowed with reason) in "
    "Close Settings (the Close Lead or System Manager)."
)
_RATE_MOVE_MESSAGE = (
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
        gaps.append({"code": RATE_MOVE_UNDECLARED, "message": _RATE_MOVE_MESSAGE})
    return gaps


def self_approval_problem(policy, owner, user, doctype, name, reason=None, exempt=None):
    """None, or the sentence that refuses ``user`` approving a document they
    prepared (``owner``), under the declared ``self_approval`` ``policy``
    (konsol#305-D2-3, R5). The rules, in order:

    1. ``user != owner``, or a non-empty ``exempt`` reason (the caller's own,
       e.g. "derived" for a Business Combination's Ownership Period, or
       "system" for a patch/install/migrate) — always passes.
    2. Undeclared policy (blank or None) refuses, naming the Close Settings
       gap (P02's ``SELF_APPROVAL_UNDECLARED`` message), prefixed with who
       prepared what.
    3. Blocked always refuses, whether or not a reason is supplied.
    4. Allowed with reason and a blank (or whitespace-only) reason refuses.
    5. Allowed with reason and a reason — passes.

    This model does not read frappe flags; it only honours a non-empty
    ``exempt`` its caller already decided.
    """
    if user != owner or exempt:
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
    return None


def self_approval_note(policy, user, reason):
    """The Comment text left on a document self-approved under Close
    Settings (Allowed with reason)."""
    return "Self-approved by %s under Close Settings (%s): %s" % (user, policy, reason)
