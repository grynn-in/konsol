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


class _Retry(Exception):
    """frappe.RetryBackgroundJobError."""


class _Validation(Exception):
    """frappe.ValidationError."""


def _stub_frappe(enqueued, logged, enqueue_error=None, form_dict=None):
    """A frappe for apply_schema. set_user behaves as v15's: it rewrites the
    session in place (user, sid, data) and clears form_dict."""
    session = _D(user="zz@example.com", sid="sid-of-the-request", data=_D(csrf_token="t"))
    fr = types.SimpleNamespace(
        throw=lambda msg, exc=None: (_ for _ in ()).throw(_Throw(msg)),
        PermissionError=PermissionError,
        get_roles=lambda: ["EPM Admin"],
        log_error=lambda *a, **kw: logged.append(a),
        get_traceback=lambda: "tb",
        logger=lambda: types.SimpleNamespace(info=lambda msg: None, warning=lambda msg: None),
        session=session,
        local=types.SimpleNamespace(session=session, form_dict=_D(form_dict or {})),
    )
    fr.form_dict = fr.local.form_dict

    def set_user(user):
        session.user = user
        session.sid = user
        session.data = _D()
        fr.local.form_dict = fr.form_dict = _D()

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


def test_the_standalone_apply_schema_syncs_inline_as_administrator_and_restores_the_caller():
    """An EPM Admin may not create Custom Fields, or delete Administrator's.
    set_user clears form_dict, so run_dbt is read first, and the dbt build is
    queued as the caller, whose session is restored whole."""
    enqueued, logged, synced = [], [], []
    ns = _publish_ns(enqueued, logged, synced, form_dict={"run_dbt": 1})
    fr = ns["frappe"]
    form_dict = fr.local.form_dict
    summary = ns["apply_schema"]()
    assert synced == ["Administrator"] and summary["budget_fields_synced"] == ["added dim_x"]
    assert fr.session.user == "zz@example.com"
    assert fr.session.sid == "sid-of-the-request" and fr.session.data == {"csrf_token": "t"}
    assert fr.local.form_dict is form_dict
    assert [(e["method"], e["user"]) for e in enqueued] == [(DBT_JOB, "zz@example.com")]
    assert summary["dbt_triggered"] is True


def test_the_caller_is_restored_when_the_standalone_sync_fails():
    enqueued, logged, synced = [], [], []
    ns = _publish_ns(enqueued, logged, synced, sync_error=RuntimeError("DDL failed"))
    summary = ns["apply_schema"]()
    assert synced == ["Administrator"] and any("Budget fields" in e for e in summary["errors"])
    assert ns["frappe"].session.user == "zz@example.com"
    assert ns["frappe"].session.sid == "sid-of-the-request"


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


def test_the_job_syncs_as_administrator_and_retries_a_busy_lock():
    """It runs as the publisher, and an EPM Admin can neither create a Custom
    Field nor delete one Administrator created. A busy lock is retried by the
    job runner, not skipped: the holder may have read before this commit."""
    calls = []
    ns = {"frappe": types.SimpleNamespace(set_user=lambda u: calls.append(("set_user", u)),
                                          logger=lambda: types.SimpleNamespace(info=lambda m: None)),
          "_sync_budget_custom_fields": lambda **kw: calls.append(("sync", kw)) or []}
    _load({"sync_budget_custom_fields_job"}, ns)["sync_budget_custom_fields_job"]()
    assert calls == [("set_user", "Administrator"), ("sync", {"retry_on_busy": True})]


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
        RetryBackgroundJobError=_Retry, log_error=lambda *a, **kw: None, get_traceback=lambda: "",
        form_dict=_D(), msgprint=lambda *a, **kw: None, conf=types.SimpleNamespace(db_name="_zz"),
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
    """MariaDB for the sync: a named lock, transactions, Dimension rows and
    Budget Line Custom Fields. Violations are recorded, not raised: the sync
    swallows a failing RELEASE_LOCK."""

    def __init__(self, lock=1):
        self.lock = lock          # what GET_LOCK returns: 1, 0 (timeout) or None (error)
        self.held = False
        self.fresh = False        # a commit since the lock was taken: the snapshot is current
        self.pending = []         # uncommitted writes (a delete does not commit itself)
        self.violations = []
        self.lock_names = []
        self.dimensions = {}
        self.fields = {}          # name -> {"fieldname", "creation"}
        self.clock = 0
        self.fail_insert = None   # None, "before" (validate), "after" (updatedb's DDL)
        self.release_error = None
        self.logged = []
        self.exceptions = []

    def _stamp(self):
        self.clock += 1
        return f"2026-09-13 00:00:{self.clock:02d}"

    def commit(self):
        self.pending.clear()
        if self.held:
            self.fresh = True

    def sql(self, query, values=None, as_dict=False):
        q = " ".join(query.split())
        if q.startswith("SELECT GET_LOCK"):
            self.lock_names.append(values[0])
            self.held, self.fresh = self.lock == 1, False
            return ((self.lock,),)
        assert q.startswith("SELECT RELEASE_LOCK"), q
        self.lock_names.append(values[0])
        if self.release_error:
            raise self.release_error
        if not self.held:
            self.violations.append("released a lock it does not hold")
        if self.pending:
            self.violations.append(f"released with uncommitted work: {self.pending}")
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
        return [types.SimpleNamespace(name=n, fieldname=f["fieldname"]) for n, f in self.fields.items()
                if f["fieldname"].startswith("dim_")]

    def exists(self, doctype, filters):
        assert doctype == "Custom Field" and filters["creation"][0] == "!="
        return next((n for n, f in self.fields.items()
                     if f["fieldname"] == filters["fieldname"] and f["creation"] != filters["creation"][1]),
                    None)

    def committed_elsewhere(self, fieldname):
        """Another session commits the field after this sync read."""
        self.fields[f"Budget Line-{fieldname}"] = {"fieldname": fieldname, "creation": "earlier"}

    def new_doc(self, doctype):
        site = self

        class CustomField(types.SimpleNamespace):
            def insert(self):
                if not site.held:
                    site.violations.append("wrote outside the lock")
                self.creation = site._stamp()          # set_user_and_timestamp, before validate
                name = f"{self.dt}-{self.fieldname}"
                if name in site.fields or site.fail_insert == "before":
                    raise _Validation(f"A field with the name {self.fieldname} already exists")
                site.fields[name] = {"fieldname": self.fieldname, "creation": self.creation}
                if site.fail_insert == "after":
                    raise RuntimeError("updatedb: ALTER TABLE failed")
                site.commit()                          # updatedb commits

        return CustomField()

    def delete_doc(self, doctype, name):
        if not self.held:
            self.violations.append("wrote outside the lock")
        del self.fields[name]
        self.pending.append(("delete", name))

    def frappe(self):
        return types.SimpleNamespace(
            db=types.SimpleNamespace(sql=self.sql, commit=self.commit, exists=self.exists),
            get_all=self.get_all, new_doc=self.new_doc, delete_doc=self.delete_doc,
            conf=types.SimpleNamespace(db_name="_zzdb"), RetryBackgroundJobError=_Retry,
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


def test_the_sync_is_idempotent_and_follows_publish_and_unpublish():
    site = _site_with(dim_cost_center="Published")
    site.fields["Budget Line-dim_cost_center"] = {"fieldname": "dim_cost_center", "creation": "old"}
    site.fields["Budget Line-other_field"] = {"fieldname": "other_field", "creation": "old"}   # left alone
    sync = _sync(site)

    site.dimensions["dim_zz"] = {"label": "ZZ", "in_budget": 1, "status": "Published"}
    assert sync() == ["added dim_zz"]
    assert sync() == [], "a second run (a duplicate job, or after_migrate) changes nothing"

    site.dimensions["dim_zz"]["status"] = "Inactive"   # unpublished
    assert sync() == ["removed dim_zz"]
    assert sync() == []
    assert {f["fieldname"] for f in site.fields.values()} == {"dim_cost_center", "other_field"}
    assert site.violations == [] and not site.held


def test_reads_follow_a_commit_under_the_lock_and_the_release_follows_the_last_commit():
    """The deletes don't commit themselves: released first, the next sync ran
    while they were pending. And Frappe's own reads (validate's get_meta,
    delete_doc's get_doc) are plain, so the snapshot must postdate the lock."""
    site = _site_with(dim_zz="Inactive")
    site.fields["Budget Line-dim_zz"] = {"fieldname": "dim_zz", "creation": "old"}
    assert _sync(site)() == ["removed dim_zz"]
    assert site.violations == [], site.violations


def test_a_dimension_not_in_budget_gets_no_field():
    site = _Site()
    site.dimensions["dim_business_unit"] = {"label": "BU", "in_budget": 0, "status": "Published"}
    assert _sync(site)() == [] and site.fields == {}


def test_a_field_committed_elsewhere_meanwhile_counts_as_synced():
    """validate refuses it ("already exists", a ValidationError) before
    db_insert could raise DuplicateEntryError."""
    site = _site_with(dim_zz="Published")
    original_get_all = site.get_all

    def get_all(doctype, **kw):   # the field lands right after this sync read
        rows = original_get_all(doctype, **kw)
        if doctype == "Custom Field":
            site.committed_elsewhere("dim_zz")
        return rows

    site.get_all = get_all
    assert _sync(site)() == []
    assert site.fields["Budget Line-dim_zz"]["creation"] == "earlier" and not site.held


def test_our_own_insert_failing_after_the_row_is_written_still_raises():
    """updatedb's DDL failed: the row is ours (our creation stamp). Passing it
    off as success would commit a field with no column."""
    for when in ("after", "before"):
        site = _site_with(dim_zz="Published")
        site.fail_insert = when
        try:
            _sync(site)()
        except (RuntimeError, _Validation):
            pass
        else:
            raise AssertionError(f"insert failing {when} the row: swallowed")
        assert not site.held, when


def test_an_inline_sync_that_cannot_get_the_lock_is_logged_and_skipped():
    for result in (0, None):   # 0: timed out; NULL: error
        site = _site_with(dim_zz="Published")
        site.lock = result
        assert _sync(site)() == [], result
        assert site.fields == {} and len(site.logged) == 1, result
        assert site.violations == [], result


def test_the_job_retries_when_it_cannot_get_the_lock():
    """Skipping would drop a publish committed after the holder read."""
    for result in (0, None):
        site = _site_with(dim_zz="Published")
        site.lock = result
        try:
            _sync(site)(retry_on_busy=True)
        except _Retry:
            pass
        else:
            raise AssertionError("a busy lock in the job must raise RetryBackgroundJobError")
        assert site.fields == {} and site.logged == [], result


def test_the_lock_is_released_when_the_sync_fails():
    site = _site_with(dim_zz="Published")
    site.fail_insert = "after"
    try:
        _sync(site)()
    except RuntimeError:
        pass
    assert not site.held


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
