"""Demo mode endpoint wiring: konsol/demo_mode.py (demo mode D2).

`try_demo` is a Guest-callable, rate-limited GET that logs the visitor in as
the user named by `frappe.conf.demo_user` when `demo_mode_model.decide`
allows it, and answers 404 otherwise, so nothing hints at the feature when
it is off. `end_sessions`, `switch_off` and `switch_on` are the operator's
bench-execute helpers (PR #421 review, findings 1 and 2).

Loaded by path against a stub `frappe` module (pattern: test_close_www.py).
The decision rule itself is tested in test_demo_mode_model.py.
"""
import contextlib
import importlib.util
import os
import re
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


def _frappe_stub(conf, user_row, roles, session_counts):
    frappe = types.ModuleType("frappe")
    calls = _Calls()
    frappe.calls = calls
    frappe._ = lambda s: s
    frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.ValidationError = type("ValidationError", (Exception,), {})

    def decorator_recorder(name):
        def outer(**kw):
            def deco(f):
                calls.append((name, (f.__name__,), kw))
                return f
            return deco
        return outer

    def throw(msg, exc=None, **k):
        calls.append(("throw", (msg,), {"exc": exc}))
        raise (exc or frappe.ValidationError)(msg)

    def get_value(doctype, name, fields, **k):
        calls.append(("get_value", (doctype, name, fields), k))
        return user_row

    counts = list(session_counts)

    def sql(query, values=None, **k):
        # `Sessions` has no DocType, so the module counts with plain SQL.
        # Each call pops the next count; the last one repeats.
        calls.append(("sql", (query, values), k))
        n = counts.pop(0) if len(counts) > 1 else counts[0]
        return [(n,)]

    frappe.whitelist = decorator_recorder("whitelist")
    # the decorator lives in frappe.rate_limiter (v15 and v16); a bare frappe.rate_limit does not exist
    # and broke the module import on the live v16 stack (7 Nov 2026)
    rate_limiter = types.ModuleType("frappe.rate_limiter")
    rate_limiter.rate_limit = decorator_recorder("rate_limit")
    frappe.rate_limiter = rate_limiter
    sys.modules["frappe.rate_limiter"] = rate_limiter
    frappe.throw = throw
    frappe.conf = dict(conf)
    frappe.db = types.SimpleNamespace(get_value=get_value, sql=sql, set_value=calls.rec("set_value"))
    frappe.clear_document_cache = calls.rec("clear_document_cache")
    frappe.clear_cache = calls.rec("clear_cache")
    frappe.get_roles = lambda user=None: calls.append(("get_roles", (user,), {})) or list(roles)
    frappe.logger = lambda name=None: types.SimpleNamespace(
        info=calls.rec("log.info:%s" % name), error=calls.rec("log.error:%s" % name)
    )
    frappe.local = types.SimpleNamespace(
        login_manager=types.SimpleNamespace(login_as=calls.rec("login_as")),
        response={},
        flags=types.SimpleNamespace(),
    )
    sessions = types.ModuleType("frappe.sessions")
    sessions.clear_sessions = calls.rec("clear_sessions")
    frappe.sessions = sessions
    return frappe


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


def _load(conf, user_row=None, roles=(), session_counts=(0,)):
    frappe = _frappe_stub(conf, user_row, roles, session_counts)
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


def _only(frappe, name):
    found = [c for c in frappe.calls if c[0] == name]
    assert len(found) == 1, (name, frappe.calls)
    return found[0]


def _expect_not_found(module, frappe):
    try:
        module.try_demo()
    except frappe.DoesNotExistError as e:
        return e
    raise AssertionError("try_demo answered instead of 404")


# ---- declaration ------------------------------------------------------------

def test_try_demo_is_guest_callable_and_get_only():
    _module, frappe = _load({})
    decl = [c for c in frappe.calls if c[0] == "whitelist" and c[1] == ("try_demo",)]
    assert len(decl) == 1, frappe.calls
    assert decl[0][2] == {"allow_guest": True, "methods": ["GET"]}


def test_try_demo_is_rate_limited_per_ip():
    # PR #421 review, finding 3: an unauthenticated write (session insert,
    # User update, commit) per GET must be throttled. frappe.rate_limit is
    # IP-based by default.
    _module, frappe = _load({})
    decl = [c for c in frappe.calls if c[0] == "rate_limit" and c[1] == ("try_demo",)]
    assert len(decl) == 1, frappe.calls
    assert decl[0][2] == {"limit": 10, "seconds": 60}


def test_operator_helpers_are_not_whitelisted():
    # bench-execute helpers for the switch, not an HTTP surface.
    _module, frappe = _load({})
    exposed = {c[1][0] for c in frappe.calls if c[0] in ("whitelist", "rate_limit")}
    assert exposed == {"try_demo"}, exposed


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
    e = _expect_not_found(module, frappe)
    assert str(e) == "Not found"
    names = _names(frappe)
    assert "login_as" not in names
    assert "get_value" not in names, "the user table was read while off"
    assert "get_roles" not in names
    assert frappe.local.response == {}
    assert not hasattr(frappe.local.flags, "commit")


def test_off_logs_nothing():
    # PR #421 review, finding 4: a site without a demo gets bot hits on
    # /try; the log is not the place to count them.
    module, frappe = _load({})
    e = _expect_not_found(module, frappe)
    assert "off" not in str(e)
    assert not [c for c in frappe.calls if c[0].startswith("log.")], frappe.calls


# ---- try_demo: refused ----------------------------------------------------------

def test_privileged_demo_user_is_refused_with_not_found_and_an_error_log():
    module, frappe = _load({"demo_user": DEMO}, user_row=_user(), roles=DEMO_ROLES + ["System Manager"])
    _expect_not_found(module, frappe)
    assert "login_as" not in _names(frappe)
    assert _only(frappe, "throw")[2]["exc"] is frappe.DoesNotExistError
    # A key that names a refused user is an operator error: ERROR shows at
    # the default log level, INFO does not (finding 4).
    err = _only(frappe, "log.error:konsol")
    assert "System Manager" in " ".join(str(x) for x in err[1])
    assert not [c for c in frappe.calls if c[0].startswith("log.info")]
    assert not hasattr(frappe.local.flags, "commit")


def test_missing_or_disabled_user_is_refused():
    for row in (None, _user(enabled=0), _user(user_type="Website User")):
        module, frappe = _load({"demo_user": DEMO}, user_row=row, roles=DEMO_ROLES)
        _expect_not_found(module, frappe)
        assert "login_as" not in _names(frappe)
        assert _only(frappe, "log.error:konsol")


def test_roles_are_not_read_for_a_missing_user():
    module, frappe = _load({"demo_user": DEMO}, user_row=None)
    _expect_not_found(module, frappe)
    assert "get_roles" not in _names(frappe)


# ---- try_demo: allowed -------------------------------------------------------------

def test_allowed_logs_in_as_the_demo_user_and_redirects_to_desk():
    module, frappe = _load({"demo_user": DEMO}, user_row=_user(), roles=DEMO_ROLES)
    module.try_demo()
    assert _only(frappe, "login_as") == ("login_as", (DEMO,), {})
    assert frappe.local.response["type"] == "redirect"
    assert frappe.local.response["location"] == "/desk"
    assert "throw" not in _names(frappe)
    assert not [c for c in frappe.calls if c[0].startswith("log.")]


def test_allowed_asks_the_request_to_commit():
    # PR #421 review, finding 5: the login's Activity Log row is inserted
    # without a commit and a GET rolls back; flags.commit is what
    # app.sync_database honours.
    module, frappe = _load({"demo_user": DEMO}, user_row=_user(), roles=DEMO_ROLES)
    module.try_demo()
    assert frappe.local.flags.commit is True


def test_allowed_reads_the_user_row_the_rule_needs():
    module, frappe = _load({"demo_user": DEMO}, user_row=_user(), roles=DEMO_ROLES)
    module.try_demo()
    gv = _only(frappe, "get_value")
    assert gv[1][0] == "User"
    assert gv[1][1] == DEMO
    assert set(gv[1][2]) >= {"name", "enabled", "user_type"}
    assert gv[2].get("as_dict") is True
    assert _only(frappe, "get_roles") == ("get_roles", (DEMO,), {})


def test_roles_and_login_use_the_user_rows_canonical_name():
    # PR #421 review, finding 7: the key is operator-typed; the User row's
    # name is canonical.
    module, frappe = _load({"demo_user": "Demo@Example.COM"}, user_row=_user(), roles=DEMO_ROLES)
    module.try_demo()
    assert _only(frappe, "get_roles")[1] == (DEMO,)
    assert _only(frappe, "login_as")[1] == (DEMO,)
    assert module.decision().user == DEMO


def test_login_as_happens_before_the_redirect_is_set():
    module, frappe = _load({"demo_user": DEMO}, user_row=_user(), roles=DEMO_ROLES)
    seen = {}

    def login_as(user):
        seen["response_at_login"] = dict(frappe.local.response)

    frappe.local.login_manager.login_as = login_as
    module.try_demo()
    assert seen["response_at_login"] == {}


# ---- end_sessions ----------------------------------------------------------------

def test_end_sessions_clears_until_none_are_left_and_returns_the_number_removed():
    # PR #421 review, finding 1: clear_sessions removes at most 100 per call.
    module, frappe = _load({"demo_user": DEMO}, session_counts=[250, 150, 50, 0])
    with _installed(frappe):
        assert module.end_sessions() == 250
    clears = [c for c in frappe.calls if c[0] == "clear_sessions"]
    assert clears == [("clear_sessions", (DEMO,), {})] * 3, clears
    first = [c for c in frappe.calls if c[0] == "sql"][0]
    assert "tabSessions" in first[1][0]
    assert first[1][1] == DEMO
    names = _names(frappe)
    assert names.index("sql") < names.index("clear_sessions")


def test_end_sessions_takes_an_explicit_user_after_the_key_is_removed():
    module, frappe = _load({}, session_counts=[1, 0])
    with _installed(frappe):
        assert module.end_sessions(DEMO) == 1
    assert _only(frappe, "clear_sessions") == ("clear_sessions", (DEMO,), {})


def test_end_sessions_with_no_user_does_nothing():
    module, frappe = _load({}, session_counts=[9])
    with _installed(frappe):
        assert module.end_sessions() == 0
    assert "clear_sessions" not in _names(frappe)
    assert "sql" not in _names(frappe)


def test_end_sessions_refuses_to_spin_when_nothing_is_removed():
    module, frappe = _load({"demo_user": DEMO}, session_counts=[5, 5])
    with _installed(frappe):
        try:
            module.end_sessions()
        except RuntimeError as e:
            assert DEMO in str(e)
        else:
            raise AssertionError("end_sessions looped on a count that did not fall")
    assert len([c for c in frappe.calls if c[0] == "clear_sessions"]) == 1


# ---- switch_off / switch_on --------------------------------------------------------

def test_switch_off_disables_the_user_then_ends_every_session():
    # PR #421 review, finding 2: v16 caches site_config per worker for 60 s,
    # so unsetting the key alone leaves a window. decide() reads `enabled`
    # from the DB on every request, and Session.validate_user refuses a
    # disabled user regardless of the key.
    module, frappe = _load({"demo_user": "Demo@Example.COM"}, user_row=_user(), session_counts=[2, 0])
    with _installed(frappe):
        out = module.switch_off()
    assert out == {"user": DEMO, "enabled": 0, "sessions_ended": 2}
    sv = _only(frappe, "set_value")
    assert sv[1][:4] == ("User", DEMO, "enabled", 0)
    # validate_user reads get_cached_value: the cached row must go too.
    assert _only(frappe, "clear_document_cache")[1] == ("User", DEMO)
    names = _names(frappe)
    assert names.index("set_value") < names.index("clear_sessions")


def test_switch_off_takes_an_explicit_user_and_refuses_an_unknown_one():
    module, frappe = _load({}, user_row=_user(), session_counts=[0])
    with _installed(frappe):
        assert module.switch_off(DEMO)["user"] == DEMO
    module, frappe = _load({}, user_row=None)
    with _installed(frappe):
        try:
            module.switch_off(DEMO)
        except frappe.ValidationError as e:
            assert DEMO in str(e)
        else:
            raise AssertionError("switch_off accepted a user that does not exist")
    assert "set_value" not in _names(frappe)


def test_switch_off_with_no_user_at_all_is_refused():
    module, frappe = _load({})
    with _installed(frappe):
        try:
            module.switch_off()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("switch_off ran with neither a key nor an argument")


def test_switch_on_enables_the_user_and_reports_the_key():
    module, frappe = _load({"demo_user": DEMO}, user_row=_user(enabled=0))
    with _installed(frappe):
        out = module.switch_on()
    assert out == {"user": DEMO, "enabled": 1, "key": DEMO}
    sv = _only(frappe, "set_value")
    assert sv[1][:4] == ("User", DEMO, "enabled", 1)
    assert _only(frappe, "clear_document_cache")[1] == ("User", DEMO)
    assert "clear_sessions" not in _names(frappe)


def test_switch_on_without_the_key_says_so():
    module, frappe = _load({}, user_row=_user(enabled=0))
    with _installed(frappe):
        out = module.switch_on(DEMO)
    assert out["key"] is None
    assert _only(frappe, "set_value")[1][:4] == ("User", DEMO, "enabled", 1)


# ---- source: nothing else reads the key --------------------------------------------

def test_only_demo_mode_reads_the_demo_user_key():
    # The word `demo_user` may appear in demo_mode.py (the conf read) and in
    # demo_mode_model.py (a parameter name) and nowhere else in the app.
    hits = set()
    for dirpath, dirnames, filenames in os.walk(APP_DIR):
        dirnames[:] = [d for d in dirnames if d not in ("tests", "node_modules", "__pycache__")]
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            try:
                with open(path, errors="replace") as fh:
                    src = fh.read()
            except (OSError, UnicodeError):
                continue
            if re.search(r"\bdemo_user\b", src):
                hits.add(os.path.relpath(path, APP_DIR))
    assert hits == {"demo_mode.py", "demo_mode_model.py"}, sorted(hits)
