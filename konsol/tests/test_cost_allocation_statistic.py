"""konsol#269 K5a: the Cost Allocation Statistic doctype (plan section 2).

Shape tests read the JSON; behaviour tests load the controller by file path
against a stub frappe (the pattern of test_business_combination.py): the stub
records the locking query, the period_status calls and the syncs.

Decided by Deepak Pai on konsol#269 (14 Nov 2026): the closed-period cancel gate
applies only when the statistic's scenario has scenario_type 'actual'.
"""
import ast
import importlib.util
import json
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EPM_DOCTYPES = os.path.join(APP_DIR, "epm", "doctype")
STAT = "cost_allocation_statistic"
VALUE = "cost_allocation_statistic_value"
LAYOUT_TYPES = {"Tab Break", "Section Break", "Column Break"}

STAT_FIELDS = [
    ("driver", "Link", 1, 0),
    ("dimension", "Link", 0, 1),
    ("entity", "Link", 1, 0),
    ("scenario", "Link", 1, 0),
    ("fiscal_year", "Int", 1, 0),
    ("status", "Select", 0, 1),
    ("amended_from", "Link", 0, 1),
    ("values_file", "Attach", 0, 0),
    ("row_count", "Int", 0, 1),
    ("total", "Float", 0, 1),
    ("values", "Table", 1, 0),
]

VALUE_FIELDS = [
    ("fiscal_period", "Int", 1),
    ("member", "Data", 1),
    ("value", "Float", 1),
]


def _doc(folder):
    with open(os.path.join(EPM_DOCTYPES, folder, folder + ".json")) as f:
        return json.load(f)


def _by_name(folder):
    return {f["fieldname"]: f for f in _doc(folder)["fields"]}


def _workflow():
    with open(os.path.join(EPM_DOCTYPES, STAT, STAT + "_workflow.json")) as f:
        return json.load(f)


# -- shape ---------------------------------------------------------------------

def test_statistic_folders_are_packages_with_json_and_controller():
    for folder in (STAT, VALUE):
        base = os.path.join(EPM_DOCTYPES, folder)
        for name in ("__init__.py", folder + ".json", folder + ".py"):
            assert os.path.exists(os.path.join(base, name)), f"{folder}/{name} missing"


def test_statistic_header_is_a_submittable_epm_doctype():
    doc = _doc(STAT)
    assert doc["name"] == "Cost Allocation Statistic"
    assert doc["module"] == "EPM"
    assert doc["is_submittable"] == 1
    assert doc.get("istable", 0) == 0
    assert doc["track_changes"] == 1
    assert doc["autoname"] == "format:CAS-{#####}"
    assert doc["title_field"] == "driver"


def test_statistic_tabs_and_sections():
    doc = _doc(STAT)
    tabs = [f["label"] for f in doc["fields"] if f["fieldtype"] == "Tab Break"]
    sections = [f["label"] for f in doc["fields"] if f["fieldtype"] == "Section Break"]
    assert tabs == ["Statistic", "Values"]
    assert sections == ["Scope", "Upload", "Values"]


def test_statistic_fields_types_required_and_read_only():
    got = [(f["fieldname"], f["fieldtype"], f.get("reqd", 0), f.get("read_only", 0))
           for f in _doc(STAT)["fields"] if f["fieldtype"] not in LAYOUT_TYPES]
    assert got == STAT_FIELDS


def test_statistic_links_options_and_help_text():
    fields = _by_name(STAT)
    assert fields["driver"]["options"] == "Cost Allocation Driver"
    assert fields["dimension"]["options"] == "Dimension"
    assert fields["dimension"]["fetch_from"] == "driver.dimension"
    assert fields["entity"]["options"] == "Entity"
    assert fields["scenario"]["options"] == "Scenario"
    assert fields["amended_from"]["options"] == "Cost Allocation Statistic"
    assert fields["values"]["options"] == "Cost Allocation Statistic Value"
    assert fields["status"]["options"] == "Draft\nPending Approval\nApproved\nCancelled"
    assert fields["status"]["default"] == "Draft"
    for name in ("driver", "dimension", "entity", "scenario", "fiscal_year", "values_file",
                 "row_count", "total", "values"):
        assert fields[name].get("description"), f"{name} has no help text"


def test_statistic_value_child_shape():
    doc = _doc(VALUE)
    assert doc["name"] == "Cost Allocation Statistic Value"
    assert doc["istable"] == 1
    assert doc["module"] == "EPM"
    got = [(f["fieldname"], f["fieldtype"], f.get("reqd", 0)) for f in doc["fields"]]
    assert got == VALUE_FIELDS
    value = {f["fieldname"]: f for f in doc["fields"]}["value"]
    assert value.get("non_negative") == 1


def test_statistic_approver_holds_submit_and_cancel():
    perms = {p["role"]: p for p in _doc(STAT)["permissions"]}
    assert perms["EPM Admin"].get("submit") == 1
    assert perms["EPM Admin"].get("cancel") == 1
    assert not perms["EPM Analyst"].get("submit")


def test_statistic_workflow_and_install():
    wf = _workflow()
    assert wf["document_type"] == "Cost Allocation Statistic"
    assert wf["workflow_state_field"] == "status"
    states = {s["state"]: (int(s["doc_status"]), s["allow_edit"]) for s in wf["states"]}
    assert states == {
        "Draft": (0, "EPM Analyst"),
        "Pending Approval": (0, "EPM Admin"),
        "Approved": (1, "EPM Admin"),
        "Cancelled": (2, "EPM Admin"),
    }
    transitions = [(t["state"], t["action"], t["next_state"], t["allowed"]) for t in wf["transitions"]]
    assert transitions == [
        ("Draft", "Send for Approval", "Pending Approval", "EPM Analyst"),
        ("Pending Approval", "Reject", "Draft", "EPM Admin"),
        ("Pending Approval", "Approve", "Approved", "EPM Admin"),
        ("Approved", "Cancel", "Cancelled", "EPM Admin"),
    ]
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        installed = re.findall(r'"([^"]+)"', f.read().split("INSTALLED = (")[1].split(")")[0])
    assert "Cost Allocation Statistic" in installed


def test_statistic_controllers_define_their_classes():
    for folder, cls in ((STAT, "CostAllocationStatistic"), (VALUE, "CostAllocationStatisticValue")):
        with open(os.path.join(EPM_DOCTYPES, folder, folder + ".py")) as f:
            names = [n.name for n in ast.parse(f.read()).body if isinstance(n, ast.ClassDef)]
        assert names == [cls], (folder, names)


# -- behaviour -----------------------------------------------------------------

class Refused(Exception):
    pass


class _Doc:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def set(self, key, value):
        setattr(self, key, value)

    def append(self, key, row):
        getattr(self, key).append(_Row(row))

    def check_permission(self, perm):
        self.permission_checked = perm

    def save(self):
        self.saved = True
        self.validate()

    def get(self, key, default=None):
        return getattr(self, key, default)


class _Row(dict):
    __getattr__ = dict.get


class _World:
    """In-memory site: dimension members, scenario types, other statistics."""

    def __init__(self):
        self.members = {"ZZDIM": ["ZZIT", "ZZHR", "ZZP1"]}
        self.scenario_types = {"ZZACT": "actual", "ZZBUD": "budget"}
        self.driver_dimension = {"ZZ Headcount": "ZZDIM"}
        self.other_rows = []          # rows of other submitted statistics
        self.sql = []
        self.open_calls = []
        self.declared_calls = []
        self.undeclared = set()
        self.closed = set()
        self.syncs = []
        self.after_commit = []
        self.docs = {}                # name -> statistic under load
        self.file_content = b""
        self.file_reads = []


def _load(world):
    frappe = types.ModuleType("frappe")

    def throw(msg, *a, **k):
        raise Refused(msg)

    frappe.throw = throw
    frappe._ = lambda s: s
    def whitelist(*a, **k):
        def deco(fn):
            fn.whitelist_kwargs = k
            return fn
        return deco

    frappe.whitelist = whitelist
    frappe.ValidationError = type("ValidationError", (Exception,), {})

    def get_value(doctype, name, field=None, *a, **k):
        if doctype == "Scenario":
            return world.scenario_types.get(name)
        if doctype == "Cost Allocation Driver":
            return world.driver_dimension.get(name)
        raise AssertionError(doctype)

    def get_all(doctype, filters=None, fields=None, pluck=None, **k):
        # KF2: members come from the shared helper, never frappe.get_all (v16's
        # virtual get_all pages at 20 and checks the user's read permission).
        raise AssertionError(f"frappe.get_all({doctype!r}) is not allowed here")

    def sql(query, values=None, as_dict=False, **k):
        world.sql.append(query)
        return [_Row(r) for r in world.other_rows]

    frappe.db = types.SimpleNamespace(get_value=get_value, sql=sql)
    frappe.get_all = get_all

    def get_doc(doctype, name=None):
        if doctype == "File":
            world.file_reads.append(name)
            return types.SimpleNamespace(get_content=lambda: world.file_content)
        assert doctype == "Cost Allocation Statistic", doctype
        return world.docs[name]

    frappe.get_doc = get_doc
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = _Doc
    ch = types.ModuleType("konsol.clickhouse")
    ch.sync_doctype_after_commit = lambda *a, **k: world.syncs.append(a)
    ps = types.ModuleType("konsol.period_status")

    def assert_open(year, period, action="run"):
        world.open_calls.append((year, period))
        if (year, period) in world.closed:
            raise Refused(f"period {period} of FY{year} is closed")

    def assert_declared(year, period):
        world.declared_calls.append((year, period))
        if (year, period) in world.undeclared:
            raise Refused(f"period {period} of FY{year} not declared")

    ps.assert_open = assert_open
    ps.assert_declared = assert_declared
    stubs = {"frappe": frappe, "frappe.model": model, "frappe.model.document": document,
             "konsol.clickhouse": ch, "konsol.period_status": ps}
    konsol_pkg = sys.modules.get("konsol")
    if konsol_pkg is None or not hasattr(konsol_pkg, "__path__"):
        stubs["konsol"] = types.ModuleType("konsol")
        stubs["konsol"].__path__ = [APP_DIR]
    saved = {k: sys.modules.get(k) for k in stubs}
    for name in [n for n in sys.modules
                 if n.startswith("konsol.epm.doctype.cost_allocation_statistic")]:
        del sys.modules[name]
    sys.modules.update(stubs)
    try:
        path = os.path.join(EPM_DOCTYPES, STAT, STAT + ".py")
        spec = importlib.util.spec_from_file_location("cas_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # the shared member helper is covered in test_cost_allocation_members.py
        mod._dimension_members = lambda dimension: set(world.members.get(dimension, []))
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _stat(world, mod, rows=None, **over):
    base = dict(name="CAS-00001", driver="ZZ Headcount", entity="ZZOP", scenario="ZZACT",
                fiscal_year=2026, dimension=None, doctype="Cost Allocation Statistic",
                values=[_Row(r) for r in (rows if rows is not None else [
                    {"fiscal_period": 1, "member": "ZZIT", "value": 10.0},
                    {"fiscal_period": 1, "member": "ZZHR", "value": 5.0},
                    {"fiscal_period": 2, "member": "ZZIT", "value": 11.0},
                ])])
    base.update(over)
    doc = mod.CostAllocationStatistic.__new__(mod.CostAllocationStatistic)
    doc.__dict__.update(base)
    return doc


def _refusal(fn):
    try:
        fn()
    except Refused as e:
        return str(e)
    raise AssertionError("expected a refusal")


def test_validate_accepts_good_rows_and_sets_dimension_count_total():
    w = _World()
    mod = _load(w)
    doc = _stat(w, mod)
    doc.validate()
    assert doc.dimension == "ZZDIM"
    assert doc.row_count == 3
    assert doc.total == 26.0
    assert sorted(set(w.declared_calls)) == [(2026, 1), (2026, 2)]


def test_validate_refuses_a_period_outside_the_fiscal_year():
    w = _World()
    w.undeclared.add((2026, 13))
    mod = _load(w)
    doc = _stat(w, mod, rows=[{"fiscal_period": 13, "member": "ZZIT", "value": 1.0}])
    assert "13" in _refusal(doc.validate)


def test_validate_refuses_members_not_in_the_dimension_and_lists_them():
    w = _World()
    mod = _load(w)
    doc = _stat(w, mod, rows=[{"fiscal_period": 1, "member": "ZZIT", "value": 1.0},
                              {"fiscal_period": 1, "member": "ZZXX", "value": 1.0},
                              {"fiscal_period": 2, "member": "ZZYY", "value": 1.0}])
    msg = _refusal(doc.validate)
    assert "ZZXX" in msg and "ZZYY" in msg and "ZZDIM" in msg


def test_validate_refuses_a_duplicate_period_member():
    w = _World()
    mod = _load(w)
    doc = _stat(w, mod, rows=[{"fiscal_period": 1, "member": "ZZIT", "value": 1.0},
                              {"fiscal_period": 1, "member": "ZZIT", "value": 2.0}])
    msg = _refusal(doc.validate)
    assert "ZZIT" in msg and "duplicate" in msg.lower()


def test_validate_refuses_a_negative_value():
    w = _World()
    mod = _load(w)
    doc = _stat(w, mod, rows=[{"fiscal_period": 1, "member": "ZZIT", "value": -1.0}])
    assert "ZZIT" in _refusal(doc.validate)


def test_validate_refuses_more_than_20000_rows():
    w = _World()
    w.members["ZZDIM"] = [f"M{i}" for i in range(20001)]
    mod = _load(w)
    rows = [{"fiscal_period": 1, "member": f"M{i}", "value": 1.0} for i in range(20001)]
    msg = _refusal(_stat(w, mod, rows=rows).validate)
    assert "20,000" in msg or "20000" in msg
    ok = _stat(w, mod, rows=rows[:20000])
    ok.validate()
    assert ok.row_count == 20000


def test_validate_refuses_no_rows():
    w = _World()
    mod = _load(w)
    assert _refusal(_stat(w, mod, rows=[]).validate)


def test_before_submit_locks_the_rows_it_checks_and_refuses_an_overlap():
    w = _World()
    mod = _load(w)
    w.other_rows = [{"name": "CAS-00007", "fiscal_period": 2, "member": "ZZIT"}]
    msg = _refusal(_stat(w, mod).before_submit)
    assert "CAS-00007" in msg and "ZZIT" in msg
    assert w.sql and all(q.rstrip().upper().endswith("FOR UPDATE") for q in w.sql)


def test_before_submit_passes_when_nothing_overlaps():
    w = _World()
    mod = _load(w)
    w.other_rows = [{"name": "CAS-00007", "fiscal_period": 3, "member": "ZZIT"}]
    _stat(w, mod).before_submit()
    assert len(w.sql) == 1 and w.sql[0].rstrip().upper().endswith("FOR UPDATE")


def test_before_cancel_asserts_open_per_period_for_an_actual_scenario():
    w = _World()
    mod = _load(w)
    _stat(w, mod).before_cancel()
    assert sorted(w.open_calls) == [(2026, 1), (2026, 2)]


def test_before_cancel_refuses_a_closed_period_on_actual():
    w = _World()
    w.closed.add((2026, 2))
    mod = _load(w)
    assert "closed" in _refusal(_stat(w, mod).before_cancel)


def test_before_cancel_skips_the_gate_for_a_budget_scenario():
    w = _World()
    w.closed.add((2026, 2))
    mod = _load(w)
    _stat(w, mod, scenario="ZZBUD").before_cancel()
    assert w.open_calls == []


def test_validate_accepts_the_25th_dimension_value_as_a_member():
    w = _World()
    w.members["ZZDIM"] = [f"ZZM{i:02d}" for i in range(1, 26)]
    mod = _load(w)
    doc = _stat(w, mod, rows=[{"fiscal_period": 1, "member": "ZZM25", "value": 3.0}])
    doc.validate()
    assert doc.row_count == 1


def test_statistic_reads_members_through_the_shared_helper_only():
    src = open(os.path.join(EPM_DOCTYPES, STAT, STAT + ".py")).read()
    assert "cost_allocation_members" in src
    assert 'get_all(\n            "Dimension Value"' not in src and '"Dimension Value"' not in src


def test_before_cancel_refuses_while_a_submitted_run_cites_the_statistic():
    w = _World()
    mod = _load(w)
    calls = []

    def sql(query, values=None, as_dict=False, **k):
        calls.append((query, values))
        q = " ".join(query.split())
        if ("docstatus = 1" in q and "w.statistic = %(statistic)s" in q
                and (values or {}).get("statistic") == "CAS-00001"):
            return [_Row({"name": "CARS-00007"})]
        return []

    mod.frappe.db.sql = sql
    msg = _refusal(_stat(w, mod, scenario="ZZBUD").before_cancel)
    assert "CARS-00007" in msg
    assert calls
    assert all(" ".join(q.split()).upper().endswith("FOR UPDATE") for q, _ in calls)


def test_before_cancel_allows_when_no_submitted_run_cites_it():
    w = _World()
    mod = _load(w)

    def sql(query, values=None, as_dict=False, **k):
        q = " ".join(query.split())
        assert "docstatus = 1" in q and (values or {}).get("statistic") == "CAS-00001"
        return []

    mod.frappe.db.sql = sql
    _stat(w, mod, scenario="ZZBUD").before_cancel()


def test_cancel_exception_cites_its_decision():
    # The actual-only statistic cancel gate was decided on konsol#269 (14 Nov 2026).
    src = open(os.path.join(EPM_DOCTYPES, STAT, STAT + ".py")).read()
    assert "14 Nov 2026" in src and "konsol#269" in src
    assert "pending" not in src


def test_sync_header_and_values_after_submit_and_cancel_and_delete():
    w = _World()
    mod = _load(w)
    for hook in ("on_submit", "on_cancel", "after_delete"):
        w.syncs.clear()
        getattr(_stat(w, mod), hook)()
        assert [s[0] for s in w.syncs] == [
            "Cost Allocation Statistic", "Cost Allocation Statistic Value"], hook
        assert [s[1] for s in w.syncs] == [
            "epm_staging.cost_allocation_statistics",
            "epm_staging.cost_allocation_statistic_values"], hook


def test_sync_maps_and_filters():
    w = _World()
    mod = _load(w)
    cls = mod.CostAllocationStatistic
    assert cls.CH_TABLE == "epm_staging.cost_allocation_statistics"
    assert cls.CH_FIELD_MAP == {
        "name": "name", "driver": "driver", "dimension": "dimension",
        "data_area_id": "entity", "scenario_id": "scenario", "fiscal_year": "fiscal_year"}
    child = cls.CHILD_CONTROLLERS["Cost Allocation Statistic Value"]
    assert child.CH_TABLE == "epm_staging.cost_allocation_statistic_values"
    assert child.CH_FIELD_MAP == {"parent": "parent", "idx": "idx",
                                  "fiscal_period": "fiscal_period", "member": "member",
                                  "value": "value"}
    assert child.CH_SYNC_FILTERS == {"docstatus": 1}


def test_no_commit_and_no_save_in_the_controller():
    with open(os.path.join(EPM_DOCTYPES, STAT, STAT + ".py")) as f:
        src = f.read()
    assert "db.commit" not in src and "self.save(" not in src


# -- K5b: Load from File -------------------------------------------------------

GOOD_CSV = "fiscal_period,member,value\n1,ZZIT,7\n2,ZZHR,3.5\n"


def _loadable(w, mod, **over):
    over.setdefault("docstatus", 0)
    over.setdefault("status", "Draft")
    over.setdefault("values_file", "/private/files/zz.csv")
    doc = _stat(w, mod, **over)
    w.docs[doc.name] = doc
    return doc


def test_load_is_a_post_only_whitelisted_function():
    mod = _load(_World())
    assert mod.load_values_from_file.whitelist_kwargs == {"methods": ["POST"]}


def test_load_replaces_every_row_and_saves():
    w = _World()
    mod = _load(w)
    doc = _loadable(w, mod)
    w.file_content = GOOD_CSV.encode()
    mod.load_values_from_file("CAS-00001")
    assert [(r.fiscal_period, r.member, r.value) for r in doc.values] == [
        (1, "ZZIT", 7.0), (2, "ZZHR", 3.5)]
    assert doc.saved is True
    assert doc.permission_checked == "write"
    assert w.file_reads == [{"file_url": "/private/files/zz.csv"}]
    assert doc.row_count == 2 and doc.total == 10.5     # validate ran on save


def test_load_accepts_a_bom_and_a_decoded_string():
    w = _World()
    mod = _load(w)
    doc = _loadable(w, mod)
    w.file_content = b"\xef\xbb\xbf" + GOOD_CSV.encode()
    mod.load_values_from_file("CAS-00001")
    assert len(doc.values) == 2
    w.file_content = GOOD_CSV
    mod.load_values_from_file("CAS-00001")
    assert len(doc.values) == 2


def test_load_refuses_a_submitted_or_pending_document_and_changes_nothing():
    for over in ({"docstatus": 1, "status": "Approved"}, {"docstatus": 0, "status": "Pending Approval"},
                 {"docstatus": 2, "status": "Cancelled"}):
        w = _World()
        mod = _load(w)
        doc = _loadable(w, mod, **over)
        before = list(doc.values)
        w.file_content = GOOD_CSV.encode()
        msg = _refusal(lambda: mod.load_values_from_file("CAS-00001"))
        assert "draft" in msg.lower()
        assert doc.values == before and not doc.get("saved") and w.file_reads == []


def test_load_refuses_when_no_file_is_attached():
    w = _World()
    mod = _load(w)
    doc = _loadable(w, mod, values_file=None)
    msg = _refusal(lambda: mod.load_values_from_file("CAS-00001"))
    assert "file" in msg.lower() and not doc.get("saved")


def test_load_with_bad_rows_lists_every_error_and_changes_nothing():
    w = _World()
    mod = _load(w)
    doc = _loadable(w, mod)
    before = list(doc.values)
    w.file_content = b"fiscal_period,member,value\nx,ZZIT,1\n2,,1\n"
    msg = _refusal(lambda: mod.load_values_from_file("CAS-00001"))
    assert "row 2" in msg.lower() and "row 3" in msg.lower()
    assert doc.values == before and not doc.get("saved")


def test_load_with_undecodable_bytes_changes_nothing():
    w = _World()
    mod = _load(w)
    doc = _loadable(w, mod)
    before = list(doc.values)
    w.file_content = b"\xff\xfe\x00bad"
    _refusal(lambda: mod.load_values_from_file("CAS-00001"))
    assert doc.values == before and not doc.get("saved")


def test_load_save_refusal_propagates_so_the_request_rolls_back():
    w = _World()
    mod = _load(w)
    _loadable(w, mod)
    w.file_content = b"fiscal_period,member,value\n1,NOTAMEMBER,1\n"
    msg = _refusal(lambda: mod.load_values_from_file("CAS-00001"))
    assert "NOTAMEMBER" in msg


def test_load_button_is_in_the_form_script():
    path = os.path.join(EPM_DOCTYPES, STAT, STAT + ".js")
    with open(path) as f:
        src = f.read()
    assert "Load from File" in src and "values_file" in src
    assert f"konsol.epm.doctype.{STAT}.{STAT}.load_values_from_file" in src
    assert "docstatus === 0" in src


def test_kf3_a_failed_member_read_is_not_reported_as_unknown_members():
    from konsol.cost_allocation_members import MemberReadError

    w = _World()
    mod = _load(w)

    def broken(dimension):
        raise MemberReadError(dimension)

    mod._dimension_members = broken
    msg = _refusal(_stat(w, mod, dimension="ZZDIM").validate)
    assert "could not read the members of ZZDIM" in msg and "Error Log" in msg
    assert "not members" not in msg
