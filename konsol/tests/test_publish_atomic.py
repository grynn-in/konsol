"""A publish commits or rolls back as one transaction (konsol#135).

The Budget Line Custom Field sync inserts Custom Fields, and CustomField.on_update
calls frappe.db.updatedb, which ends with a commit. Run inside a publish, it
committed the Dimension's save halfway, before the build request was taken.
The publish now enqueues the sync as a job after the commit.

Site-free: the code runs against a stub frappe.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_APPLY = os.path.join(APP_DIR, "schema_apply.py")
LIFECYCLE = os.path.join(APP_DIR, "schema_lifecycle.py")
INSTALL = os.path.join(APP_DIR, "install.py")
JOB = "konsol.schema_apply.sync_budget_custom_fields_job"
DBT_JOB = "konsol.tasks.run_dbt_build_async"

# frappe.enqueue's own parameters: none of them may be a job kwarg.
_ENQUEUE_OWN = {"method", "queue", "timeout", "event", "is_async", "job_name", "now",
                "enqueue_after_commit", "on_success", "on_failure", "at_front",
                "job_id", "deduplicate"}


def _tree(path):
    with open(path) as f:
        return ast.parse(f.read())


def _functions(path):
    return {n.name: n for n in _tree(path).body if isinstance(n, ast.FunctionDef)}


def _calls(fn):
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)]


def _call_name(call):
    return getattr(call.func, "attr", None) or getattr(call.func, "id", None)


def _load(names, ns):
    """Compile the named schema_apply functions (and its _BUDGET constants) into ns."""
    tree = _tree(SCHEMA_APPLY)
    body = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in names)
            or (isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) and t.id.startswith("_BUDGET")
                                                   for t in n.targets))]
    for node in body:   # drop @frappe.whitelist(): the stub has none
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
    exec(compile(ast.Module(body=body, type_ignores=[]), SCHEMA_APPLY, "exec"), ns)
    return ns


class _D(dict):
    """frappe._dict."""
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class _Throw(Exception):
    pass


class _Validation(Exception):
    """frappe.ValidationError (CustomField.validate: "already exists")."""


class _Duplicate(Exception):
    """frappe.DuplicateEntryError (db_insert)."""


def _cint(s, default=0):
    """frappe.utils.cint."""
    try:
        return int(float(s))
    except Exception:
        return default


def _stub_frappe(enqueued, logged, enqueue_error=None, form_dict=None):
    """A frappe for apply_schema. set_user behaves as v15's (__init__.py:641):
    it rewrites the session in place (user, sid, data), clears form_dict and
    resets the permission caches (local.user_perms, local.cache)."""
    session = _D(user="zz@example.com", sid="sid-of-the-request", data=_D(csrf_token="t"))
    fr = types.SimpleNamespace(
        throw=lambda msg, exc=None: (_ for _ in ()).throw(_Throw(msg)),
        PermissionError=PermissionError,
        get_roles=lambda: ["EPM Admin"],
        log_error=lambda *a, **kw: logged.append(a),
        get_traceback=lambda: "tb",
        logger=lambda: types.SimpleNamespace(info=lambda msg: None, warning=lambda msg: None),
        utils=types.SimpleNamespace(cint=_cint),
        session=session,
        local=types.SimpleNamespace(session=session, form_dict=_D(form_dict or {}),
                                    user_perms="perms:zz@example.com", cache={"k": "zz"}),
    )
    fr.form_dict = fr.local.form_dict

    def set_user(user):
        session.user = user
        session.sid = user
        session.data = _D()
        fr.local.form_dict = fr.form_dict = _D()
        fr.local.user_perms = None
        fr.local.cache = {}

    def enqueue(method, **kw):
        if enqueue_error:
            raise enqueue_error
        enqueued.append({"method": method, "kw": kw, "user": session.user})

    fr.set_user, fr.enqueue = set_user, enqueue
    return fr


def _publish_ns(enqueued, logged, synced, enqueue_error=None, form_dict=None, sync_error=None):
    fr = _stub_frappe(enqueued, logged, enqueue_error, form_dict)

    def sync():
        synced.append(fr.session.user)
        # get_user() builds the permission cache lazily, for whoever is the user
        fr.local.user_perms = "perms:" + fr.session.user
        fr.local.cache["k"] = fr.session.user
        if sync_error:
            raise sync_error
        return ["added dim_x"]

    ns = {
        "frappe": fr,
        "regenerate_vars": lambda: None,
        "_apply_clickhouse_columns": lambda: [],
        "_apply_fact_tables": lambda: ([], []),
        "_sync_budget_custom_fields": sync,
    }
    return _load({"apply_schema", "apply_schema_for_publish", "queue_budget_custom_field_sync",
                  "_check_schema_role", "_apply_schema_steps", "_switch_to_administrator"}, ns)


def test_a_publish_queues_the_custom_field_sync_after_the_commit_and_never_runs_it_inline():
    enqueued, logged, synced = [], [], []
    ns = _publish_ns(enqueued, logged, synced)
    summary = ns["apply_schema_for_publish"]()
    assert synced == [], "the sync commits through updatedb: never inside the publish"
    assert [e["method"] for e in enqueued] == [JOB]
    kw = enqueued[0]["kw"]
    assert kw.get("enqueue_after_commit") is True, "enqueued now, a worker could run it before the commit"
    assert set(kw) <= _ENQUEUE_OWN, "no job kwargs: the job reads the committed dimensions"
    assert summary["budget_fields_synced"] == ["queued after commit"] and not summary["errors"]


def test_a_publish_is_not_failed_by_an_enqueue_error():
    enqueued, logged, synced = [], [], []
    ns = _publish_ns(enqueued, logged, synced, enqueue_error=ConnectionError("redis down"))
    summary = ns["apply_schema_for_publish"]()
    assert synced == [] and enqueued == []
    assert any("Budget fields" in e for e in summary["errors"]) and logged


def _assert_caller_restored(fr, form_dict):
    assert fr.session.user == "zz@example.com"
    assert fr.session.sid == "sid-of-the-request" and fr.session.data == {"csrf_token": "t"}
    assert fr.local.form_dict is form_dict
    # set_user(caller) resets the caches the sync filled as Administrator
    assert fr.local.user_perms is None and fr.local.cache == {}, (fr.local.user_perms, fr.local.cache)


def test_the_standalone_apply_schema_syncs_inline_as_administrator_and_restores_the_caller():
    """An EPM Admin may not create Custom Fields, or delete Administrator's.
    The dbt build is queued as the caller, whose session is restored whole."""
    enqueued, logged, synced = [], [], []
    ns = _publish_ns(enqueued, logged, synced, form_dict={"run_dbt": "1"})
    fr = ns["frappe"]
    form_dict = fr.local.form_dict
    summary = ns["apply_schema"](run_dbt="1")   # Frappe passes form_dict as the kwargs
    assert synced == ["Administrator"] and summary["budget_fields_synced"] == ["added dim_x"]
    _assert_caller_restored(fr, form_dict)
    assert [(e["method"], e["user"]) for e in enqueued] == [(DBT_JOB, "zz@example.com")]
    assert summary["dbt_triggered"] is True


def test_the_caller_is_restored_when_the_standalone_sync_fails():
    enqueued, logged, synced = [], [], []
    ns = _publish_ns(enqueued, logged, synced, sync_error=RuntimeError("DDL failed"))
    form_dict = ns["frappe"].local.form_dict
    summary = ns["apply_schema"]()
    assert synced == ["Administrator"] and any("Budget fields" in e for e in summary["errors"])
    _assert_caller_restored(ns["frappe"], form_dict)


def test_run_dbt_zero_queues_no_dbt_build():
    """cli_api passes cint(run_dbt); a form_dict fallback turned 0 back into
    the truthy string "0"."""
    for value in (0, "0", False, None, ""):
        enqueued, logged, synced = [], [], []
        ns = _publish_ns(enqueued, logged, synced, form_dict={"run_dbt": "0"})
        summary = ns["apply_schema"](run_dbt=value)
        assert enqueued == [] and summary["dbt_triggered"] is False, repr(value)
    for value in (1, "1", True):
        enqueued, logged, synced = [], [], []
        ns = _publish_ns(enqueued, logged, synced, form_dict={"run_dbt": "1"})
        assert ns["apply_schema"](run_dbt=value)["dbt_triggered"] is True, repr(value)


def test_the_standalone_apply_schema_is_post_only():
    """A GET rolls back at the end of the request: the sync's inserts (committed
    by updatedb) stuck while its deletes were dropped."""
    fn = _functions(SCHEMA_APPLY)["apply_schema"]
    [deco] = fn.decorator_list
    assert ast.unparse(deco) == "frappe.whitelist(methods=['POST'])"


def test_the_queued_job_path_resolves_to_a_module_function():
    fns = _functions(SCHEMA_APPLY)
    assert JOB.rsplit(".", 1)[1] in fns
    job = fns["sync_budget_custom_fields_job"]
    assert not job.args.args, "the job takes no kwargs"
    assert "_sync_budget_custom_fields" in {_call_name(c) for c in _calls(job)}


def test_the_job_syncs_as_administrator_and_waits_for_the_lock_itself():
    """It runs as the publisher, and an EPM Admin can neither create a Custom
    Field nor delete one Administrator created."""
    calls = []
    ns = {"frappe": types.SimpleNamespace(set_user=lambda u: calls.append(("set_user", u)),
                                          logger=lambda: types.SimpleNamespace(info=lambda m: None)),
          "_sync_budget_custom_fields": lambda **kw: calls.append(("sync", kw)) or []}
    ns = _load({"sync_budget_custom_fields_job"}, ns)
    ns["sync_budget_custom_fields_job"]()
    assert calls == [("set_user", "Administrator"), ("sync", {"in_job": True})]
    wait = ns["_BUDGET_FIELD_SYNC_JOB_LOCK_ATTEMPTS"] * ns["_BUDGET_FIELD_SYNC_LOCK_WAIT"]
    assert wait < 300, "the short queue's job timeout"


def test_the_sync_does_not_rely_on_frappes_job_retry():
    """v15's retry path (RetryBackgroundJobError) ends every retried job in
    AttributeError and loses its Error Log."""
    used = {n.attr for n in ast.walk(_tree(SCHEMA_APPLY)) if isinstance(n, ast.Attribute)}
    assert "RetryBackgroundJobError" not in used


def test_the_publish_path_neither_syncs_inline_nor_commits():
    fns = _functions(SCHEMA_APPLY)
    for name in ("apply_schema_for_publish", "queue_budget_custom_field_sync", "_apply_schema_steps"):
        called = {_call_name(c) for c in _calls(fns[name])}
        assert "_sync_budget_custom_fields" not in called, name
        assert "commit" not in called, name
    lifecycle = _functions(LIFECYCLE)["apply_and_rebuild"]
    called = {_call_name(c) for c in _calls(lifecycle)}
    assert "apply_schema_for_publish" in called
    assert "apply_schema" not in called and "_sync_budget_custom_fields" not in called


# --- apply_and_rebuild, run for real against a stub frappe ---

class _Touched(Exception):
    pass


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_publish(touched, enqueued):
    """apply_and_rebuild with the real konsol.schema_apply / schema_lifecycle
    (and any konsol module they import), against a frappe that records, and
    refuses, everything the Custom Field sync or a commit would touch. So an
    inline sync is caught whatever name it is called under, even through a
    helper that swallows the error (install._sync_budget_line_custom_fields)."""
    def refuse(what):
        def fn(*a, **kw):
            touched.append(what)
            raise _Touched(what)
        return fn

    def sql(query, *a, **kw):
        if "`tabBuild Approval`" in query:   # the build request's debounce read
            return []
        return refuse("db.sql " + " ".join(query.split())[:50])()

    def new_doc(doctype):
        if doctype == "Build Approval":
            return types.SimpleNamespace(insert=lambda **kw: None, name="BAPR-TEST")
        return refuse("new_doc " + doctype)()

    def get_all(doctype, **kw):
        if doctype in ("Dimension", "Dataset") and "in_budget" not in str(kw):   # steps 1-3
            return []
        return refuse("get_all " + doctype)()

    session = _D(user="zz@example.com", sid="sid", data=_D())
    frappe = types.ModuleType("frappe")
    frappe.__dict__.update(
        whitelist=lambda *a, **kw: (lambda f: f),
        enqueue=lambda method, **kw: enqueued.append((method, kw)),
        get_roles=lambda *a: ["EPM Admin"], throw=refuse("throw"), PermissionError=PermissionError,
        log_error=lambda *a, **kw: None, get_traceback=lambda: "",
        form_dict=_D(), msgprint=lambda *a, **kw: None, conf=types.SimpleNamespace(db_name="_zz"),
        utils=types.SimpleNamespace(cint=_cint),
        logger=lambda: types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                                             exception=lambda *a, **k: None),
        session=session, local=types.SimpleNamespace(session=session, form_dict=_D()),
        new_doc=new_doc, get_all=get_all, get_doc=refuse("get_doc"), delete_doc=refuse("delete_doc"),
        set_user=refuse("set_user"),
        db=types.SimpleNamespace(sql=sql, commit=refuse("db.commit"), rollback=refuse("db.rollback"),
                                 exists=refuse("db.exists")),
    )
    konsol = types.ModuleType("konsol")
    konsol.__path__ = [APP_DIR]   # other konsol modules load from source
    stubs = {
        "frappe": frappe,
        "konsol": konsol,
        "konsol.clickhouse": types.SimpleNamespace(execute=refuse("clickhouse"), get_connection=refuse("clickhouse")),
        "konsol.dbt_config": types.SimpleNamespace(regenerate_vars=lambda: None),
        "konsol.build_lock": types.SimpleNamespace(lock_build_requests=lambda: None,
                                                   flag_running_build=lambda row: None),
    }
    before = {k: v for k, v in sys.modules.items() if k.split(".")[0] in ("frappe", "konsol")}
    try:
        for key in before:
            del sys.modules[key]
        sys.modules.update(stubs)
        _module("konsol.schema_apply", SCHEMA_APPLY)
        lifecycle = _module("konsol.schema_lifecycle", LIFECYCLE)
        return lifecycle.apply_and_rebuild(types.SimpleNamespace(doctype="Dimension", name="dim_zz"), "Publish")
    finally:
        for key in [k for k in sys.modules if k.split(".")[0] in ("frappe", "konsol")]:
            del sys.modules[key]
        sys.modules.update(before)


def test_apply_and_rebuild_touches_no_custom_field_and_never_commits():
    touched, enqueued = [], []
    assert _run_publish(touched, enqueued) == "BAPR-TEST"
    assert touched == [], f"the publish's transaction reached the sync or a commit: {touched}"
    assert [m for m, _ in enqueued] == [JOB]


def test_after_migrate_re_runs_the_sync_as_the_repair_path():
    """A crash between the publish's commit and the job leaves Budget Line
    behind; the next migrate (or publish, or apply_schema) repairs it."""
    fns = _functions(INSTALL)
    after = {_call_name(c) for c in _calls(fns["after_migrate"])}
    assert "_sync_budget_line_custom_fields" in after
    helper = {_call_name(c) for c in _calls(fns["_sync_budget_line_custom_fields"])}
    assert "_sync_budget_custom_fields" in helper


# --- the sync itself: serialised, idempotent, right after publish and unpublish ---

class _Site:
    """MariaDB as the sync sees it: a named lock, and REPEATABLE READ
    transactions over Budget Line's Custom Fields. A transaction reads its
    snapshot (taken at its last commit or rollback) plus its own uncommitted
    writes; a row another session commits later stays invisible to it.
    Violations are recorded, not raised: the sync swallows a failing
    RELEASE_LOCK."""

    def __init__(self, lock=1):
        self.lock = lock          # GET_LOCK's result, or a list of results, one per call
        self.lock_calls = 0
        self.held = False
        self.fresh = False        # a new snapshot since the lock was taken
        self.committed = {}       # name -> row: what MariaDB has committed
        self.snap = {}            # this transaction's snapshot of it
        self.own = []             # uncommitted writes: ("insert", name, row) / ("delete", name)
        self.violations = []
        self.lock_names = []
        self.dimensions = {}
        self.clock = 0
        self.stamp_before_validate = True   # False in a migrate or patch
        self.fail_insert = None   # None, "before" (validate), "after" (updatedb's DDL)
        self.fail_delete = set()
        self.release_error = None
        self.logged = []
        self.exceptions = []

    # -- transactions --
    def seed(self, fieldname, creation="old"):
        self.committed[f"Budget Line-{fieldname}"] = {"fieldname": fieldname, "creation": creation}
        self.snap = dict(self.committed)

    def visible(self):
        rows = dict(self.snap)
        for write in self.own:
            if write[0] == "insert":
                rows[write[1]] = write[2]
            else:
                rows.pop(write[1], None)
        return rows

    def _new_transaction(self):
        self.own = []
        self.snap = dict(self.committed)
        if self.held:
            self.fresh = True

    def commit(self):
        for write in self.own:
            if write[0] == "insert":
                self.committed[write[1]] = write[2]
            else:
                self.committed.pop(write[1], None)
        self._new_transaction()

    def rollback(self):
        self._new_transaction()

    def commit_elsewhere(self, fieldname):
        """Another session commits the field: not in this snapshot."""
        self.committed[f"Budget Line-{fieldname}"] = {"fieldname": fieldname, "creation": "theirs"}

    # -- SQL --
    def sql(self, query, values=None, as_dict=False):
        q = " ".join(query.split())
        self.lock_names.append(values[0])
        if q.startswith("SELECT GET_LOCK"):
            self.lock_calls += 1
            result = self.lock.pop(0) if isinstance(self.lock, list) else self.lock
            self.held, self.fresh = result == 1, False
            return ((result,),)
        assert q.startswith("SELECT RELEASE_LOCK"), q
        if self.release_error:
            raise self.release_error
        if not self.held:
            self.violations.append("released a lock it does not hold")
        if self.own:
            self.violations.append(f"released with uncommitted work: {self.own}")
        self.held = False
        return ((1,),)

    def _read(self, what):
        if not self.held:
            self.violations.append(f"{what} read outside the lock")
        elif not self.fresh:
            self.violations.append(f"{what} read the snapshot from before the lock")

    def get_all(self, doctype, filters=None, fields=None, limit_page_length=None):
        self._read(doctype)
        if doctype == "Dimension":
            assert filters == {"in_budget": 1, "status": "Published"}
            return [types.SimpleNamespace(dimension_name=n, label=d["label"])
                    for n, d in self.dimensions.items()
                    if d["in_budget"] == 1 and d["status"] == "Published"]
        assert doctype == "Custom Field" and filters == {"dt": "Budget Line", "fieldname": ("like", "dim_%")}
        return [types.SimpleNamespace(name=n, fieldname=r["fieldname"]) for n, r in self.visible().items()
                if r["fieldname"].startswith("dim_")]

    def exists(self, doctype, filters):
        assert doctype == "Custom Field"
        creation = filters.get("creation")
        return next((n for n, r in self.visible().items()
                     if r["fieldname"] == filters["fieldname"]
                     and (creation is None or r["creation"] != creation[1])), None)

    def _stamp(self):
        self.clock += 1
        return f"2026-09-13 00:00:{self.clock:02d}"

    def new_doc(self, doctype):
        site = self

        class CustomField(types.SimpleNamespace):
            def insert(self):
                if not site.held:
                    site.violations.append("wrote outside the lock")
                self.creation = site._stamp() if site.stamp_before_validate else None
                name = f"{self.dt}-{self.fieldname}"
                if name in site.visible() or site.fail_insert == "before":   # validate (get_meta)
                    raise _Validation(f"A field with the name {self.fieldname} already exists")
                if name in site.committed:                                    # db_insert's unique key
                    raise _Duplicate(name)
                self.creation = self.creation or site._stamp()                # db_insert stamps it
                site.own.append(("insert", name, {"fieldname": self.fieldname, "creation": self.creation}))
                if site.fail_insert == "after":
                    raise RuntimeError("updatedb: ALTER TABLE failed")
                site.commit()                                                 # updatedb commits

        return CustomField()

    def delete_doc(self, doctype, name):
        if not self.held:
            self.violations.append("wrote outside the lock")
        if name in self.fail_delete:
            raise RuntimeError(f"delete of {name} failed")
        self.own.append(("delete", name))

    def frappe(self):
        return types.SimpleNamespace(
            db=types.SimpleNamespace(sql=self.sql, commit=self.commit, rollback=self.rollback,
                                     exists=self.exists),
            get_all=self.get_all, new_doc=self.new_doc, delete_doc=self.delete_doc,
            conf=types.SimpleNamespace(db_name="_zzdb"),
            log_error=lambda *a, **kw: self.logged.append(a),
            logger=lambda: types.SimpleNamespace(exception=lambda msg: self.exceptions.append(msg)))


def _sync(site):
    ns = _load({"_sync_budget_custom_fields", "_sync_budget_custom_fields_locked",
                "_budget_field_sync_lock", "_created_elsewhere"}, {"frappe": site.frappe()})
    return ns["_sync_budget_custom_fields"]


def _site_with(**dims):
    site = _Site()
    for name, status in dims.items():
        site.dimensions[name] = {"label": name, "in_budget": 1, "status": status}
    return site


def _fieldnames(site):
    return {r["fieldname"] for r in site.committed.values()}


def test_the_sync_is_idempotent_and_follows_publish_and_unpublish():
    site = _site_with(dim_cost_center="Published")
    site.seed("dim_cost_center")
    site.seed("other_field")   # not a dimension field: left alone
    sync = _sync(site)

    site.dimensions["dim_zz"] = {"label": "ZZ", "in_budget": 1, "status": "Published"}
    assert sync() == ["added dim_zz"]
    assert sync() == [], "a second run (a duplicate job, or after_migrate) changes nothing"

    site.dimensions["dim_zz"]["status"] = "Inactive"   # unpublished
    assert sync() == ["removed dim_zz"]
    assert sync() == []
    assert _fieldnames(site) == {"dim_cost_center", "other_field"}
    assert site.violations == [] and not site.held


def test_reads_follow_a_commit_under_the_lock_and_the_release_follows_the_last_commit():
    """The deletes don't commit themselves: released first, the next sync ran
    while they were pending. And Frappe's own reads (validate's get_meta,
    delete_doc's get_doc) are plain, so the snapshot must postdate the lock."""
    site = _site_with(dim_zz="Inactive")
    site.seed("dim_zz")
    assert _sync(site)() == ["removed dim_zz"]
    assert site.violations == [], site.violations
    assert _fieldnames(site) == set()


def test_a_failing_delete_rolls_back_before_the_lock_is_released():
    """Every inline caller catches the error and commits: without the
    rollback, the earlier delete would land outside the lock. (An ALTER
    commits implicitly, so an add whose DDL ran is committed already.)"""
    site = _site_with(dim_a="Inactive", dim_b="Inactive")
    site.seed("dim_a")
    site.seed("dim_b")
    site.fail_delete = {"Budget Line-dim_b"}
    try:
        _sync(site)()
    except RuntimeError:
        pass
    else:
        raise AssertionError("the failing delete must propagate")
    assert site.violations == [], site.violations
    site.commit()   # the caller commits after catching it
    assert _fieldnames(site) == {"dim_a", "dim_b"}, "nothing half-done committed outside the lock"


def test_a_dimension_not_in_budget_gets_no_field():
    site = _Site()
    site.dimensions["dim_business_unit"] = {"label": "BU", "in_budget": 0, "status": "Published"}
    assert _sync(site)() == [] and site.committed == {}


def _field_lands_after_the_read(site, fieldname):
    original = site.get_all

    def get_all(doctype, **kw):
        rows = original(doctype, **kw)
        if doctype == "Custom Field":
            site.commit_elsewhere(fieldname)
        return rows

    site.get_all = get_all


def test_a_field_committed_elsewhere_after_the_read_counts_as_synced():
    """It is not in this transaction's snapshot: the check must roll back
    to see it. In a migrate or patch, creation is not stamped before validate."""
    for stamped in (True, False):
        site = _site_with(dim_zz="Published")
        site.stamp_before_validate = stamped
        _field_lands_after_the_read(site, "dim_zz")
        assert _sync(site)() == [], stamped
        assert site.committed["Budget Line-dim_zz"]["creation"] == "theirs", stamped
        assert site.violations == [] and not site.held, stamped


def test_our_own_insert_failing_still_raises():
    """updatedb's DDL failed after our row was written (our creation stamp),
    or validate refused it with nobody else's row there. Passing either off
    as success would commit a field with no column, or hide the error."""
    for stamped in (True, False):
        for when in ("after", "before"):
            site = _site_with(dim_zz="Published")
            site.stamp_before_validate = stamped
            site.fail_insert = when
            try:
                _sync(site)()
            except (RuntimeError, _Validation):
                pass
            else:
                raise AssertionError(f"insert failing {when} the row (stamped={stamped}): swallowed")
            assert site.violations == [] and not site.held, (when, stamped)
            assert site.committed == {}, (when, stamped)


def test_an_inline_sync_that_cannot_get_the_lock_is_logged_and_skipped():
    for result in (0, None):   # 0: timed out; NULL: error
        site = _site_with(dim_zz="Published")
        site.lock = result
        assert _sync(site)() == [], result
        assert site.committed == {} and len(site.logged) == 1 and site.lock_calls == 1, result
        assert site.violations == [], result


def test_the_job_waits_for_the_lock_then_raises_a_plain_error():
    """Skipping would drop a publish committed after the holder read. A plain
    error, so the job runner logs and commits it (not RetryBackgroundJobError)."""
    for result in (0, None):
        site = _site_with(dim_zz="Published")
        site.lock = [result] * 3
        try:
            _sync(site)(in_job=True)
        except TimeoutError:
            pass
        else:
            raise AssertionError("a lock still busy after the job's attempts must raise")
        assert site.lock_calls == 3 and site.committed == {} and site.logged == [], result


def test_the_job_syncs_once_a_later_attempt_gets_the_lock():
    site = _site_with(dim_zz="Published")
    site.lock = [0, 1]
    assert _sync(site)(in_job=True) == ["added dim_zz"]
    assert site.lock_calls == 2 and site.violations == []


def test_the_lock_is_released_when_the_sync_fails():
    site = _site_with(dim_zz="Published")
    site.fail_insert = "after"
    try:
        _sync(site)()
    except RuntimeError:
        pass
    assert not site.held and site.violations == []


def test_a_failing_release_does_not_hide_the_sync_error():
    site = _site_with(dim_zz="Published")
    site.fail_insert = "after"
    site.release_error = ConnectionError("server has gone away")
    try:
        _sync(site)()
    except RuntimeError as e:
        assert "ALTER TABLE" in str(e)
    else:
        raise AssertionError("the sync's own error must propagate")
    assert len(site.exceptions) == 1 and "RELEASE_LOCK" in site.exceptions[0]


def test_the_lock_name_is_per_database():
    """GET_LOCK names are global to the MariaDB server."""
    site = _site_with(dim_zz="Published")
    _sync(site)()
    assert site.lock_names == ["konsol_budget_field_sync:_zzdb"] * 2
