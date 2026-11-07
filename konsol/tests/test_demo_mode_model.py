"""Demo mode rule, pure: konsol/demo_mode_model.py (demo mode D1).

`decide(demo_user, user_row, roles)` says whether a visitor may be logged in
as the demo user, and why not. Loaded by path; the module imports nothing
from frappe or konsol.
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "demo_mode_model.py")
_spec = importlib.util.spec_from_file_location("demo_mode_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

DEMO = "demo@example.com"
DEMO_ROLES = ["EPM Analyst", "Entity Accountant", "Desk User"]


def _user(**over):
    row = {"name": DEMO, "enabled": 1, "user_type": "System User"}
    row.update(over)
    return row


# ---- the module is pure -----------------------------------------------------

def test_module_imports_nothing_from_frappe_or_konsol():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert not name.startswith(("frappe", "konsol")), name


def test_privileged_roles_are_the_three_decided():
    assert M.PRIVILEGED_ROLES == ("Administrator", "System Manager", "EPM Admin")


# ---- off -------------------------------------------------------------------

def test_no_demo_user_means_off():
    for off in (None, "", "   "):
        d = M.decide(off, _user(), DEMO_ROLES)
        assert d.allowed is False, off
        assert d.reason == "demo mode is off", off


def test_off_wins_over_every_other_check():
    # Nothing about the user leaks when the key is unset.
    d = M.decide(None, _user(enabled=0, user_type="Website User"), ["System Manager"])
    assert d.allowed is False
    assert d.reason == "demo mode is off"


# ---- the user named by the key -------------------------------------------

def test_missing_user_row_is_refused():
    d = M.decide(DEMO, None, [])
    assert d.allowed is False
    assert d.reason == "no such user"


def test_disabled_user_is_refused():
    for enabled in (0, None, False):
        d = M.decide(DEMO, _user(enabled=enabled), DEMO_ROLES)
        assert d.allowed is False, enabled
        assert d.reason == "user disabled", enabled


def test_website_user_is_refused_naming_system_user():
    d = M.decide(DEMO, _user(user_type="Website User"), DEMO_ROLES)
    assert d.allowed is False
    assert "System User" in d.reason
    assert "Website User" in d.reason


def test_missing_user_type_is_refused():
    d = M.decide(DEMO, _user(user_type=None), DEMO_ROLES)
    assert d.allowed is False
    assert "System User" in d.reason


# ---- privileged roles ------------------------------------------------------

def test_each_privileged_role_is_refused_and_named():
    for role in M.PRIVILEGED_ROLES:
        d = M.decide(DEMO, _user(), DEMO_ROLES + [role])
        assert d.allowed is False, role
        assert role in d.reason, (role, d.reason)
        assert "privileged" in d.reason, d.reason


def test_two_privileged_roles_are_both_named():
    d = M.decide(DEMO, _user(), ["System Manager", "EPM Admin"])
    assert d.allowed is False
    assert "System Manager" in d.reason and "EPM Admin" in d.reason


def test_role_check_is_exact_not_substring():
    # "EPM Administrator" is not "EPM Admin"; a made-up role must not trip it.
    d = M.decide(DEMO, _user(), ["EPM Administrator"])
    assert d.allowed is True


# ---- allowed ----------------------------------------------------------------

def test_decided_demo_roles_are_allowed():
    d = M.decide(DEMO, _user(), DEMO_ROLES)
    assert d.allowed is True
    assert d.reason == "allowed"


def test_allowed_with_no_roles_at_all():
    # Role emptiness is not this rule's business; the Desk decides what an
    # unprivileged user may see.
    d = M.decide(DEMO, _user(), [])
    assert d.allowed is True


def test_allowed_is_an_exact_bool_on_both_paths():
    # v16 lesson (test_v16_hooks.py): callers compare with `is True`.
    assert type(M.decide(DEMO, _user(), DEMO_ROLES).allowed) is bool
    assert type(M.decide(None, _user(), DEMO_ROLES).allowed) is bool


def test_decision_is_immutable():
    d = M.decide(DEMO, _user(), DEMO_ROLES)
    try:
        d.allowed = False
    except (AttributeError, TypeError):
        return
    raise AssertionError("Decision.allowed could be reassigned")
