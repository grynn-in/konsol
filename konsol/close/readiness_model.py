"""Readiness model, pure: konsol/close/readiness_model.py (konsol#305 E201;
stories 2.1, W2-4, 2.3).

``readiness(period_row, problems, rates, run, allowed)`` turns the sign-off
gate's problems (``signoff_gate.sign_off_problems``), the period row, the
rate gate (``group_rates.rate_gate``) and the latest close run
(``assertion_run.latest_close_run``) into the readiness checklist shown in
the close app's readiness strip: ``{"ready": int, "total": int, "items":
[...]}. ``items`` is always in ``ITEMS`` order and always has ``len(ITEMS)``
entries — nothing is dropped and nothing is guessed. A ``config_gaps`` code
this module does not recognise lands under ``configuration`` rather than
being silently skipped.

An item is ``{"code", "state": "ok"|"blocked"|"unknown", "label", "detail",
"entities", "hidden"}``. ``entities`` (from the #289 ownership gap or the
completeness gap) is cut to ``allowed``: ``allowed=None`` means every entity
is visible, ``allowed=set()`` means none are, and ``hidden`` is the count of
entities the cut removed. A hidden entity's code never appears anywhere in
the result, including inside a message string, so a scoped caller's own
readiness never names an entity outside their scope.

Imports nothing from frappe or konsol; loaded by path in its test
(mirror test_close_policy_model.py:1-13).
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name):
    """Load a sibling konsol/close module by path (as tb_view_model.py does),
    so this module keeps loading without frappe or a package-relative import."""
    spec = importlib.util.spec_from_file_location(
        "konsol_close_readiness_model_" + name, os.path.join(_HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


signoff_model = _load_sibling("signoff_model")
close_policy_model = _load_sibling("close_policy_model")

OK = "ok"
BLOCKED = "blocked"
UNKNOWN = "unknown"

# Fixed order (P25.b-adjacent, W2-4): the readiness strip never reorders.
ITEMS = (
    "period_open",
    "first_close",
    "previous_signed",
    "policies",
    "configuration",
    "ownership",
    "trial_balances",
    "rates",
    "checks",
)

_LABELS = {
    "period_open": "Period open",
    "first_close": "First close declared",
    "previous_signed": "Previous periods signed off",
    "policies": "Policies declared",
    "configuration": "Configuration",
    "ownership": "Every trial balance has ownership",
    "trial_balances": "Trial balances",
    "rates": "Rates",
    "checks": "Checks",
}

# Gap codes an earlier item already reports. Anything left over — including a
# code this module has never seen — falls through to "configuration": the
# failure path for a dropped gap (E201 test list).
_HANDLED_ELSEWHERE = (
    signoff_model.FIRST_CLOSE_UNDECLARED,
    signoff_model.HISTORY_PERIOD,
    signoff_model.UNOWNED_TB,
    close_policy_model.SELF_APPROVAL_UNDECLARED,
    close_policy_model.RATE_MOVE_UNDECLARED,
)

_SIGNED_RUN_STATES = ("Signed Off", "Acknowledged", "Overridden")
_KNOWN_RUN_STATUSES = _SIGNED_RUN_STATES + ("Green", "Amber", "Red", "Error")


def _item(code, state, detail="", entities=(), hidden=0):
    return {
        "code": code,
        "state": state,
        "label": _LABELS[code],
        "detail": detail,
        "entities": list(entities),
        "hidden": hidden,
    }


def _cut(entities, allowed):
    """``entities`` sorted, restricted to ``allowed`` (None = everyone), and
    the count the cut removed. Never returns a code outside ``allowed``."""
    entities = sorted(set(entities or ()))
    if allowed is None:
        return entities, 0
    visible = [e for e in entities if e in allowed]
    return visible, len(entities) - len(visible)


_VOWELS = "aeiou"


def _default_plural(singular):
    """English pluralisation default, good enough for this module's nouns
    (currently only 'entity'): a consonant before a trailing 'y' turns the
    'y' into 'ies' (entity -> entities); anything else just appends 's'."""
    if len(singular) > 1 and singular[-1] == "y" and singular[-2].lower() not in _VOWELS:
        return singular[:-1] + "ies"
    return singular + "s"


def _noun(n, singular, plural=None):
    plural = plural or _default_plural(singular)
    return "%d %s" % (n, singular if n == 1 else plural)


def _hidden_detail(hidden):
    """E201b: an item whose pre-cut list is non-empty stays blocked even when
    the cut leaves nothing visible; this is its detail, and it never names
    the hidden entity."""
    return "%s you cannot see" % _noun(hidden, "entity")


_GAP_LABELS = {
    signoff_model.FREQUENCY_UNDECLARED: "Reporting frequency not set",
    signoff_model.QUARTER_UNDECLARED: "Quarter not declared",
    # #305 5.4: ic_balance_model.RULE_UNDECLARED (this module imports no
    # sibling but signoff_model/close_policy_model; the test feeds the real gap).
    "ic_unrealized_profit_rule_undeclared": "Unrealised-profit rule not declared",
    # F51b: ic_balance_model.RULE_AMBIGUOUS (same reason).
    "ic_unrealized_profit_rule_ambiguous": "More than one unrealised-profit rule per pair",
    # I53: ic_balance_model.DRAFT_PENDING (same reason).
    "ic_balance_draft_pending": "IC Balance draft awaiting approval",
}


def _scoped_gap_detail(gap, allowed):
    """A leftover configuration gap's detail text. A gap with no
    ``entities`` key, or an unrestricted caller (``allowed is None``), keeps
    its own message unchanged. Otherwise ``signoff_model``'s message can name
    up to 5 entities (E201b), so this writes its own wording: the gap code's
    plain label plus the visible and hidden counts, naming nobody."""
    if "entities" not in gap or allowed is None:
        return gap["message"]
    visible, hidden = _cut(gap["entities"], allowed)
    label = _GAP_LABELS.get(gap["code"], gap["code"])
    return "%s: %s, and %d you cannot see" % (label, _noun(len(visible), "entity"), hidden)


def _period_open_item(period_row):
    status = period_row.get("status")
    if status == "Open":
        return _item("period_open", OK, "%s is Open" % period_row.get("code"))
    return _item("period_open", BLOCKED, "%s is %s" % (period_row.get("code"), status))


def _first_close_item(by_code):
    gap = by_code.get(signoff_model.FIRST_CLOSE_UNDECLARED) or by_code.get(signoff_model.HISTORY_PERIOD)
    if gap:
        return _item("first_close", BLOCKED, gap["message"])
    return _item("first_close", OK, "The first close period is declared")


def _previous_signed_item(by_code, order):
    if signoff_model.FIRST_CLOSE_UNDECLARED in by_code:
        return _item("previous_signed", UNKNOWN, "Declare the first close period first")
    if order:
        return _item("previous_signed", BLOCKED, order["message"])
    return _item("previous_signed", OK, "Every prior period is signed off")


def _policies_item(by_code):
    gaps = [
        by_code[code]
        for code in (close_policy_model.SELF_APPROVAL_UNDECLARED, close_policy_model.RATE_MOVE_UNDECLARED)
        if code in by_code
    ]
    if gaps:
        return _item("policies", BLOCKED, " ".join(g["message"] for g in gaps))
    return _item("policies", OK, "Both close policies are declared")


_TERMINAL = (".", "!", "?")


def _sentences(parts):
    """R52u: one sentence per part, joined by a space. A part that is not
    the last and lacks terminal punctuation gets a full stop, so two gaps
    never run together ("…you cannot see Declare…"); one that already ends
    in terminal punctuation is never given a second. The last part is left
    as written, like every other item's detail."""
    out = []
    for i, part in enumerate(parts):
        part = part.rstrip()
        if i < len(parts) - 1 and not part.endswith(_TERMINAL):
            part += "."
        out.append(part)
    return " ".join(out)


def _configuration_item(config_gaps, allowed):
    leftover = [g for g in config_gaps if g["code"] not in _HANDLED_ELSEWHERE]
    if not leftover:
        return _item("configuration", OK, "Configuration is complete")
    entities = []
    for gap in leftover:
        entities.extend(gap.get("entities") or ())
    visible, hidden = _cut(entities, allowed)
    detail = _sentences([_scoped_gap_detail(gap, allowed) for gap in leftover])
    return _item("configuration", BLOCKED, detail, visible, hidden)


def _ownership_item(by_code, allowed):
    gap = by_code.get(signoff_model.UNOWNED_TB)
    if not gap:
        return _item("ownership", OK, "Every trial balance has ownership")
    visible, hidden = _cut(gap.get("entities"), allowed)
    if not visible:
        # E201b: the gate still blocks these entities; a hidden problem is
        # never read as "ok".
        return _item("ownership", BLOCKED, _hidden_detail(hidden), hidden=hidden)
    n = len(visible)
    noun = "entity has" if n == 1 else "entities have"
    detail = "%d %s no ownership for the period" % (n, noun)
    return _item("ownership", BLOCKED, detail, visible, hidden)


def _trial_balances_item(completeness, allowed):
    if not completeness:
        return _item("trial_balances", OK, "Every expected trial balance is in")
    visible, hidden = _cut(completeness.get("missing"), allowed)
    if not visible:
        # E201b: same rule as ownership above.
        return _item("trial_balances", BLOCKED, _hidden_detail(hidden), hidden=hidden)
    return _item("trial_balances", BLOCKED, "%d trial balances missing" % len(visible), visible, hidden)


def _rates_item(rates):
    missing, error, blockers = rates
    if error:
        return _item("rates", BLOCKED, "Rates cannot be checked: %s" % error)
    parts = ["%s → %s %s" % (f, t, rt) for f, t, rt in (missing or ())]
    parts.extend(blockers or ())
    if parts:
        return _item("rates", BLOCKED, "; ".join(parts))
    return _item("rates", OK, "Every required rate is approved")


def _checks_item(run):
    if run is None:
        return _item("checks", BLOCKED, "Checks not run")
    status = run.get("status")
    if status not in _KNOWN_RUN_STATUSES:
        raise ValueError("Unknown close run status %r; expected one of %s." % (status, ", ".join(_KNOWN_RUN_STATUSES)))
    if status in _SIGNED_RUN_STATES:
        return _item("checks", OK, "Signed off")
    if status == "Green":
        return _item("checks", OK, "Green")
    if status == "Amber":
        return _item("checks", OK, "warnings to acknowledge at sign-off")
    # Red or Error.
    n = int(run.get("failed") or 0) + int(run.get("errored") or 0)
    return _item("checks", BLOCKED, "%d failing" % n)


def readiness(period_row, problems, rates, run, allowed):
    """``{"ready": int, "total": int, "items": [...]}`` for the readiness
    strip. See the module docstring for the argument shapes."""
    config_gaps = list(problems.get("config_gaps") or ())
    by_code = {g["code"]: g for g in config_gaps}
    order = problems.get("order")
    completeness = problems.get("completeness")

    items = [
        _period_open_item(period_row),
        _first_close_item(by_code),
        _previous_signed_item(by_code, order),
        _policies_item(by_code),
        _configuration_item(config_gaps, allowed),
        _ownership_item(by_code, allowed),
        _trial_balances_item(completeness, allowed),
        _rates_item(rates),
        _checks_item(run),
    ]
    ready = sum(1 for item in items if item["state"] == OK)
    return {"ready": ready, "total": len(ITEMS), "items": items}
