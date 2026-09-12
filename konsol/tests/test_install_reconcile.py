"""A fresh site's warehouse is filled after install (#142), exercised rather than read.

Nothing syncs during install-app (sync_table and after_commit_once stand down
while frappe.flags.in_install is set), and after_migrate was the only caller of
reconcile_all, so a new site's write-through tables stayed empty until the
first bench migrate. install.py now queues a reconcile job after the install
commits, and again when the configurator points EPM Settings at the real
ClickHouse. Loaded under a private module name with a stub frappe.
"""
import ast
import importlib.util
import os
import sys
import types
from collections import deque

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def _load(enqueue_raises=None, target=None):
    """install.py over a stub frappe. ``target`` is the EPM Settings store
    that _warehouse_target reads."""
    fake = types.ModuleType("frappe")
    fake.flags = types.SimpleNamespace(in_install="konsol")
    fake.enqueued = []
    fake.printed = []
    after_commit = _Callbacks()

    def commit():
        after_commit.run()

    fake.db = types.SimpleNamespace(after_commit=after_commit, commit=commit, exists=lambda *a: True)
    fake.logger = lambda *a, **k: types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None)

    def enqueue(method, **kwargs):
        if enqueue_raises:
            raise enqueue_raises
        fake.enqueued.append((method, kwargs))

    fake.enqueue = enqueue
    store = target if target is not None else {}
    fake.get_single = lambda doctype: _Settings(store)

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
    mod._warehouse_target = lambda: dict(store)
    return mod, fake


def _hook(name):
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None


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
    assert fake.enqueued == [(mod.RECONCILE_JOB, {"queue": "long"})]
    fake.db.commit()
    assert len(fake.enqueued) == 1, "a later commit queued it again"


def test_the_job_is_reconcile_not_a_build():
    mod, _ = _load()
    assert mod.RECONCILE_JOB == "konsol.install.reconcile_warehouse"
    assert callable(mod.reconcile_warehouse)
    assert "queue_consolidation_build" not in mod.RECONCILE_JOB


def test_redis_down_does_not_fail_the_install_commit():
    mod, fake = _load(enqueue_raises=ConnectionError("redis_queue unreachable"))
    mod.after_sync()
    fake.db.commit()  # must not raise
    assert fake.enqueued == []


def test_reconcile_job_returns_reconcile_all_result():
    mod, _ = _load()
    stub = types.ModuleType("konsol.clickhouse")
    stub.reconcile_all = lambda: {"epm_staging.scenarios": 3, "epm_staging.currencies": None}
    saved = sys.modules.get("konsol.clickhouse")
    sys.modules["konsol.clickhouse"] = stub
    try:
        assert mod.reconcile_warehouse() == {"epm_staging.scenarios": 3, "epm_staging.currencies": None}
    finally:
        if saved is None:
            sys.modules.pop("konsol.clickhouse", None)
        else:
            sys.modules["konsol.clickhouse"] = saved


def test_configurator_setting_a_new_target_queues_the_job():
    """init.sh configures ClickHouse after install-app, so the install-time
    job ran against the default target. A changed target reconciles again."""
    store = {"clickhouse_host": "localhost", "clickhouse_port": "8123",
             "clickhouse_user": "default", "clickhouse_password": ""}
    mod, fake = _load(target=store)
    mod.setup_epm_settings(ch_host="clickhouse", ch_port=8123, ch_password="pw")
    assert fake.enqueued == [(mod.RECONCILE_JOB, {"queue": "long"})]


def test_redeploy_with_the_same_target_queues_nothing():
    """Every deploy of an existing site runs setup_epm_settings after a
    migrate that has already reconciled."""
    store = {"clickhouse_host": "clickhouse", "clickhouse_port": 8123,
             "clickhouse_user": "default", "clickhouse_password": "pw"}
    mod, fake = _load(target=store)
    mod.setup_epm_settings(ch_host="clickhouse", ch_port=8123, ch_password="pw")
    assert fake.enqueued == []
