"""konsol#255 — a declared dimension must survive the whole way to ClickHouse.

Twelve rows of konsol#255 taught two parsers to accept a ``dim_*`` column and
carry its value onto every row, and 121 tests say so. Every one of those tests
hands ``declared_dimensions`` in by hand. **No production caller did.** Both
intakes defaulted the argument to ``()``, so on a real upload the accepted set
was empty and every ``dim_*`` header was refused with "create the Dimension
dim_cost_center before a file may carry it" — while the administrator was
looking at that exact Dimension, Published and ticked. And had a value got
past that, ``_land_rows`` named no dimension in its INSERT column list, so it
would have been dropped at the last step anyway.

A unit test that passes the declared set by hand cannot see either break: that
is precisely what hid them. So every test here drives a **production entry
point** — ``TrialBalanceSubmission.on_submit`` and ``konsol.tb_bulk.run_load``
— against a stubbed frappe whose ``Dimension`` query returns a real declared
dimension, and asserts on the SQL that reaches ClickHouse. What is being
proved is the wiring between the modules, not any module's own rules; those
have their own tests.

The modules are loaded by file path with every frappe-bound import stubbed
(the pattern test_tb_bulk_model.py established), and **one** stub frappe is
shared by both, because the bulk path's last step is the single path: the job
writes one entity-period out as a single-submission CSV and submits it. A real
``TrialBalanceSubmission`` is what the job gets from ``frappe.get_doc``, so
the bulk test exercises split_table -> group_csv -> parse_tb_csv -> _land_rows
end to end rather than asserting on source text.
"""
import csv
import importlib
import importlib.util
import io
import json
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(_HERE)
TBS_PATH = os.path.join(
    APP_DIR, "consolidation", "doctype", "trial_balance_submission",
    "trial_balance_submission.py",
)
TB_BULK_PATH = os.path.join(APP_DIR, "tb_bulk.py")
TBS_MODULE = "konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission"

#: The example dimension these tests upload. konsol may not name a customer's
#: dimension (konsol#287) but a test must name one to be a test, which is why
#: konsol/tests is excluded from that guard.
DIM = "dim_cost_center"
VALUE = "CC100"

#: The site's Dimension records: the one above is Published and ticked, and a
#: second is a Draft, so a filtered read would be visible as the wrong refusal.
DECLARED = [
    {"dimension_name": DIM, "status": "Published", "in_trial_balance": 1},
    {"dimension_name": "dim_project", "status": "Draft", "in_trial_balance": 1},
]

SINGLE_CSV = (
    f"main_account,debit,credit,{DIM}\n"
    f"1010,100,0,{VALUE}\n"
    "2010,0,100,\n"
)

BULK_CSV = (
    f"data_area_id,fiscal_year,fiscal_period,main_account,debit,credit,{DIM}\n"
    f"ZZA,2099,1,1010,100,0,{VALUE}\n"
    "ZZA,2099,1,2010,0,100,\n"
)

#: Columns a raw table that has had Apply Schema run on it carries.
RAW_COLUMNS_WITH_DIM = (
    "batch_id", "data_area_id", "fiscal_year", "fiscal_period", "main_account",
    "debit_amount", "credit_amount", "description", "submission_name",
    "submitted_at", "partner_data_area_id", DIM,
)
RAW_COLUMNS_WITHOUT_DIM = RAW_COLUMNS_WITH_DIM[:-1]


class Refused(Exception):
    """frappe.throw."""


class _Doc:
    """Stand-in for frappe.model.document.Document.

    insert/submit/save are what the load job calls; submit runs the real
    controller's on_submit, which is the landing path under test. validate()
    is deliberately NOT run: its period, chart and entity gates are other
    modules' business and have their own tests.
    """

    def insert(self, *a, **k):
        return self

    def save(self, *a, **k):
        return self

    def submit(self):
        self.on_submit()
        return self

    def check_permission(self, *a, **k):
        return True


class _Site:
    """One stubbed site: its Dimension records, its files, and the SQL sent.

    ``raw_columns`` is what ClickHouse reports on the raw trial-balance table,
    so a test can put the site before or after Apply Schema added the
    dimension column.
    """

    def __init__(self, declared=DECLARED, raw_columns=RAW_COLUMNS_WITH_DIM):
        self.declared = [dict(r) for r in declared]
        self.raw_columns = list(raw_columns)
        self.sent = []
        self.files = {}
        self.dimension_queries = []
        self._n = 0

    # -- what the modules under test send ---------------------------------

    def execute(self, sql, *a, **k):
        self.sent.append(sql)
        if "system.columns" in sql:
            return "\n".join(self.raw_columns)
        return ""

    def add_file(self, content, name="tb.csv"):
        self._n += 1
        url = f"/private/files/{self._n}-{name}"
        self.files[url] = {"file_url": url, "file_name": name, "content": content}
        return url

    # -- assertions -------------------------------------------------------

    @property
    def inserts(self):
        return [s for s in self.sent if s.startswith("INSERT INTO epm_raw.trial_balance_submissions")]

    def one_insert(self):
        found = self.inserts
        assert len(found) == 1, f"expected one landing INSERT, got {len(found)}:\n" + "\n".join(self.sent)
        return found[0]


def _file_doc(site, record):
    doc = _Doc()
    doc.__dict__.update(record)
    doc.name = record["file_url"]
    doc.get_content = lambda: record["content"]
    return doc


def _frappe(site):
    """A frappe stub over `site`. Only what the two intakes actually call."""
    frappe = types.ModuleType("frappe")
    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v: int(v or 0)
    utils.strip_html = lambda v: v
    utils.get_fullname = lambda u: u
    frappe.utils = utils

    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
    frappe.flags = types.SimpleNamespace(in_install=False, in_migrate=False, in_patch=False)

    def throw(msg, exc=None, *a, **k):
        raise Refused(msg)

    frappe.throw = throw
    frappe.msgprint = lambda *a, **k: None
    frappe.log_error = lambda *a, **k: None
    frappe.clear_messages = lambda *a, **k: None
    frappe.logger = lambda *a, **k: types.SimpleNamespace(warning=lambda *a, **k: None)
    frappe.has_permission = lambda *a, **k: True
    frappe.enqueue = lambda *a, **k: None

    def get_all(doctype, filters=None, fields=None, pluck=None, limit_page_length=None, **k):
        if doctype == "Dimension":
            site.dimension_queries.append({"filters": filters, "fields": fields})
            rows = site.declared
            if filters:
                rows = [r for r in rows
                        if all(str(r.get(f)) == str(v) for f, v in filters.items())]
            return [dict(r) for r in rows]
        if doctype == "Entity":
            return ["ZZA"] if pluck else [{"name": "ZZA"}]
        if doctype == "Trial Balance Submission":
            return []
        raise AssertionError(doctype)

    frappe.get_all = get_all
    frappe.get_list = lambda doctype, **k: (["ZZA"] if k.get("pluck") else [{"name": "ZZA"}])

    def get_doc(first, second=None, **k):
        if isinstance(first, dict):
            doctype = first["doctype"]
            if doctype == "File":
                url = site.add_file(first["content"], first.get("file_name") or "tb.csv")
                return _file_doc(site, site.files[url])
            if doctype == "Trial Balance Submission":
                doc = site.tbs_class()
                doc.__dict__.update({k: v for k, v in first.items() if k != "doctype"})
                doc.name, doc.batch_id = "TBS-BULK-1", "batch-bulk-1"
                doc.row_count = 0
                return doc
            raise AssertionError(doctype)
        if first == "File":
            return _file_doc(site, site.files[second["file_url"]])
        if first == "Trial Balance Upload":
            return site.upload
        raise AssertionError(first)

    frappe.get_doc = get_doc

    db = types.SimpleNamespace()
    db.table_exists = lambda doctype: True
    db.get_value = lambda *a, **k: None
    db.get_single_value = lambda *a, **k: ""
    db.set_value = lambda *a, **k: None
    db.sql = lambda *a, **k: []
    db.commit = lambda: None
    db.rollback = lambda: None
    db.is_deadlocked = lambda e: False
    frappe.db = db
    return frappe


def _load(site, with_bulk=False):
    """Load the intake(s) against `site`, then put sys.modules back.

    ``konsol.tb_dimension`` is popped rather than stubbed: it is the module
    that reads the site's Dimension records, so it has to bind THIS frappe.
    A copy cached by another test file would read somebody else's stub.
    """
    frappe = _frappe(site)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = [APP_DIR]

    clickhouse = types.ModuleType("konsol.clickhouse")
    clickhouse.execute = site.execute
    clickhouse.ensure_raw_tables = lambda: None

    period_status = types.ModuleType("konsol.period_status")
    period_status.assert_open = lambda *a, **k: None
    period_status.assert_postable = lambda *a, **k: None
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (Exception,), {})
    period_status.period_row = lambda y, p: {"code": f"P{int(p):02d}", "type": "Regular", "status": "Open"}
    period_status.postable_types = lambda: {"Regular"}

    schema_lifecycle = types.ModuleType("konsol.schema_lifecycle")
    schema_lifecycle.check_epm_admin = lambda: None

    entity_permissions = types.ModuleType("konsol.entity_permissions")
    entity_permissions.allowed_entity_codes = lambda user=None: None

    group_chart = types.ModuleType("konsol.group_chart")
    group_chart.chart_accounts = lambda: {}

    ica = types.ModuleType("konsol.consolidation.doctype.intercompany_account.intercompany_account")
    ica.intercompany_accounts = lambda: []

    document = types.ModuleType("frappe.model.document")
    document.Document = _Doc
    model = types.ModuleType("frappe.model")
    model.document = document

    stubs = {
        "frappe": frappe, "frappe.utils": frappe.utils,
        "frappe.model": model, "frappe.model.document": document,
        "konsol": konsol, "konsol.clickhouse": clickhouse,
        "konsol.period_status": period_status,
        "konsol.schema_lifecycle": schema_lifecycle,
        "konsol.entity_permissions": entity_permissions,
        "konsol.group_chart": group_chart,
        "konsol.consolidation.doctype.intercompany_account.intercompany_account": ica,
    }
    # Every konsol submodule these two pull in fresh, so each binds the stub
    # frappe above rather than a copy another test file cached.
    reload = ("konsol.tb_dimension", "konsol.tb_dimension_model", "konsol.tb_bulk_model",
              "konsol.tb_basis_model", "konsol.consolidation",
              "konsol.consolidation.doctype",
              "konsol.consolidation.doctype.trial_balance_submission",
              "konsol.consolidation.doctype.intercompany_account", TBS_MODULE)
    saved = {k: sys.modules.get(k) for k in list(stubs) + list(reload)}
    try:
        sys.modules.update(stubs)
        for name in reload:
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(TBS_MODULE, TBS_PATH)
        tbs = importlib.util.module_from_spec(spec)
        sys.modules[TBS_MODULE] = tbs
        spec.loader.exec_module(tbs)
        site.tbs_class = tbs.TrialBalanceSubmission
        if not with_bulk:
            return tbs
        spec = importlib.util.spec_from_file_location("tb_bulk_under_test", TB_BULK_PATH)
        bulk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bulk)
        return tbs, bulk
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _submit_single(site, tbs, csv_text=SINGLE_CSV):
    """Upload `csv_text` and submit it, the way the form does."""
    doc = tbs.TrialBalanceSubmission()
    doc.batch_id, doc.name = "batch-1", "TBS-1"
    doc.data_area_id, doc.fiscal_year, doc.fiscal_period = "ZZA", 2099, 1
    doc.row_count, doc.amount_basis = 2, "Period movement"
    doc.tb_file = site.add_file(csv_text)
    doc.on_submit()
    return doc


# ── the single intake: the form ───────────────────────────────────────────

def test_a_declared_dimension_reaches_the_warehouse_from_the_form():
    """The headline. Nothing here names the declared set: the controller has
    to go and read it, the way production does."""
    site = _Site()
    tbs = _load(site)

    _submit_single(site, tbs)   # red: refused, "create the Dimension ..."

    sql = site.one_insert()
    assert f"submitted_at, {'partner_data_area_id'}, {DIM}) VALUES" in sql, sql
    assert f"'{VALUE}'" in sql, sql
    # blank is legal: a dimension is optional per row
    assert sql.count("''") >= 1, sql


def test_the_controller_reads_the_sites_dimensions_itself():
    """The break this file exists for: production passed nothing at all."""
    site = _Site()
    tbs = _load(site)
    _submit_single(site, tbs)
    assert site.dimension_queries, (
        "the controller never asked the site which dimensions it declares, so "
        "the accepted set was empty on every real upload"
    )


def test_an_undeclared_dimension_is_still_refused_from_the_form():
    """Wiring the declared set in must not turn the intake into a loader that
    accepts whatever it is given (konsol#247)."""
    site = _Site()
    tbs = _load(site)
    try:
        _submit_single(site, tbs, SINGLE_CSV.replace(DIM, "dim_made_up"))
        assert False, "expected the undeclared dimension to be refused"
    except Refused as e:
        assert "dim_made_up" in str(e) and "create the Dimension" in str(e)
    assert site.inserts == []


def test_a_draft_dimension_is_refused_for_being_draft_not_for_being_absent():
    """The reader must hand the model every Dimension row, not only the
    Published and ticked ones: filtered at the source, a Draft dimension is
    reported as "not declared at all" and the administrator is sent to create
    a record that is on their screen."""
    site = _Site()
    tbs = _load(site)
    try:
        _submit_single(site, tbs, SINGLE_CSV.replace(DIM, "dim_project"))
        assert False, "expected the Draft dimension to be refused"
    except Refused as e:
        assert "is Draft, not Published" in str(e), str(e)
        assert "create the Dimension" not in str(e), str(e)


def test_landing_is_refused_when_apply_schema_has_not_made_the_column():
    """The dim_* columns arrive by ALTER at Apply Schema time, so a Dimension
    published since the last one is accepted by the parser and has nowhere to
    land. The submission is refused by name; the values are never dropped."""
    site = _Site(raw_columns=RAW_COLUMNS_WITHOUT_DIM)
    tbs = _load(site)
    try:
        _submit_single(site, tbs)
        assert False, "expected the missing column to refuse the submission"
    except Refused as e:
        assert DIM in str(e) and "Apply Schema" in str(e), str(e)
    assert site.inserts == [], "nothing may land when a column is missing"


def test_a_file_with_no_dimension_column_sends_no_extra_query():
    """A site that declares no dimension pays nothing for this: the column
    list is unchanged and the raw table is not interrogated."""
    site = _Site(declared=[])
    tbs = _load(site)
    _submit_single(site, tbs, "main_account,debit,credit\n1010,100,0\n2010,0,100\n")
    sql = site.one_insert()
    assert "submitted_at, partner_data_area_id) VALUES" in sql, sql
    assert not [s for s in site.sent if "system.columns" in s]


# ── the bulk intake: the load job ─────────────────────────────────────────

def _run_bulk_load(site, bulk, csv_text=BULK_CSV, name="TBU-1"):
    """Run the load job over a one-entity-period upload of `csv_text`.

    ``rq`` is stubbed for the length of the call only: run_load imports
    ``rq.timeouts`` when it runs, the host has no rq, and leaving a fake one
    in sys.modules would change what the runner reports for every file after
    this one.
    """
    upload = _Doc()
    upload.name = name
    upload.upload_file = site.add_file(csv_text, "bulk.csv")
    upload.amount_basis = "Period movement"
    upload.report = json.dumps([{"entity": "ZZA", "fiscal_year": 2099,
                                 "fiscal_period": 1, "ok": True, "rows": 2}])
    upload.status = "Loading"
    site.upload = upload
    site.progress = []
    bulk._save_progress = lambda name, report, loaded, failed, status=None, error=None: (
        site.progress.append({"report": report, "loaded": loaded, "failed": failed,
                              "status": status, "error": error}))

    rq = types.ModuleType("rq")
    timeouts = types.ModuleType("rq.timeouts")
    timeouts.BaseTimeoutException = type("BaseTimeoutException", (Exception,), {})
    rq.timeouts = timeouts
    saved = {k: sys.modules.get(k) for k in ("rq", "rq.timeouts")}
    sys.modules["rq"], sys.modules["rq.timeouts"] = rq, timeouts
    try:
        bulk.run_load(upload.name)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return site.progress[-1]


def test_a_declared_dimension_reaches_the_warehouse_from_the_bulk_load():
    """The same proof for the other intake, through the real chain:
    split_table -> group_csv -> parse_tb_csv -> _land_rows."""
    site = _Site()
    tbs, bulk = _load(site, with_bulk=True)

    last = _run_bulk_load(site, bulk)   # red: "create the Dimension ..."

    assert last["failed"] == 0, last
    assert last["error"] in (None, ""), last["error"]
    sql = site.one_insert()
    assert f"submitted_at, partner_data_area_id, {DIM}) VALUES" in sql, sql
    assert f"'{VALUE}'" in sql, sql


def test_the_bulk_check_accepts_a_declared_dimension_column():
    """The check runs before anything loads; a refusal here is what the
    administrator actually saw."""
    site = _Site()
    tbs, bulk = _load(site, with_bulk=True)
    table = [row for row in csv.reader(io.StringIO(BULK_CSV))]
    groups, report = bulk._check(table, "Period movement")
    assert len(report) == 1 and report[0]["ok"], report
    rows = list(groups.values())[0]
    assert rows[0][DIM] == VALUE, rows[0]


def test_the_bulk_load_still_refuses_an_undeclared_dimension():
    site = _Site()
    tbs, bulk = _load(site, with_bulk=True)
    last = _run_bulk_load(site, bulk, BULK_CSV.replace(DIM, "dim_made_up"), name="TBU-2")
    assert site.inserts == []
    assert "dim_made_up" in (last["error"] or ""), last
