"""`konsol.close.tb_api.check_tb`: a TB check that persists nothing (konsol#305 A19, D1).

The endpoint is loaded against a recording stub frappe. The REAL parser
(``parse_tb_csv`` in the Trial Balance Submission controller), the REAL per-row
checker (``konsol/close/tb_model.py``) and the REAL period gate
(``konsol/period_status.py``) run; only the site is faked. Every call that
writes — a document ``insert``/``save``/``submit``, ``db.set_value``,
``db.commit``, ``get_doc("File")``, ``new_doc``, ``delete_doc``, ``enqueue``,
a non-SELECT SQL statement, and a ClickHouse ``execute`` — lands in ``site.log``,
and every call to ``check_tb`` must leave that log empty, whatever it returns
or raises (Problems 2: ``tb_bulk.check_file`` inserts a Trial Balance Upload and
the client uploads a File first; this check does neither).

Entity scope is the real ``entity_permissions.assert_entity_access`` on live
(the A19 live check); here it is a stub that refuses entities outside
``site.allowed``, so the test proves the endpoint calls it before reading.
"""
import datetime
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
TB_API = os.path.join(APP_DIR, "close", "tb_api.py")
CONTROLLER = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_submission",
                          "trial_balance_submission.py")
CONTROLLER_NAME = "konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission"

ROLES = ("EPM Admin", "Entity Accountant", "System Manager")

CHART = {
    "1010": {"main_account": "1010", "is_group": 0, "is_posting": 1},
    "2010": {"main_account": "2010", "is_group": 0, "is_posting": 1},
    "4010": {"main_account": "4010", "is_group": 0, "is_posting": 1},
    "4000": {"main_account": "4000", "is_group": 1, "is_posting": 0},
}

GOOD = "main_account,debit,credit\n1010,100.50,0\n2010,0,100.50\n"
# 4001 is not in the chart (did you mean 4010?), 4000 is a heading, and the file does not balance.
BAD = "main_account,debit,credit\n4001,100,0\n4000,0,50\n"
# A stray trailing cell: parse_tb_csv raises ValueError.
MALFORMED = "main_account,debit,credit\n1010,100,0,oops\n"


class _Row(dict):
    __getattr__ = dict.get


class _Site:
    """A fake site. ``log`` records every write-shaped call."""

    def __init__(self, roles=("Entity Accountant",), allowed=None, entities=("ZZOP", "ZZB"),
                 groups=("ZZG",), period_status="Open", year_status="Open", declared=True,
                 submitted=None, chart=CHART):
        self.roles = set(roles)
        self.allowed = None if allowed is None else set(allowed)
        self.entities = list(entities)
        self.groups = list(groups)
        self.period_status = period_status
        self.year_status = year_status
        self.declared = declared
        self.submitted = dict(submitted or {})   # {(entity, fy, fp): name}
        self.chart = chart
        self.log = []
        self.access_checked = []

    def record(self, what, *args):
        self.log.append((what,) + args)

    # -- frappe ---------------------------------------------------------------

    def frappe(self):
        site = self
        frappe = types.ModuleType("frappe")

        class ValidationError(Exception):
            pass

        class PermissionError(Exception):
            pass

        frappe.ValidationError = ValidationError
        frappe.PermissionError = PermissionError
        frappe._ = lambda s: s
        frappe.whitelist = lambda *a, **k: (lambda fn: fn)
        frappe.session = types.SimpleNamespace(user="zz-ea@example.com")
        frappe.get_roles = lambda user=None: sorted(site.roles)

        def throw(msg, exc=None, *a, **k):
            raise (exc or ValidationError)(msg)

        def only_for(roles, message=False):
            roles = (roles,) if isinstance(roles, str) else tuple(roles)
            site.only_for_roles = roles
            if not site.roles & set(roles):
                raise PermissionError("Not permitted")

        class _RecordingDoc(_Row):
            def insert(self, *a, **k):
                site.record("insert", self.get("doctype"))
                return self

            def save(self, *a, **k):
                site.record("save", self.get("doctype"))
                return self

            def submit(self, *a, **k):
                site.record("submit", self.get("doctype"))
                return self

            def cancel(self, *a, **k):
                site.record("cancel", self.get("doctype"))
                return self

        def get_doc(*a, **k):
            site.record("get_doc", a[0] if a else k)
            return _RecordingDoc(doctype=a[0] if a and isinstance(a[0], str) else None)

        def new_doc(doctype, *a, **k):
            site.record("new_doc", doctype)
            return _RecordingDoc(doctype=doctype)

        def get_all(doctype, filters=None, pluck=None, **k):
            if doctype == "Entity":
                filters = filters or {}
                names = site.entities if filters.get("is_group") == 0 else site.entities + site.groups
                return list(names) if pluck else [_Row(name=n) for n in names]
            raise AssertionError(f"unexpected get_all({doctype!r})")

        def sql(query, values=None, as_dict=False, **k):
            q = " ".join(query.split())
            if not q.upper().startswith("SELECT"):
                site.record("sql", q)
                return []
            if "`tabEPM Fiscal Year Period`" in q:
                rows = [] if not site.declared else [_Row(
                    period_code="FY2099-P08", period_type="Regular",
                    start_date=datetime.date(2099, 8, 1), end_date=datetime.date(2099, 8, 31),
                    status=site.period_status)]
            elif "`tabEPM Fiscal Year`" in q:
                rows = [_Row(name="FY2099", status=site.year_status)] if values and int(values[0]) == 2099 else []
            else:
                raise AssertionError(f"unexpected query: {q}")
            return rows if as_dict else [tuple(r.values()) for r in rows]

        def get_value(doctype, filters=None, fieldname="name", *a, **k):
            if doctype == "Trial Balance Submission":
                assert filters.get("docstatus") == 1, filters
                key = (filters.get("data_area_id"), int(filters.get("fiscal_year")),
                       int(filters.get("fiscal_period")))
                return site.submitted.get(key)
            raise AssertionError(f"unexpected get_value({doctype!r})")

        def exists(doctype, filters=None, *a, **k):
            if doctype == "Entity":
                name = filters.get("name") if isinstance(filters, dict) else filters
                is_group = filters.get("is_group") if isinstance(filters, dict) else None
                if is_group == 0:
                    return name if name in site.entities else None
                return name if name in site.entities + site.groups else None
            raise AssertionError(f"unexpected exists({doctype!r})")

        frappe.throw = throw
        frappe.only_for = only_for
        frappe.get_doc = get_doc
        frappe.new_doc = new_doc
        frappe.get_all = get_all
        frappe.get_list = get_all
        frappe.delete_doc = lambda *a, **k: site.record("delete_doc", a)
        frappe.enqueue = lambda *a, **k: site.record("enqueue", a)
        frappe.db = types.SimpleNamespace(
            sql=sql,
            get_value=get_value,
            exists=exists,
            get_single_value=lambda doctype, field: 0,
            set_value=lambda *a, **k: site.record("set_value", a),
            commit=lambda: site.record("commit"),
            rollback=lambda *a, **k: site.record("rollback"),
            delete=lambda *a, **k: site.record("db.delete", a),
            table_exists=lambda *a: True,
        )
        return frappe


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_NAMES = (
    "frappe", "frappe.model", "frappe.model.document", "konsol", "konsol.clickhouse",
    "konsol.fiscal_status_model", "konsol.period_status", "konsol.tb_basis_model",
    "konsol.schema_lifecycle", "konsol.group_chart", "konsol.entity_permissions",
    "konsol.close", "konsol.close.tb_model", CONTROLLER_NAME, "konsol.close.tb_api",
)


def _load(site):
    """tb_api bound to ``site``; sys.modules is restored before returning."""
    frappe = site.frappe()
    saved = {n: sys.modules.get(n) for n in _NAMES}
    try:
        for n in _NAMES:
            sys.modules.pop(n, None)
        doc_mod = types.ModuleType("frappe.model.document")
        doc_mod.Document = type("Document", (), {})
        konsol = types.ModuleType("konsol")
        konsol.__path__ = [APP_DIR]
        close = types.ModuleType("konsol.close")
        close.__path__ = [os.path.join(APP_DIR, "close")]
        clickhouse = types.ModuleType("konsol.clickhouse")
        clickhouse.execute = lambda *a, **k: site.record("clickhouse.execute", a)
        clickhouse.ensure_raw_tables = lambda: site.record("clickhouse.ensure_raw_tables")
        group_chart = types.ModuleType("konsol.group_chart")
        group_chart.chart_accounts = lambda: dict(site.chart or {})

        def assert_entity_access(code, user=None):
            site.access_checked.append(code)
            if site.allowed is not None and code not in site.allowed:
                raise frappe.PermissionError(f"Not permitted to access entity '{code}'")

        entity_permissions = types.ModuleType("konsol.entity_permissions")
        entity_permissions.assert_entity_access = assert_entity_access
        schema_lifecycle = types.ModuleType("konsol.schema_lifecycle")
        schema_lifecycle.check_epm_admin = lambda: None
        sys.modules.update({
            "frappe": frappe, "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": doc_mod, "konsol": konsol, "konsol.close": close,
            "konsol.clickhouse": clickhouse, "konsol.group_chart": group_chart,
            "konsol.entity_permissions": entity_permissions,
            "konsol.schema_lifecycle": schema_lifecycle,
        })
        _load_path("konsol.fiscal_status_model", os.path.join(APP_DIR, "fiscal_status_model.py"))
        period_status = _load_path("konsol.period_status", os.path.join(APP_DIR, "period_status.py"))
        _load_path("konsol.tb_basis_model", os.path.join(APP_DIR, "tb_basis_model.py"))
        _load_path("konsol.close.tb_model", os.path.join(APP_DIR, "close", "tb_model.py"))
        _load_path(CONTROLLER_NAME, CONTROLLER)
        api = _load_path("konsol.close.tb_api", TB_API)
        return api, frappe, period_status
    finally:
        for n, mod in saved.items():
            if mod is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = mod


def _check(site, entity="ZZOP", fy=2099, fp=8, basis="Period movement", content=GOOD):
    api, frappe, period_status = _load(site)
    return api.check_tb(entity=entity, fiscal_year=fy, fiscal_period=fp,
                        amount_basis=basis, content=content), frappe, period_status


def _raises(site, **kwargs):
    api, frappe, period_status = _load(site)
    try:
        api.check_tb(**{"entity": "ZZOP", "fiscal_year": 2099, "fiscal_period": 8,
                        "amount_basis": "Period movement", "content": GOOD, **kwargs})
    except Exception as e:   # noqa: BLE001 - the type is asserted by the caller
        return e, frappe, period_status
    raise AssertionError("check_tb did not raise")


# -- the happy path -------------------------------------------------------------

def test_a_good_file_is_ok_and_writes_nothing():
    site = _Site(allowed={"ZZOP"})
    result, _, _ = _check(site)
    assert result["ok"] is True, result
    assert result["file_problems"] == []
    assert [r["line"] for r in result["rows"]] == [2, 3]
    assert all(r["problems"] == [] for r in result["rows"])
    assert result["totals"] == {"debit": 100.5, "credit": 100.5, "difference": 0.0}
    assert result["period_problem"] is None
    assert result["replaces"] is None
    assert site.access_checked == ["ZZOP"]
    assert site.log == []


def test_a_bom_is_stripped():
    site = _Site()
    result, _, _ = _check(site, content="﻿" + GOOD)
    assert result["ok"] is True, result
    assert result["rows"][0]["main_account"] == "1010"
    assert site.log == []


def test_bytes_content_with_a_bom_is_decoded():
    site = _Site()
    result, _, _ = _check(site, content=("﻿" + GOOD).encode("utf-8"))
    assert result["ok"] is True, result
    assert site.log == []


# -- per-row problems -------------------------------------------------------------

def test_a_bad_file_reports_per_row_problems_and_writes_nothing():
    site = _Site()
    result, _, _ = _check(site, content=BAD)
    assert result["ok"] is False
    first, second = result["rows"]
    assert first["line"] == 2
    assert first["problems"][0]["message"] == "Account 4001 is not in the group chart"
    assert first["problems"][0]["suggestion"] == "Did you mean 4010?"
    assert second["problems"][0]["code"] == "HEADING_ACCOUNT"
    assert any("do not equal credits" in p for p in result["file_problems"]), result["file_problems"]
    assert site.log == []


def test_a_partner_is_checked_against_non_group_entities():
    site = _Site()
    csv = "main_account,debit,credit,partner_data_area_id\n1010,10,0,zzb\n2010,0,10,ZZG\n"
    result, _, _ = _check(site, content=csv)
    assert result["ok"] is False
    first, second = result["rows"]
    assert first["problems"][0]["code"] == "UNKNOWN_PARTNER"
    assert first["problems"][0]["suggestion"] == "Did you mean ZZB?"
    # a group entity is not a partner
    assert second["problems"][0]["code"] == "UNKNOWN_PARTNER"
    assert site.log == []


def test_a_basis_cell_that_contradicts_the_form_is_a_row_problem():
    site = _Site()
    csv = "main_account,debit,credit,amount_basis\n1010,10,0,Period-end balance\n2010,0,10,\n"
    result, _, _ = _check(site, content=csv, basis="Period movement")
    assert result["ok"] is False
    assert result["rows"][0]["problems"][0]["code"] == "AMOUNT_BASIS"
    assert result["rows"][1]["problems"] == []
    assert site.log == []


def test_no_published_chart_is_a_file_problem():
    site = _Site(chart={})
    result, _, _ = _check(site)
    assert result["ok"] is False
    assert any("No group chart is published yet" in p for p in result["file_problems"])
    assert site.log == []


# -- failure paths ------------------------------------------------------------------

def test_a_malformed_csv_is_a_file_problem_not_a_throw():
    site = _Site()
    result, _, _ = _check(site, content=MALFORMED)
    assert result["ok"] is False
    assert result["rows"] == []
    assert len(result["file_problems"]) >= 1
    assert result["file_problems"][0].startswith("Could not read the trial balance file: "), result
    assert site.log == []


def test_an_empty_file_is_a_file_problem():
    site = _Site()
    for content in ("", None):
        result, _, _ = _check(site, content=content)
        assert result["ok"] is False
        assert "The file is empty" in result["file_problems"][0], result
    assert site.log == []


def test_an_entity_the_user_cannot_access_is_refused():
    site = _Site(allowed={"ZZOP"})
    err, frappe, _ = _raises(site, entity="ZZB")
    assert isinstance(err, frappe.PermissionError), repr(err)
    assert site.access_checked == ["ZZB"]
    assert site.log == []


def test_an_unknown_or_group_entity_is_refused():
    for entity in ("ZZNOPE", "ZZG"):
        site = _Site()
        err, frappe, _ = _raises(site, entity=entity)
        assert isinstance(err, frappe.ValidationError), repr(err)
        assert entity in str(err)
        assert site.log == []


def test_a_role_outside_the_gate_is_refused():
    for roles in (("EPM Analyst",), ("EPM User",), ()):
        site = _Site(roles=roles)
        err, frappe, _ = _raises(site)
        assert isinstance(err, frappe.PermissionError), (roles, repr(err))
        assert site.access_checked == []
        assert site.log == []


def test_the_gate_names_the_three_roles():
    site = _Site(roles=("EPM Admin",))
    _check(site)
    assert set(site.only_for_roles) == set(ROLES)


def test_an_undeclared_period_raises_period_not_declared():
    site = _Site(declared=False)
    err, _, period_status = _raises(site)
    assert isinstance(err, period_status.PeriodNotDeclared), repr(err)
    site = _Site()
    err, _, period_status = _raises(site, fiscal_year=2098)
    assert isinstance(err, period_status.PeriodNotDeclared), repr(err)
    assert site.log == []


def test_a_closed_period_sets_period_problem_without_throwing():
    for period_status, year_status, word in (("Closed", "Open", "Closed"),
                                             ("Locked", "Open", "Locked"),
                                             ("Open", "Closed", "Closed")):
        site = _Site(period_status=period_status, year_status=year_status)
        result, _, _ = _check(site)
        assert result["period_problem"] == (
            f"FY2099 P8 is {word}: a trial balance can't be submitted"), result["period_problem"]
        # the file itself is still judged
        assert result["ok"] is True
        assert site.log == []


# -- replaces -----------------------------------------------------------------------

def test_an_existing_submitted_tb_is_named_in_replaces():
    site = _Site(submitted={("ZZOP", 2099, 8): "TBS-0001", ("ZZOP", 2099, 7): "TBS-0000"})
    result, _, _ = _check(site, fy="2099", fp="8")
    assert result["replaces"] == "TBS-0001"
    assert site.log == []


# -- the recording stub itself can fail -------------------------------------------------

def test_the_recording_stub_catches_a_write():
    """A check that could not fail is not evidence: prove the log sees writes."""
    site = _Site()
    frappe = site.frappe()
    frappe.get_doc({"doctype": "File"}).insert()
    frappe.db.commit()
    frappe.db.sql("DELETE FROM `tabFile`")
    assert [e[0] for e in site.log] == ["get_doc", "insert", "commit", "sql"]


def test_tb_api_is_post_only_and_gated_in_source():
    with open(TB_API) as fh:
        source = fh.read()
    assert '@frappe.whitelist(methods=["POST"])\ndef check_tb(' in source
    assert "from konsol.entity_permissions import assert_entity_access" in source


# ================================================================================
# submit_tb (konsol#305 A24): upload a corrected file = cancel + submit in one step
# ================================================================================
#
# The same recording site, with documents that record the write they stand for.
# Every gate that refuses must leave the log empty; the happy path's log is the
# order of work; a failure after the cancel must end with the new batch's claim
# deleted and the old batch re-claimed (Problems 3: a MariaDB rollback puts the
# old document back to docstatus 1, but not its ClickHouse claim).

OLD = {"name": "TBS-ZZOP-2099-P8-001", "batch_id": "b-old", "data_area_id": "ZZOP",
       "fiscal_year": 2099, "fiscal_period": 8, "row_count": 2,
       "amount_basis": "Period movement", "docstatus": 1}


class _Boom(Exception):
    pass


class _SubmitSite(_Site):
    """``fail`` names the step that raises: "insert" (the new TB's validate),
    "submit" (after its claim landed), "cancel"; ``fail_execute`` makes every
    ClickHouse statement raise (the restore itself fails)."""

    def __init__(self, fail=None, fail_execute=False, on_behalf="No", docs=None, **kw):
        super().__init__(**kw)
        self.fail = fail
        self.fail_execute = fail_execute
        self.on_behalf = on_behalf
        self.docs = dict(docs or {OLD["name"]: OLD})
        self.files = []

    def frappe(self):
        frappe = super().frappe()
        site = self

        class _Doc(_Row):
            pass

        class _Old(_Doc):
            def cancel(self, *a, **k):
                site.record("cancel", self.name)
                if site.fail == "cancel":
                    raise frappe.ValidationError("Cannot cancel a trial balance submission")
                self["docstatus"] = 2
                return self

        class _File(_Doc):
            def insert(self, *a, **k):
                site.record("insert", "File")
                self["file_url"] = "/private/files/" + self["file_name"]
                site.files.append(dict(self))
                return self

        class _New(_Doc):
            def insert(self, *a, **k):
                site.record("insert", "Trial Balance Submission", self.get("amended_from"))
                if site.fail == "insert":
                    raise frappe.ValidationError("Trial balance failed validation: injected")
                self["name"] = "TBS-ZZOP-2099-P8-001-1" if self.get("amended_from") else "TBS-ZZOP-2099-P8-002"
                self["batch_id"] = "b-new"
                self["uploaded_on_behalf"] = site.on_behalf
                return self

            def submit(self, *a, **k):
                site.record("submit", self.name)
                if site.fail == "submit":
                    # the controller claimed the batch, then something after it raised
                    site.record("claimed", self.batch_id)
                    raise _Boom("the Build Approval hook raised")
                self["docstatus"] = 1
                return self

        def get_doc(*a, **k):
            if a and isinstance(a[0], dict):
                spec = dict(a[0])
                if spec.get("doctype") == "File":
                    return _File(spec)
                if spec.get("doctype") == "Trial Balance Submission":
                    return _New(spec)
                raise AssertionError(f"unexpected get_doc({spec!r})")
            if a and a[0] == "Trial Balance Submission":
                return _Old(site.docs[a[1]])
            # the stub's own recording get_doc (the recording-stub self test)
            site.record("get_doc", a[0] if a else k)
            return _Row(doctype=a[0] if a and isinstance(a[0], str) else None)

        frappe.get_doc = get_doc
        return frappe


def _execute_recorder(site):
    def execute(sql, *a, **k):
        site.record("clickhouse.execute", " ".join(sql.split()))
        if site.fail_execute:
            raise _Boom("ClickHouse is down")
    return execute


def _submit(site, **kwargs):
    api, frappe, period_status = _load(site)
    api.execute = _execute_recorder(site)
    args = {"entity": "ZZOP", "fiscal_year": 2099, "fiscal_period": 8,
            "amount_basis": "Period movement", "content": GOOD, "replaces": None}
    args.update(kwargs)
    return api.submit_tb(**args), frappe, period_status


def _submit_raises(site, **kwargs):
    try:
        result, _, _ = _submit(site, **kwargs)
    except Exception as e:   # noqa: BLE001 - the type is asserted by the caller
        return e
    raise AssertionError(f"submit_tb did not raise: {result!r}")


# -- happy paths ---------------------------------------------------------------------

def test_submit_replaces_in_order_cancel_file_insert_submit():
    site = _SubmitSite(submitted={("ZZOP", 2099, 8): OLD["name"]})
    result, _, _ = _submit(site, replaces=OLD["name"])
    assert [e[:2] for e in site.log] == [
        ("cancel", OLD["name"]),
        ("insert", "File"),
        ("insert", "Trial Balance Submission"),
        ("submit", "TBS-ZZOP-2099-P8-001-1"),
    ], site.log
    # amended_from names the old TB
    assert site.log[2] == ("insert", "Trial Balance Submission", OLD["name"])
    assert result == {"name": "TBS-ZZOP-2099-P8-001-1", "replaced": OLD["name"],
                      "on_behalf": False}
    # the endpoint itself writes nothing to ClickHouse on success: the controller claims
    assert not any(e[0] == "clickhouse.execute" for e in site.log)


def test_a_first_submit_cancels_nothing_and_amends_nothing():
    site = _SubmitSite()
    result, _, _ = _submit(site)
    assert [e[:2] for e in site.log] == [
        ("insert", "File"), ("insert", "Trial Balance Submission"),
        ("submit", "TBS-ZZOP-2099-P8-002")], site.log
    assert site.log[1] == ("insert", "Trial Balance Submission", None)
    assert result == {"name": "TBS-ZZOP-2099-P8-002", "replaced": None, "on_behalf": False}


def test_the_file_is_private_named_for_the_period_and_holds_the_text():
    site = _SubmitSite()
    _submit(site, content="﻿" + GOOD)
    (f,) = site.files
    assert f["is_private"] == 1
    assert f["file_name"] == "ZZOP-FY2099-P8.csv"
    assert f["content"] == GOOD
    # the new TB reads the file back through its url (the controller's _parse_file)


def test_the_new_tb_carries_the_form_values_and_the_file_url():
    site = _SubmitSite()
    seen = {}
    api, frappe, _ = _load(site)
    api.execute = _execute_recorder(site)
    real_get_doc = frappe.get_doc

    def spy(*a, **k):
        doc = real_get_doc(*a, **k)
        if a and isinstance(a[0], dict) and a[0].get("doctype") == "Trial Balance Submission":
            seen.update(a[0])
        return doc

    frappe.get_doc = spy
    api.submit_tb(entity="ZZOP", fiscal_year="2099", fiscal_period="8",
                  amount_basis="Period movement", content=GOOD)
    assert seen["data_area_id"] == "ZZOP"
    assert seen["fiscal_year"] == 2099 and seen["fiscal_period"] == 8
    assert seen["amount_basis"] == "Period movement"
    assert seen["tb_file"] == "/private/files/ZZOP-FY2099-P8.csv"
    assert "uploaded_on_behalf" not in seen   # the server sets it (A18), never the request


def test_on_behalf_is_what_the_server_recorded():
    for flag, expected in (("Yes", True), ("No", False), ("", None)):
        site = _SubmitSite(on_behalf=flag, roles=("EPM Admin",))
        result, _, _ = _submit(site)
        assert result["on_behalf"] is expected, (flag, result)


def test_the_request_cannot_send_on_behalf():
    import inspect
    api, _, _ = _load(_SubmitSite())
    params = inspect.signature(api.submit_tb).parameters
    assert "on_behalf" not in params and "uploaded_on_behalf" not in params
    assert not any(p.kind == p.VAR_KEYWORD for p in params.values())


# -- refusals before anything is written ----------------------------------------------

def test_submit_in_a_period_that_is_not_open_is_refused_and_writes_nothing():
    for period_status, year_status in (("Closed", "Open"), ("Locked", "Open"), ("Open", "Closed")):
        site = _SubmitSite(period_status=period_status, year_status=year_status,
                           submitted={("ZZOP", 2099, 8): OLD["name"]})
        err = _submit_raises(site, replaces=OLD["name"])
        assert "Cannot submit a trial balance" in str(err), str(err)
        assert site.log == [], site.log


def test_submit_of_an_undeclared_period_is_refused_and_writes_nothing():
    site = _SubmitSite(declared=False)
    err = _submit_raises(site)
    assert type(err).__name__ == "PeriodNotDeclared", repr(err)
    assert site.log == []


def test_a_failed_check_is_refused_with_the_first_problems_and_writes_nothing():
    site = _SubmitSite(submitted={("ZZOP", 2099, 8): OLD["name"]})
    err = _submit_raises(site, content=BAD, replaces=OLD["name"])
    msg = str(err)
    assert "Line 2: Account 4001 is not in the group chart" in msg, msg
    assert "do not equal credits" in msg, msg
    assert site.log == [], site.log


def test_an_unreadable_file_is_refused_and_writes_nothing():
    for content in (MALFORMED, "", None):
        site = _SubmitSite()
        err = _submit_raises(site, content=content)
        assert "trial balance" in str(err).lower(), str(err)
        assert site.log == [], (content, site.log)


def test_a_stale_replaces_is_refused_and_writes_nothing():
    cases = (
        ({("ZZOP", 2099, 8): OLD["name"]}, "TBS-ZZOP-2099-P8-000"),  # another TB is live now
        ({("ZZOP", 2099, 8): OLD["name"]}, None),                    # one appeared since the check
        ({}, OLD["name"]),                                            # the checked one was cancelled
    )
    for submitted, replaces in cases:
        site = _SubmitSite(submitted=submitted)
        err = _submit_raises(site, replaces=replaces)
        assert str(err) == ("The trial balance changed since you checked it; "
                            "check the file again."), str(err)
        assert site.log == [], site.log


def test_an_empty_replaces_means_none():
    site = _SubmitSite()
    result, _, _ = _submit(site, replaces="")
    assert result["replaced"] is None


def test_submit_refuses_an_entity_outside_scope_and_writes_nothing():
    site = _SubmitSite(allowed={"ZZOP"})
    err = _submit_raises(site, entity="ZZB")
    assert type(err).__name__ == "PermissionError", repr(err)
    assert site.access_checked == ["ZZB"]
    assert site.log == []


def test_submit_refuses_a_group_or_unknown_entity_and_writes_nothing():
    for entity in ("ZZG", "ZZNOPE"):
        site = _SubmitSite()
        err = _submit_raises(site, entity=entity)
        assert entity in str(err)
        assert site.log == []


def test_submit_refuses_roles_outside_the_gate():
    for roles in (("EPM Analyst",), ("EPM User",), ()):
        site = _SubmitSite(roles=roles)
        err = _submit_raises(site)
        assert type(err).__name__ == "PermissionError", (roles, repr(err))
        assert site.access_checked == []
        assert site.log == []
    site = _SubmitSite(roles=("EPM Admin",))
    _submit(site)
    assert set(site.only_for_roles) == set(ROLES)


# -- a failure after the cancel restores the old claim -------------------------------------

def _claim_of_old(entry):
    return (entry[0] == "clickhouse.execute"
            and entry[1].startswith("INSERT INTO epm_raw.trial_balance_submission_control")
            and "'b-old'" in entry[1] and f"'{OLD['name']}'" in entry[1]
            and "'Period movement'" in entry[1])


def _delete_of_new(entry):
    return (entry[0] == "clickhouse.execute"
            and entry[1].startswith("ALTER TABLE epm_raw.trial_balance_submission_control DELETE")
            and "batch_id = 'b-new'" in entry[1] and "mutations_sync = 1" in entry[1])


def test_a_failing_new_submit_reclaims_the_old_batch_and_propagates():
    site = _SubmitSite(fail="submit", submitted={("ZZOP", 2099, 8): OLD["name"]})
    err = _submit_raises(site, replaces=OLD["name"])
    assert isinstance(err, _Boom) and str(err) == "the Build Approval hook raised", repr(err)
    steps = [e[:2] for e in site.log]
    assert steps[:5] == [("cancel", OLD["name"]), ("insert", "File"),
                         ("insert", "Trial Balance Submission"),
                         ("submit", "TBS-ZZOP-2099-P8-001-1"), ("claimed", "b-new")], site.log
    # then: the new batch's claim is deleted, and the old batch is claimed again
    assert len(site.log) == 7, site.log
    assert _delete_of_new(site.log[5]), site.log[5]
    assert _claim_of_old(site.log[6]), site.log[6]


def test_a_failing_new_validate_reclaims_the_old_batch_and_propagates():
    site = _SubmitSite(fail="insert", submitted={("ZZOP", 2099, 8): OLD["name"]})
    err = _submit_raises(site, replaces=OLD["name"])
    assert "injected" in str(err), repr(err)
    assert [e[:2] for e in site.log][:3] == [("cancel", OLD["name"]), ("insert", "File"),
                                            ("insert", "Trial Balance Submission")]
    # nothing of the new batch was claimed: only the old claim is re-inserted
    ch = [e for e in site.log if e[0] == "clickhouse.execute"]
    assert len(ch) == 1 and _claim_of_old(ch[0]), site.log


def test_a_failing_first_submit_reclaims_nothing():
    site = _SubmitSite(fail="submit")
    err = _submit_raises(site)
    assert isinstance(err, _Boom)
    ch = [e for e in site.log if e[0] == "clickhouse.execute"]
    assert len(ch) == 1 and _delete_of_new(ch[0]), site.log
    assert not any(_claim_of_old(e) for e in site.log)


def test_a_failing_cancel_reclaims_the_old_batch_too():
    """on_cancel may have deleted the claim before a later cancel hook raised."""
    site = _SubmitSite(fail="cancel", submitted={("ZZOP", 2099, 8): OLD["name"]})
    err = _submit_raises(site, replaces=OLD["name"])
    assert "Cannot cancel" in str(err)
    assert [e[:2] for e in site.log][0] == ("cancel", OLD["name"])
    ch = [e for e in site.log if e[0] == "clickhouse.execute"]
    assert len(ch) == 1 and _claim_of_old(ch[0]), site.log


def test_a_failed_restore_is_not_silent():
    site = _SubmitSite(fail="submit", fail_execute=True,
                       submitted={("ZZOP", 2099, 8): OLD["name"]})
    err = _submit_raises(site, replaces=OLD["name"])
    msg = str(err)
    assert "the Build Approval hook raised" in msg, msg
    assert OLD["name"] in msg and "not claimed in the warehouse" in msg, msg
    assert "b-new" in msg, msg


def test_submit_tb_is_post_only_in_source():
    with open(TB_API) as fh:
        source = fh.read()
    assert '@frappe.whitelist(methods=["POST"])\ndef submit_tb(' in source
