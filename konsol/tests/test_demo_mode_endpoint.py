"""Demo mode endpoint wiring: konsol/demo_mode.py (demo mode D2).

`try_demo` is a Guest-callable GET that logs the visitor in as the user named
by `frappe.conf.demo_user` when `demo_mode_model.decide` allows it, and
answers 404 otherwise, so nothing hints at the feature when it is off.
`end_sessions` is the off switch's helper.

Loaded by path against a stub `frappe` module (pattern: test_close_www.py).
The decision rule itself is tested in test_demo_mode_model.py.
"""
import contextlib
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(APP_DIR)
PY_PATH = os.path.join(APP_DIR, "demo_mode.py")
DEMO = "demo@example.com"
DEMO_ROLES = ["EPM Analyst", "Entity Accountant", "Desk User"]


class _Calls(list):
    def rec(self, name):
        def f(*a, **k):
            self.append((name, a, k))
        return f


def _frappe_stub(conf, user_row, roles, session_count=0):
    frappe = types.ModuleType("frappe")
    calls = _Calls()
    frappe.calls = calls
    frappe._ = lambda s: s
    frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.ValidationError = type("ValidationError", (Exception,), {})

    def whitelist(**kw):
        def deco(f):
            calls.append(("whitelist", (f.__name__,), kw))
            return f
        return deco

    def throw(msg, exc=None, **k):
        calls.append(("throw", (msg,), {"exc": exc}))
        raise (exc or frappe.ValidationError)(msg)

    def get_value(doctype, name, fields, **k):
        calls.append(("get_value", (doctype, name, fields), k))
        return user_row

    def sql(query, values=None, **k):
        # `Sessions` has no DocType, so the module counts with plain SQL.
        calls.append(("sql", (query, values), k))
        return [(session_count,)]

    frappe.whitelist = whitelist
    frappe.throw = throw
    frappe.conf = dict(conf)
    frappe.db = types.SimpleNamespace(get_value=get_value, sql=sql)
    frappe.get_roles = lambda user=None: calls.append(("get_roles", (user,), {})) or list(roles)
    frappe.logger = lambda name=None: types.SimpleNamespace(info=calls.rec("log.info:%s" % name))
    frappe.local = types.SimpleNamespace(
        login_manager=types.SimpleNamespace(login_as=calls.rec("login_as")),
        response={},
    )
    sessions = types.ModuleType("frappe.sessions")
    sessions.clear_sessions = calls.rec("clear_sessions")
    frappe.sessions = sessions
    return frappe, sessions


@contextlib.contextmanager
def _installed(frappe):
    """Put the stub `frappe` (and its `frappe.sessions`) in sys.modules for
    the block; `end_sessions` imports frappe.sessions lazily, at call time."""
    saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.sessions")}
    sys.modules["frappe"] = frappe
    sys.modules["frappe.sessions"] = frappe.sessions
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                sys.modules[k] = v
            else:
                sys.modules.pop(k, None)


def _load(conf, user_row=None, roles=(), session_count=0):
    frappe, _sessions = _frappe_stub(conf, user_row, roles, session_count)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    with _installed(frappe):
        spec = importlib.util.spec_from_file_location("demo_mode_under_test", PY_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module, frappe


def _user(**over):
    row = {"name": DEMO, "enabled": 1, "user_type": "System User"}
    row.update(over)
    return row


def _names(frappe):
    return [c[0] for c in frappe.calls]


# ---- declaration ------------------------------------------------------------

def test_try_demo_is_guest_callable_and_get_only():
    _module, frappe = _load({})
    decl = [c for c in frappe.calls if c[0] == "whitelist" and c[1] == ("try_demo",)]
    assert len(decl) == 1, frappe.calls
    assert decl[0][2] == {"allow_guest": True, "methods": ["GET"]}


def test_end_sessions_is_not_whitelisted():
    # It is a bench-execute helper for the off switch, not an HTTP surface.
    _module, frappe = _load({})
    assert ("end_sessions",) not in [c[1] for c in frappe.calls if c[0] == "whitelist"]


# ---- enabled() ----------------------------------------------------------------

def test_enabled_reads_the_demo_user_key():
    module, _frappe = _load({"demo_user": DEMO})
    assert module.enabled() == DEMO


def test_enabled_is_none_when_the_key_is_missing_or_blank():
    for conf in ({}, {"demo_user": ""}, {"demo_user": None}):
        module, _frappe = _load(conf)
        assert module.enabled() is None, conf


# ---- try_demo: off ------------------------------------------------------------

def test_off_answers_not_found_and_touches_nothing():
    module, frappe = _load({}, user_row=_user(), roles=DEMO_ROLES)
    try:
        module.try_demo()
    except frappe.DoesNotExistError as e:
        assert str(e) == "Not found"
    else:
        raise AssertionError("try_demo answered while demo mode is off")
    names = _names(frappe)
    assert "login_as" not in names
    assert "get_value" not in names, "the user table was read while off"
    assert "get_roles" not in names
    assert frappe.local.response == {}


def test_off_logs_the_reason_but_does_not_show_it():
    module, frappe = _load({})
    try:
        module.try_demo()
    except frappe.DoesNotExistError as e:
        assert "off" not in str(e)
    logs = [c for c in frappe.calls if c[0] == "log.info:konsol"]
    assert logs, frappe.calls
    assert "demo mode is off" in " ".join(str(x) for x in logs[0][1])


# ---- try_demo: refused ----------------------------------------------------------

def test_privileged_demo_user_is_refused_with_not_found():
    module, frappe = _load({"demo_user": DEMO}, user_row=_user(), roles=DEMO_ROLES + ["System Manager"])
    try:
        module.try_demo()
    except frappe.DoesNotExistError:
        pass
    else:
        raise AssertionError("a System Manager demo user was logged in")
    assert "login_as" not in _names(frappe)
    throw = [c for c in frappe.calls if c[0] == "throw"][0]
    assert throw[2]["exc"] is frappe.DoesNotExistError
    logs = [c for c in frappe.calls if c[0] == "log.info:konsol"]
    assert "System Manager" in " ".join(str(x) for x in logs[0][1])


def test_missing_or_disabled_user_is_refused():
    for row in (None, _user(enabled=0), _user(user_type="Website User")):
        module, frappe = _load({"demo_user": DEMO}, user_row=row, roles=DEMO_ROLES)
        try:
            module.try_demo()
        except frappe.DoesNotExistError:
            pass
        else:
            raise AssertionError("refused user row was logged in: %r" % (row,))
        assert "login_as" not in _names(frappe)


def test_roles_are_not_read_for_a_missing_user():
    module, frappe = _load({"demo_user": DEMO}, user_row=None)
    try:
        module.try_demo()
    except frappe.DoesNotExistError:
        pass
    assert "get_roles" not in _names(frappe)


# ---- try_demo: allowed -------------------------------------------------------------

def test_allowed_logs_in_as_the_demo_user_and_redirects_to_desk():
    module, frappe = _load({"demo_user": DEMO}, user_row=_user(), roles=DEMO_ROLES)
    module.try_demo()
    login = [c for c in frappe.calls if c[0] == "login_as"]
    assert login == [("login_as", (DEMO,), {})]
    assert frappe.local.response["type"] == "redirect"
    assert frappe.local.response["location"] == "/desk"
    assert "throw" not in _names(frappe)


def test_allowed_reads_the_user_row_the_rule_needs():
    module, frappe = _load({"demo_user": DEMO}, user_row=_user(), roles=DEMO_ROLES)
    module.try_demo()
    gv = [c for c in frappe.calls if c[0] == "get_value"][0]
    assert gv[1][0] == "User"
    assert gv[1][1] == DEMO
    assert set(gv[1][2]) >= {"enabled", "user_type"}
    assert gv[2].get("as_dict") is True
    roles = [c for c in frappe.calls if c[0] == "get_roles"]
    assert roles == [("get_roles", (DEMO,), {})]


def test_login_as_happens_before_the_redirect_is_set():
    module, frappe = _load({"demo_user": DEMO}, user_row=_user(), roles=DEMO_ROLES)
    seen = {}

    def login_as(user):
        seen["response_at_login"] = dict(frappe.local.response)

    frappe.local.login_manager.login_as = login_as
    module.try_demo()
    assert seen["response_at_login"] == {}


# ---- end_sessions ----------------------------------------------------------------

def test_end_sessions_clears_the_demo_user_and_returns_the_count():
    module, frappe = _load({"demo_user": DEMO}, session_count=3)
    with _installed(frappe):
        assert module.end_sessions() == 3
    assert [c for c in frappe.calls if c[0] == "clear_sessions"] == [("clear_sessions", (DEMO,), {})]
    count = [c for c in frappe.calls if c[0] == "sql"][0]
    assert "tabSessions" in count[1][0]
    assert count[1][1] == DEMO
    # Counted before clearing, so the number is what the switch logged out.
    names = _names(frappe)
    assert names.index("sql") < names.index("clear_sessions")


def test_end_sessions_takes_an_explicit_user_after_the_key_is_removed():
    # The off switch unsets the key first, then ends the sessions by name.
    module, frappe = _load({}, session_count=1)
    with _installed(frappe):
        assert module.end_sessions(DEMO) == 1
    assert [c for c in frappe.calls if c[0] == "clear_sessions"] == [("clear_sessions", (DEMO,), {})]


def test_end_sessions_with_no_user_does_nothing():
    module, frappe = _load({}, session_count=9)
    assert module.end_sessions() == 0
    assert "clear_sessions" not in _names(frappe)
    assert "sql" not in _names(frappe)


# ---- source: nothing else reads the key --------------------------------------------

def test_only_demo_mode_reads_the_demo_user_key():
    # The quoted key, as frappe.conf.get("demo_user") or a JS/JSON string,
    # may appear in one file. The pure model takes the value as a parameter
    # named demo_user, which is not a read of the key.
    hits = []
    for dirpath, dirnames, filenames in os.walk(APP_DIR):
        dirnames[:] = [d for d in dirnames if d not in ("tests", "node_modules", "public", "__pycache__")]
        for fn in filenames:
            if not fn.endswith((".py", ".js", ".json", ".html")):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, errors="replace") as fh:
                src = fh.read()
            if '"demo_user"' in src or "'demo_user'" in src:
                hits.append(os.path.relpath(path, APP_DIR))
    assert hits == ["demo_mode.py"], hits
