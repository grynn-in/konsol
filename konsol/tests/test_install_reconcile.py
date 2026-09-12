"""A fresh site's warehouse is filled after install (#142), exercised rather than read.

Nothing syncs during install-app (sync_table and after_commit_once stand down
while frappe.flags.in_install is set), and after_migrate was the only caller of
reconcile_all, so a new site's write-through tables stayed empty until the
first bench migrate. install.py now queues a reconcile job after the install
commits, and again when the configurator points EPM Settings at the real
ClickHouse. Loaded under a private module name with a stub frappe and a stub
konsol.clickhouse; install.py's own functions (_warehouse_target included) run
unmodified.
"""
import ast
import contextlib
import importlib.util
import os
import sys
import threading
import time
import types
from collections import deque

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "zz142.local"
DB_NAME = "_zz142db"
KEY = f"konsol:reconcile_warehouse:{DB_NAME}"


class _Callbacks:
    """Frappe's CallbackManager: runs in order, and does not catch."""

    def __init__(self):
        self._functions = deque()

    def add(self, fn):
        self._functions.append(fn)

    def run(self):
        while self._functions:
            self._functions.popleft()()


class _Settings:
    def __init__(self, store):
        self._store = store
        self.flags = types.SimpleNamespace()

    def save(self):
        for key in ("clickhouse_host", "clickhouse_port", "clickhouse_user", "clickhouse_password"):
            self._store[key] = getattr(self, key)


class _LockNotOwned(Exception):
    """redis-py's LockNotOwnedError: releasing a lock that already expired."""


class _Lock:
    """redis-py's Lock over a threading.Lock shared per key."""

    def __init__(self, cache, name, timeout, blocking_timeout):
        self._cache, self.name = cache, name
        self.timeout, self.blocking_timeout = timeout, blocking_timeout
        self._inner = cache.inner.setdefault(name, threading.Lock())

    def acquire(self):
        if self._cache.acquire_raises:
            raise self._cache.acquire_raises
        got = self._inner.acquire(timeout=self._cache.wait_limit or self.blocking_timeout)
        if got:
            self._cache.events.append(("acquire", self.name))
        return got

    def release(self):
        self._cache.events.append(("release", self.name))
        if self._cache.release_raises:
            raise _LockNotOwned("lock expired")
        self._inner.release()


class _Cache:
    def __init__(self):
        self.inner, self.events, self.locks = {}, [], []
        self.wait_limit = None  # test override of blocking_timeout
        self.acquire_raises = None
        self.release_raises = False

    def lock(self, name, timeout=None, blocking_timeout=None):
        lock = _Lock(self, name, timeout, blocking_timeout)
        self.locks.append(lock)
        return lock


def _load(enqueue_raises=None, store=None):
    """install.py over a stub frappe. ``store`` is the EPM Settings record."""
    fake = types.ModuleType("frappe")
    fake.flags = types.SimpleNamespace(in_install="konsol")
    fake.local = types.SimpleNamespace(site=SITE)
    fake.conf = types.SimpleNamespace(db_name=DB_NAME)
    fake.enqueued = []
    fake.errors = []
    fake.warnings = []
    fake.cache = _Cache()
    after_commit = _Callbacks()
    fake.db = types.SimpleNamespace(after_commit=after_commit, commit=after_commit.run, exists=lambda *a: True)
    fake.logger = lambda *a, **k: types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda msg, *a, **k: fake.warnings.append(msg))
    fake.log_error = lambda title=None, message=None, **k: fake.errors.append(title)

    def enqueue(method, **kwargs):
        if enqueue_raises:
            raise enqueue_raises
        fake.enqueued.append((method, kwargs))

    fake.enqueue = enqueue
    fake.store = store if store is not None else {}
    fake.get_single = lambda doctype: _Settings(fake.store)

    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = fake
    try:
        spec = importlib.util.spec_from_file_location(
            "_host_install", os.path.join(APP_DIR, "install.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    mod.frappe = fake
    return mod, fake


@contextlib.contextmanager
def _clickhouse(store=None, reconcile_all=None):
    """konsol.clickhouse stub. get_connection mirrors the real one: it reads
    EPM Settings, password included, with the doctype's defaults."""
    stub = types.ModuleType("konsol.clickhouse")
    store = store if store is not None else {}
    stub.get_connection = lambda: {
        "host": store.get("clickhouse_host") or "localhost",
        "port": store.get("clickhouse_port") or "8123",
        "user": store.get("clickhouse_user") or "default",
        "password": store.get("clickhouse_password") or "",
        "secure": bool(store.get("clickhouse_secure", 0)),
        "verify": bool(store.get("clickhouse_verify_tls", 1)),
    }
    stub.reconcile_all = reconcile_all or (lambda: {})
    saved = sys.modules.get("konsol.clickhouse")
    sys.modules["konsol.clickhouse"] = stub
    try:
        yield stub
    finally:
        if saved is None:
            sys.modules.pop("konsol.clickhouse", None)
        else:
            sys.modules["konsol.clickhouse"] = saved


def _hook(name):
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None


JOB = ("konsol.install.reconcile_warehouse", {"queue": "long", "timeout": 1500})
DEPLOYED = {"clickhouse_host": "clickhouse", "clickhouse_port": 8123,
            "clickhouse_user": "default", "clickhouse_password": "pw"}
ALL_FAILED = {"epm_staging.scenarios": None, "epm_gold.currencies": None}


def _configure(store, **kwargs):
    """Run setup_epm_settings the way init.sh calls it, over ``store``, and
    return the stub frappe (``fake.enqueued`` holds what it queued)."""
    mod, fake = _load(store=store)
    with _clickhouse(fake.store):
        mod.setup_epm_settings(**{"ch_host": "clickhouse", "ch_port": 8123, "ch_password": "pw", **kwargs})
    return fake


# -- install-app ------------------------------------------------------------

def test_install_hook_is_after_sync_not_after_install():
    """after_install runs before fixtures are imported, and each fixture file
    commits: a callback registered there would reconcile half the reference
    data. after_sync runs after them, and only from install-app."""
    assert _hook("after_sync") == ["konsol.install.after_sync"]
    assert "konsol.install.after_sync" not in (_hook("after_install") or [])


def test_after_sync_queues_the_job_only_at_the_commit():
    mod, fake = _load()
    mod.after_sync()
    assert fake.enqueued == [], "queued inside the install transaction"
    fake.db.commit()
    assert fake.enqueued == [JOB]
    fake.db.commit()
    assert len(fake.enqueued) == 1, "a later commit queued it again"


def test_the_job_is_reconcile_not_a_build():
    mod, _ = _load()
    assert mod.RECONCILE_JOB == JOB[0]
    assert callable(mod.reconcile_warehouse)


def test_redis_down_does_not_fail_the_install_commit():
    mod, fake = _load(enqueue_raises=ConnectionError("redis_queue unreachable"))
    mod.after_sync()
    fake.db.commit()  # must not raise
    assert fake.enqueued == []


# -- the configurator (setup_epm_settings) ------------------------------------

def test_configurator_setting_a_new_target_queues_the_job_after_the_commit():
    """init.sh configures ClickHouse after install-app, so the install-time
    job ran against the default target. A changed target reconciles again,
    once the new settings are committed."""
    mod, fake = _load(store={})  # a fresh site: every field at its default
    with _clickhouse(fake.store):
        fake.db.commit = lambda: fake.enqueued.append("COMMIT") or fake.db.after_commit.run()
        mod.setup_epm_settings(ch_host="clickhouse", ch_port=8123, ch_password="pw")
    assert fake.enqueued == ["COMMIT", JOB], f"queued before the commit: {fake.enqueued}"


def test_password_change_queues_the_job():
    fake = _configure(dict(DEPLOYED), ch_password="rotated")
    assert fake.enqueued == [JOB]


def test_redeploy_with_the_same_target_queues_nothing():
    """Every deploy of an existing site runs setup_epm_settings after a
    migrate that has already reconciled."""
    fake = _configure(dict(DEPLOYED))
    assert fake.enqueued == []


def test_warehouse_target_includes_the_password():
    mod, fake = _load(store=dict(DEPLOYED))
    with _clickhouse(fake.store):
        before = mod._warehouse_target()
        fake.store["clickhouse_password"] = "rotated"
        assert mod._warehouse_target() != before
        fake.store["clickhouse_password"] = "pw"
        assert mod._warehouse_target() == before


# -- the job: timeouts and lock ---------------------------------------------

def test_timeouts_let_a_waiter_finish_before_rq_kills_it():
    """wait + run <= job timeout <= lock timeout: RQ never kills a job that
    waited its full wait, and a live holder never outlives its lock."""
    mod, _ = _load()
    assert mod.RECONCILE_WAIT_SECONDS < mod.RECONCILE_JOB_TIMEOUT <= mod.RECONCILE_LOCK_SECONDS
    assert mod.RECONCILE_JOB_TIMEOUT - mod.RECONCILE_WAIT_SECONDS >= 300, "too little time left to run"
    assert JOB[1]["timeout"] == mod.RECONCILE_JOB_TIMEOUT


def test_reconcile_job_returns_reconcile_all_result_under_the_lock():
    mod, fake = _load()
    seen = []

    def reconcile_all():
        seen.append(list(fake.cache.events))
        return {"epm_staging.scenarios": 3, "epm_staging.currencies": None}

    with _clickhouse(reconcile_all=reconcile_all):
        assert mod.reconcile_warehouse() == {"epm_staging.scenarios": 3, "epm_staging.currencies": None}
    assert seen == [[("acquire", KEY)]], "reconcile_all ran outside the lock"
    assert fake.cache.events == [("acquire", KEY), ("release", KEY)]
    (lock,) = fake.cache.locks
    assert lock.name == KEY, "keyed on the database, so a site alias shares it"
    assert lock.timeout == mod.RECONCILE_LOCK_SECONDS
    assert lock.blocking_timeout == mod.RECONCILE_WAIT_SECONDS
    assert fake.errors == [], "a partial sync is not an Error Log"


def test_lock_is_released_when_reconcile_raises():
    mod, fake = _load()

    def boom():
        raise RuntimeError("ClickHouse exploded")

    with _clickhouse(reconcile_all=boom):
        try:
            mod.reconcile_warehouse()
        except RuntimeError:
            pass
    assert [e[0] for e in fake.cache.events] == ["acquire", "release"]


def test_release_failing_does_not_mask_the_reconcile_error():
    mod, fake = _load()
    fake.cache.release_raises = True

    def boom():
        raise RuntimeError("ClickHouse exploded")

    with _clickhouse(reconcile_all=boom):
        try:
            mod.reconcile_warehouse()
        except RuntimeError as e:
            assert str(e) == "ClickHouse exploded"
        else:
            raise AssertionError("the reconcile error was swallowed")


def test_release_failing_after_success_still_returns_the_result():
    """An expired lock (LockNotOwnedError on release) must not fail a
    reconcile that already synced."""
    mod, fake = _load()
    fake.cache.release_raises = True
    with _clickhouse(reconcile_all=lambda: {"epm_staging.scenarios": 3}):
        assert mod.reconcile_warehouse() == {"epm_staging.scenarios": 3}
    assert ("release", KEY) in fake.cache.events


def test_redis_cache_down_reconciles_without_the_lock():
    mod, fake = _load()
    fake.cache.acquire_raises = ConnectionError("redis_cache unreachable")
    with _clickhouse(reconcile_all=lambda: {"epm_staging.scenarios": 3}):
        assert mod.reconcile_warehouse() == {"epm_staging.scenarios": 3}
    assert fake.errors == []
    assert any("lock unavailable" in w for w in fake.warnings)


def test_a_second_concurrent_run_waits_for_the_first():
    """Two jobs on two workers: the second must not start reconcile_all until
    the first has finished, or their TRUNCATE/INSERTs interleave. Daemon
    threads and a 5 s wait keep a broken release from hanging the run."""
    mod, fake = _load()
    fake.cache.wait_limit = 5
    log, first_inside, let_first_finish = [], threading.Event(), threading.Event()

    def reconcile_all():
        name = threading.current_thread().name
        log.append(f"{name} start")
        if name == "first":
            first_inside.set()
            let_first_finish.wait(5)
        log.append(f"{name} end")
        return {"epm_staging.scenarios": 1}

    with _clickhouse(reconcile_all=reconcile_all):
        first = threading.Thread(target=mod.reconcile_warehouse, name="first", daemon=True)
        second = threading.Thread(target=mod.reconcile_warehouse, name="second", daemon=True)
        first.start()
        assert first_inside.wait(5)
        second.start()
        time.sleep(0.2)
        assert log == ["first start"], f"second ran while first held the lock: {log}"
        let_first_finish.set()
        first.join(5)
        second.join(6)
    assert log == ["first start", "first end", "second start", "second end"]


def test_lock_never_acquired_skips_and_logs():
    mod, fake = _load()
    fake.cache.wait_limit = 0.05
    ran = []
    with _clickhouse(reconcile_all=lambda: ran.append(1) or {}):
        held = fake.cache.lock(KEY)
        assert held.acquire()
        assert mod.reconcile_warehouse() is None
        held.release()
    assert ran == []
    assert fake.errors == ["Warehouse reconcile skipped: another reconcile held the lock"]


# -- the job: telling an outage from an unconfigured site ---------------------

def test_every_table_failing_writes_an_error_log():
    """sync_table and reconcile_all swallow every failure, so the job ends
    successfully even when nothing reached ClickHouse."""
    mod, fake = _load()
    with _clickhouse(dict(DEPLOYED), reconcile_all=lambda: dict(ALL_FAILED)):
        mod.reconcile_warehouse()  # must not raise
    assert fake.errors == ["Warehouse reconcile: no table synced"]


def test_every_table_failing_on_the_untouched_default_target_only_warns():
    """A fresh site's install-time run, before the configurator sets the real
    target: expected, so a warning, not an Error Log."""
    mod, fake = _load()
    with _clickhouse({}, reconcile_all=lambda: dict(ALL_FAILED)):
        mod.reconcile_warehouse()
    assert fake.errors == []
    assert any("default" in w for w in fake.warnings)


def test_localhost_alone_is_not_the_default_target():
    """A dev bench with ClickHouse on localhost but a password set is a real
    outage when nothing syncs."""
    mod, fake = _load()
    with _clickhouse({"clickhouse_password": "pw"}, reconcile_all=lambda: dict(ALL_FAILED)):
        mod.reconcile_warehouse()
    assert fake.errors == ["Warehouse reconcile: no table synced"]
