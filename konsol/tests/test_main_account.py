"""Main Account, the group chart of accounts governed in konsol (konsol#182):
the DocType, its controller and its registrations.

The controller runs here against stub NestedSet / GovernedReferenceDocument
bases that record their calls, and the real group_chart_model.
"""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT_DIR = os.path.join(APP_DIR, "epm", "doctype", "main_account")
CONTROLLER = os.path.join(DT_DIR, "main_account.py")
BS = "Balance Sheet"
DDL = ("(main_account String, account_name String, chart_of_accounts String, "
       "parent_account String, is_group UInt8, account_type String, "
       "statement_section String, sub_section String, normal_balance String, "
       "time_balance String, fx_method String, is_posting UInt8, "
       "is_suspended UInt8, allow_ic UInt8, cf_category String, "
       "cf_line_item String, is_cash UInt8, main_account_category String, "
       "status String) "
       "ENGINE = MergeTree ORDER BY main_account")
#: konsolidat's clickhouse/init-db.sql line, as the plan pins it (konsol#182 section 2.3).
INIT_DB_LINE = ("CREATE TABLE IF NOT EXISTS epm_staging.main_accounts (main_account String, account_name String, "
                "chart_of_accounts String, parent_account String, is_group UInt8, account_type String, "
                "statement_section String, sub_section String, normal_balance String, time_balance String, "
                "fx_method String, is_posting UInt8, is_suspended UInt8, allow_ic UInt8, cf_category String, "
                "cf_line_item String, is_cash UInt8, main_account_category String, status String) "
                "ENGINE = MergeTree ORDER BY main_account;")


class Refused(Exception):
    pass


class _Bounds(dict):
    __getattr__ = dict.get


def _json():
    with open(os.path.join(DT_DIR, "main_account.json")) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _json()["fields"]}


def _literal(path, name):
    with open(path) as f:
        tree = ast.parse(f.read())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == name)


def _class(path, name):
    with open(path) as f:
        tree = ast.parse(f.read())
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _method(cls, name):
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


# -- the controller, against recording stubs --------------------------------------------

CALLS = []


class _Doc:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def get(self, key, default=None):
        return self.__dict__.get(key, default)

    def set(self, key, value):
        self.__dict__[key] = value


class NestedSet(_Doc):
    def on_update(self):
        CALLS.append("tree.on_update")
        if self.get("tree_refuses"):
            raise Refused("cannot be a leaf node as it has children")

    def on_trash(self):
        CALLS.append("tree.on_trash")

    def after_rename(self, olddn, newdn, merge=False):
        CALLS.append(("tree.after_rename", olddn, newdn, merge))


class GovernedReferenceDocument(_Doc):
    CH_SYNC_FILTERS = {"status": "Published"}
    BUILD_SCOPE = None

    def on_update(self):
        CALLS.append("governed.on_update")

    def after_delete(self):
        CALLS.append("governed.after_delete")

    def _resync(self):
        CALLS.append("governed._resync")

    def _request_rebuild(self, action):
        CALLS.append(("governed._request_rebuild", action))
        return "BAPR-1"


def _load():
    frappe = types.ModuleType("frappe")

    def throw(msg, *a, **k):
        raise Refused(msg)

    frappe.throw = throw
    frappe.messages = []
    frappe.msgprint = lambda msg, *a, **k: frappe.messages.append(msg)
    frappe.parents, frappe.children = {}, []
    frappe.descendants = []
    frappe.db = types.SimpleNamespace(
        get_value=lambda doctype, name, fields, as_dict=False: (
            _Bounds(lft=1, rgt=99) if fields == ["lft", "rgt"] else frappe.parents.get(name)))
    frappe.get_all = lambda doctype, filters=None, pluck=None, **k: (
        list(frappe.descendants) if "lft" in (filters or {}) else
        [c for c, parent, status in frappe.children
         if parent == filters["parent_account"] and status == filters["status"]])
    nested = types.ModuleType("frappe.utils.nestedset")
    nested.NestedSet = NestedSet
    gov = types.ModuleType("konsol.governed_reference")
    gov.GovernedReferenceDocument = GovernedReferenceDocument
    sl = types.ModuleType("konsol.schema_lifecycle")
    sl.check_epm_admin = lambda: None
    spec = importlib.util.spec_from_file_location("gcm_for_main_account", os.path.join(APP_DIR, "group_chart_model.py"))
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    konsol = types.ModuleType("konsol")
    konsol.group_chart_model = model
    stubs = {"frappe": frappe, "frappe.utils": types.ModuleType("frappe.utils"), "frappe.utils.nestedset": nested,
             "konsol": konsol, "konsol.group_chart_model": model, "konsol.governed_reference": gov,
             "konsol.schema_lifecycle": sl}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("main_account_under_test", CONTROLLER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


C = _load()
# What holds an account in the chart, read by three module helpers; stubbed
# here from lists the tests fill (the real _submitted_postings is tested below).
REAL_SUBMITTED_POSTINGS = C._submitted_postings
C.frappe.postings, C.frappe.ic, C.frappe.diff, C.frappe.asked = [], [], [], []
C._submitted_postings = lambda codes: C.frappe.asked.append(list(codes)) or [
    p for p in C.frappe.postings if p[0] in codes]
C._intercompany_rows = lambda codes: list(C.frappe.ic)
C._difference_groups = lambda codes: list(C.frappe.diff)


def _doc(status="Draft", before=None, **kw):
    fields = dict(doctype="Main Account", name="ZZ1000", main_account="ZZ1000", account_name="Cash",
                  chart_of_accounts="ZZCOA", parent_account=None, is_group=0, account_type="Asset",
                  statement_section=BS, sub_section="", normal_balance="", time_balance="", fx_method="",
                  is_posting=1, is_suspended=0, allow_ic=0, main_account_category="", cf_category="",
                  cf_line_item="", is_cash=0, description="", status=status)
    fields.update(kw)
    d = C.MainAccount(**fields)
    prior = None if before is None else C.MainAccount(**{**fields, "fx_method": "closing",
                                                          "normal_balance": "Debit", "time_balance": "balance",
                                                          **before})
    d.get_doc_before_save = lambda: prior
    return d


def _refused(fn, needle):
    try:
        fn()
    except Refused as e:
        assert needle in str(e), str(e)
        return
    raise AssertionError(f"not refused: {needle}")


# -- the DocType ----------------------------------------------------------------------------

def test_doctype_is_a_tree_on_parent_account():
    meta = _json()
    assert (meta["name"], meta["module"]) == ("Main Account", "EPM")
    assert (meta["is_tree"], meta["nsm_parent_field"]) == (1, "parent_account")
    assert (meta["autoname"], meta["naming_rule"], meta["allow_rename"]) == ("field:main_account", "By fieldname", 0)
    assert (meta["track_changes"], meta["title_field"], meta["search_fields"]) == (1, "account_name",
                                                                                   "account_name,account_type")
    fields = _fields()
    parent = fields["parent_account"]
    assert (parent["fieldtype"], parent["options"], parent["ignore_user_permissions"]) == ("Link", "Main Account", 1)
    for name, kind in (("lft", "Int"), ("rgt", "Int"), ("old_parent", "Data")):
        f = fields[name]
        assert (f["fieldtype"], f["hidden"], f["read_only"], f["no_copy"]) == (kind, 1, 1, 1), name
    assert C.MainAccount.nsm_parent_field == "parent_account"
    assert issubclass(C.MainAccount, NestedSet) and issubclass(C.MainAccount, GovernedReferenceDocument)


#: fieldname -> (fieldtype, options, default, reqd), in field order (plan section 2.1)
FIELD_TABLE = {
    "main_account": ("Data", None, None, 1),
    "account_name": ("Data", None, None, 1),
    "chart_of_accounts": ("Data", None, None, 1),
    "parent_account": ("Link", "Main Account", None, None),
    "is_group": ("Check", None, "0", None),
    "status": ("Select", "Draft\nPublished\nInactive", "Draft", None),
    "account_type": ("Select", "\nAsset\nLiability\nEquity\nRevenue\nExpense\nBalance sheet\nProfit and loss", None, None),
    "statement_section": ("Select", "\nProfit and Loss\nBalance Sheet", None, None),
    "sub_section": ("Data", None, None, None),
    "normal_balance": ("Select", "\nDebit\nCredit", None, None),
    "time_balance": ("Select", "\nflow\nbalance", None, None),
    "fx_method": ("Select", "\nclosing\naverage\nhistorical", None, None),
    "is_posting": ("Check", None, "1", None),
    "is_suspended": ("Check", None, "0", None),
    "allow_ic": ("Check", None, "0", None),
    "main_account_category": ("Data", None, None, None),
    "cf_category": ("Select", "\nOperating\nInvesting\nFinancing", None, None),
    "cf_line_item": ("Data", None, None, None),
    "is_cash": ("Check", None, "0", None),
    "source": ("Select", "Manual\nUpload", "Manual", None),
    "source_note": ("Small Text", None, None, None),
    "description": ("Small Text", None, None, None),
    "lft": ("Int", None, None, None),
    "rgt": ("Int", None, None, None),
    "old_parent": ("Data", None, None, None),
}


def test_field_table():
    fields = _fields()
    got = {n: (f["fieldtype"], f.get("options"), f.get("default"), f.get("reqd")) for n, f in fields.items()
           if f["fieldtype"] not in ("Section Break", "Column Break")}
    assert got == FIELD_TABLE
    order = [n for n in _json()["field_order"] if fields[n]["fieldtype"] not in ("Section Break", "Column Break")]
    assert order == list(FIELD_TABLE)
    assert set(_json()["field_order"]) == set(fields)
    code = fields["main_account"]
    assert (code["unique"], code["in_list_view"], code["in_standard_filter"]) == (1, 1, 1)
    assert fields["status"]["in_list_view"] == fields["status"]["in_standard_filter"] == 1
    assert fields["source"]["read_only"] == fields["source_note"]["read_only"] == 1
    # the vocabularies are the model's, so the form and the file agree
    for name, allowed in C.M.VOCAB.items():
        assert [o for o in fields[name]["options"].split("\n") if o] == list(allowed), name


def test_permissions_publish_is_the_close_leads():
    perms = {p["role"]: {k for k, v in p.items() if k != "role" and v} for p in _json()["permissions"]}
    full = {"read", "write", "create", "delete", "report", "export"}
    assert perms == {
        "System Manager": full, "EPM Admin": full, "EPM Analyst": {"read", "write", "create", "report"},
        "EPM User": {"read"}, "Entity Accountant": {"read"}, "Budget Submitter": {"read"},
        "Budget Controller": {"read"}, "Budget Manager": {"read"}, "Budget Approver": {"read"}}
    assert not any("import" in p for p in _json()["permissions"]), "no Data Import: use the chart upload"
    # publishing is check_epm_admin's, which a Group Accountant does not pass
    allowed = _literal(os.path.join(APP_DIR, "schema_lifecycle.py"), "_ALLOWED_ROLES")
    assert "EPM Admin" in allowed and "EPM Analyst" not in allowed
    # every save of a row that was or is becoming Published asks for it
    admin = []
    C.check_epm_admin = lambda: admin.append(1)
    try:
        for status, before, want in (("Draft", None, False), ("Draft", "Draft", False), ("Inactive", "Draft", False),
                                     ("Published", None, True), ("Published", "Draft", True),
                                     ("Published", "Published", True), ("Inactive", "Published", True),
                                     ("Draft", "Published", True)):
            admin.clear()
            d = _doc(status, None if before is None else {"status": before}, fx_method="closing",
                     normal_balance="Debit", time_balance="balance")
            d._guard_publish()
            assert bool(admin) == want, (status, before)
    finally:
        C.check_epm_admin = lambda: None


def test_controller_declares_the_governed_contract():
    cls = C.MainAccount
    assert cls.CH_TABLE == "epm_staging.main_accounts"
    assert cls.CH_SYNC_FILTERS == {"status": "Published"}
    assert cls.BUILD_SCOPE == "chart"
    with open(os.path.join(APP_DIR, "governed_reference.py")) as f:
        src = f.read()
    assert "scope=self.BUILD_SCOPE" in src   # publish/unpublish/delete request that scope


def _ddl():
    return {k: _literal(os.path.join(APP_DIR, "clickhouse.py"), k)
            for k in ("_REFERENCE_TABLE_DDL", "_ADDED_COLUMNS", "_RETIRED_TABLES")}


def test_field_map_is_the_ddl_in_order():
    body = _ddl()["_REFERENCE_TABLE_DDL"]["epm_staging.main_accounts"]
    columns = [c.strip().split() for c in body[1:body.index(") ENGINE")].split(",")]
    assert [c[0] for c in columns] == list(C.MainAccount.CH_FIELD_MAP)
    assert all(k == v for k, v in C.MainAccount.CH_FIELD_MAP.items())
    fields = _fields()
    for name, kind in columns:
        assert name in fields, name
        assert kind == ("UInt8" if fields[name]["fieldtype"] == "Check" else "String"), name


def test_staging_ddl_character_for_character():
    """Identical to konsolidat's clickhouse/init-db.sql, character for character."""
    body = _ddl()["_REFERENCE_TABLE_DDL"]["epm_staging.main_accounts"]
    assert body == DDL
    assert f"CREATE TABLE IF NOT EXISTS epm_staging.main_accounts {body};" == INIT_DB_LINE


def test_new_table_ships_complete_not_via_added_columns():
    consts = _ddl()
    assert "epm_staging.main_accounts" not in consts["_ADDED_COLUMNS"]
    assert "epm_staging.main_accounts" not in consts["_RETIRED_TABLES"]
    # the cash-flow and intercompany columns ship now, so PR5 needs no ALTER
    body = consts["_REFERENCE_TABLE_DDL"]["epm_staging.main_accounts"]
    for column in ("cf_category String", "cf_line_item String", "is_cash UInt8", "allow_ic UInt8"):
        assert column in body, column


def test_on_update_runs_the_tree_then_the_resync():
    """Neither base calls super(), so the MRO alone would drop one of them."""
    body = _method(_class(CONTROLLER, "MainAccount"), "on_update").body
    assert [ast.unparse(s) for s in body if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Constant)] == [
        "NestedSet.on_update(self)", "GovernedReferenceDocument.on_update(self)"]
    CALLS.clear()
    _doc().on_update()
    assert CALLS == ["tree.on_update", "governed.on_update"]
    # the tree refuses: nothing reaches the warehouse
    CALLS.clear()
    _refused(lambda: _doc(tree_refuses=True).on_update(), "has children")
    assert CALLS == ["tree.on_update"]


def test_delete_resyncs_in_after_delete_not_on_trash():
    cls = _class(CONTROLLER, "MainAccount")
    trash = ast.unparse(_method(cls, "on_trash"))
    assert "NestedSet.on_trash(self)" in trash and "_resync" not in trash and "after_delete" not in trash
    CALLS.clear()
    d = _doc()
    d.on_trash()
    d.after_delete()
    assert CALLS == ["tree.on_trash", "governed.after_delete"]


def test_rename_resyncs():
    CALLS.clear()
    _doc().after_rename("ZZ1000", "ZZ1001", False)
    assert CALLS == [("tree.after_rename", "ZZ1000", "ZZ1001", False), "governed._resync"]


def test_not_a_hook_build_trigger():
    """Governed doctypes request their rebuild from publish/unpublish/delete.
    On the doc_events list every Draft save would ask for a build."""
    assert "Main Account" not in _literal(os.path.join(APP_DIR, "hooks.py"), "_dbt_trigger_doctypes")
    with open(os.path.join(APP_DIR, "tasks.py")) as f:
        tree = ast.parse(f.read())
    build_map = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                     and getattr(n.targets[0], "id", None) == "DOCTYPE_BUILD_MAP")
    assert "Main Account" not in [k.value for k in build_map.keys]


def test_chart_scope_selects_everything_the_chart_classifies():
    tasks = os.path.join(APP_DIR, "tasks.py")
    selector = _literal(tasks, "SCOPE_SELECTOR")
    # silver_main_accounts and everything downstream: the statements, not only
    # consolidation (+tag:domain:consolidation selects ancestors)
    assert selector["chart"] == "silver_main_accounts+"
    assert "chart" not in _literal(tasks, "RAW_DEPENDENT_SCOPES")
    # without a Build Scope row, _known_domains turns the selector into a full build
    with open(os.path.join(APP_DIR, "fixtures", "build_scope.json")) as f:
        rows = {r["name"]: r for r in json.load(f)}
    assert rows["chart"]["scope_name"] == "chart" and rows["chart"]["requires_raw_data"] == 0
    # Build Approval.build_scope is a Select: a chart request must be an option
    with open(os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.json")) as f:
        scope = next(fl for fl in json.load(f)["fields"] if fl["fieldname"] == "build_scope")
    assert "chart" in scope["options"].split("\n")
    risk = _literal(os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.py"), "SCOPE_RISK")
    assert risk["chart"] == "high" == risk["consolidation"]


def test_not_a_fixture():
    """Everything in fixtures/ is force-reimported on every migrate: a site's
    chart must never be overwritten by one."""
    assert not os.path.exists(os.path.join(APP_DIR, "fixtures", "main_account.json"))
    for path in os.listdir(os.path.join(APP_DIR, "fixtures")):
        if path.endswith(".json"):
            with open(os.path.join(APP_DIR, "fixtures", path)) as f:
                assert all(r.get("doctype") != "Main Account" for r in json.load(f)), path
    fixtures = _literal(os.path.join(APP_DIR, "hooks.py"), "fixtures")
    assert "Main Account" not in [e if isinstance(e, str) else e.get("dt") for e in fixtures]


# -- the controller's rules ------------------------------------------------------------------

def test_validate_fills_defaults_then_refuses_problems():
    d = _doc()
    d.validate()
    assert (d.normal_balance, d.time_balance, d.fx_method) == ("Debit", "balance", "closing")
    _refused(lambda: _doc(fx_method="average").validate(), "not translated at the average rate")
    _refused(lambda: _doc(is_group=1).validate(), "clear is_posting")


def test_the_code_is_normalised_before_naming():
    d = _doc(main_account="  ZZ1000 ")
    d.before_naming()
    assert d.main_account == "ZZ1000"
    _refused(lambda: _doc(main_account="ZZ\t1000").before_naming(), "tab or a line break")
    _refused(lambda: _doc(main_account=" ").before_naming(), "blank")


def test_a_parent_must_be_a_published_heading_to_publish_under():
    C.frappe.parents["ZZ9000"] = {"main_account": "ZZ9000", "is_group": 1, "chart_of_accounts": "ZZCOA",
                                  "statement_section": "", "status": "Draft"}
    try:
        _refused(lambda: _doc("Published", parent_account="ZZ9000").validate(), "publish its parent ZZ9000 first")
        _doc("Draft", parent_account="ZZ9000").validate()   # a Draft may sit under a Draft heading
        C.frappe.parents["ZZ9000"]["status"] = "Published"
        _doc("Published", parent_account="ZZ9000").validate()
        C.frappe.parents["ZZ9000"]["is_group"] = 0
        _refused(lambda: _doc(parent_account="ZZ9000").validate(), "is not a heading")
        _refused(lambda: _doc(parent_account="ZZ404").validate(), "parent ZZ404 does not exist")
        _refused(lambda: _doc("Published", account_type="").validate(), "cannot be published without account_type")
    finally:
        C.frappe.parents.clear()


def test_unpublishing_a_heading_with_published_accounts_is_refused():
    C.frappe.children[:] = [("ZZ1000", "ZZ9000", "Published"), ("ZZ1100", "ZZ9000", "Draft")]
    try:
        heading = dict(name="ZZ9000", main_account="ZZ9000", is_group=1, is_posting=0, account_type="")
        _refused(lambda: _doc("Inactive", {"status": "Published"}, **heading)._guard_publish(),
                 "has Published accounts under it (ZZ1000)")
        C.frappe.children[0] = ("ZZ1000", "ZZ9000", "Inactive")
        _doc("Inactive", {"status": "Published"}, **heading)._guard_publish()
        C.frappe.messages.clear()
        _doc("Inactive", {"status": "Published"})._guard_publish()   # a leaf: a warning
        assert "no longer in the group chart" in C.frappe.messages[0]
    finally:
        C.frappe.children.clear()
        C.frappe.messages.clear()


def test_reclassifying_a_published_account_warns():
    C.frappe.messages.clear()
    _doc("Published", {"status": "Published"}, fx_method="historical", normal_balance="Debit",
         time_balance="balance").validate()
    assert C.frappe.messages and "re-translated at the next full rebuild" in C.frappe.messages[0]
    assert "fx_method" in C.frappe.messages[0]
    C.frappe.messages.clear()
    _doc("Published", {"status": "Published"}, fx_method="closing", normal_balance="Debit",
         time_balance="balance", sub_section="Current assets").validate()
    assert C.frappe.messages == []   # presentation only
    _doc("Draft", {"status": "Draft"}, fx_method="historical").validate()
    assert C.frappe.messages == []   # not live


def test_on_the_desk_in_reference_data():
    labels = _literal(os.path.join(APP_DIR, "dashboard.py"), "_LABELS")
    assert labels["Main Account"] == "Group Chart of Accounts"
    cards = dict(_literal(os.path.join(APP_DIR, "dashboard.py"), "_CARDS"))
    assert cards["Reference Data"][0] == "Main Account"
    with open(os.path.join(APP_DIR, "dashboard.py")) as f:
        refresh = f.read().split("def _workspace_needs_refresh")[1].split("\ndef ")[0]
    assert '"Main Account" not in' in refresh, "existing sites must rebuild the card once"


# -- an account in use cannot leave the chart (review of #183) ------------------------------------

def _clear():
    for name in ("postings", "ic", "diff", "asked", "descendants", "children", "messages"):
        getattr(C.frappe, name).clear()


def _leaving(status="Inactive", **kw):
    """A Published leaf leaving the chart (unpublish or a plain save)."""
    return _doc(status, {"status": "Published"}, fx_method="closing", normal_balance="Debit",
                time_balance="balance", **kw)


def test_a_leaf_that_submitted_trial_balances_post_to_cannot_leave_the_chart():
    _clear()
    C.frappe.postings[:] = [("ZZ1000", "ZZOP", 2026, 1), ("ZZ1000", "ZZOP", 2026, 2), ("ZZ4000", "ZZOP", 2026, 1)]
    try:
        for status in ("Inactive", "Draft"):   # unpublish, or any save out of Published
            msg = ""
            try:
                _leaving(status)._guard_publish()
            except Refused as e:
                msg = str(e)
            assert "ZZ1000 cannot leave the group chart" in msg and "ZZOP FY2026 P01, ZZOP FY2026 P02" in msg, msg
            assert "reclassify the account instead" in msg and "ZZ4000" not in msg
        # delete of a Published leaf: the same
        _refused(lambda: _doc("Published", fx_method="closing").on_trash(), "ZZ1000 cannot leave the group chart")
        # a Draft is not in the chart: nothing to check
        C.frappe.asked.clear()
        _doc("Draft").on_trash()
        assert C.frappe.asked == []
        # not in use: it leaves, with the warning
        C.frappe.postings.clear()
        _leaving()._guard_publish()
        assert "no longer in the group chart" in C.frappe.messages[-1]
    finally:
        _clear()


def test_an_intercompany_or_difference_account_holds_it_in_the_chart():
    _clear()
    try:
        C.frappe.ic[:] = ["ICA-ZZ1000"]
        _refused(lambda: _leaving()._guard_publish(), "Published Intercompany Accounts name it (ICA-ZZ1000)")
        C.frappe.ic.clear()
        C.frappe.diff[:] = ["CG-ZZGRP-"]
        _refused(lambda: _leaving()._guard_publish(), "book intercompany differences to it (CG-ZZGRP-)")
        _refused(lambda: _doc("Published", fx_method="closing").on_trash(), "CG-ZZGRP-")
    finally:
        _clear()


def test_a_heading_answers_for_the_accounts_under_it():
    _clear()
    heading = dict(name="ZZ9000", main_account="ZZ9000", is_group=1, is_posting=0, account_type="",
                   statement_section="")
    try:
        C.frappe.descendants[:] = ["ZZ1000", "ZZ1100"]   # none of them Published any more
        C.frappe.postings[:] = [("ZZ1100", "ZZOP", 2026, 3)]
        msg = ""
        try:
            _doc("Inactive", {"status": "Published"}, **heading)._guard_publish()
        except Refused as e:
            msg = str(e)
        assert "ZZ9000 (a heading: the accounts under it) cannot leave" in msg and "ZZ1100: ZZOP FY2026 P03" in msg
        assert C.frappe.asked == [["ZZ1000", "ZZ1100"]]
        C.frappe.postings.clear()
        C.frappe.ic[:] = ["ICA-ZZ1000"]
        _refused(lambda: _doc("Published", **heading).on_trash(), "ZZ9000 (a heading")
        C.frappe.ic.clear()
        _doc("Inactive", {"status": "Published"}, **heading)._guard_publish()   # nothing holds it
    finally:
        _clear()


def test_submitted_postings_are_the_claimed_rows_in_the_warehouse():
    sent = []
    ch = types.ModuleType("konsol.clickhouse")
    ch._sql_value = lambda v: "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"
    rates = types.ModuleType("konsol.group_rates")
    rates._not_built = lambda e: "UNKNOWN_TABLE" in str(e)
    answers = []

    def execute(sql, params=None):
        sent.append(sql)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    ch.execute = execute
    saved = {k: sys.modules.get(k) for k in ("konsol.clickhouse", "konsol.group_rates")}
    sys.modules.update({"konsol.clickhouse": ch, "konsol.group_rates": rates})
    try:
        answers.append('{"main_account":"ZZ1000","data_area_id":"ZZOP","fiscal_year":2026,"fiscal_period":1}\n')
        assert REAL_SUBMITTED_POSTINGS(["ZZ1000", "O'X"]) == [("ZZ1000", "ZZOP", 2026, 1)]
        assert "main_account IN ('ZZ1000', 'O\\'X')" in sent[0]
        assert "batch_id IN (SELECT batch_id FROM epm_raw.trial_balance_submission_control)" in sent[0]
        answers.append(RuntimeError("Code: 60. Unknown table (UNKNOWN_TABLE)"))
        assert REAL_SUBMITTED_POSTINGS(["ZZ1000"]) == []   # nothing landed: nothing to unbalance
        answers.append(ConnectionError("refused"))
        _refused(lambda: REAL_SUBMITTED_POSTINGS(["ZZ1000"]), "the warehouse could not be read")
        assert REAL_SUBMITTED_POSTINGS([]) == [] and len(sent) == 3
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_intercompany_rows_look_at_both_sides():
    asked = []
    C.frappe.db.table_exists = lambda name: True
    saved = C.frappe.get_all
    C.frappe.get_all = lambda doctype, filters=None, pluck=None, **k: asked.append((doctype, filters)) or (
        ["ICA-B"] if "counterpart_account" in filters else ["ICA-A"])
    try:
        assert C._intercompany_rows.__name__ == "<lambda>"   # the stub; read the module's own
        real = C.__dict__["_intercompany_rows"]
    finally:
        C.frappe.get_all = saved
    with open(CONTROLLER) as f:
        src = f.read()
    body = src.split("def _intercompany_rows")[1].split("\ndef ")[0]
    assert '("main_account", "counterpart_account")' in body and '"status": _PUBLISHED' in body
    groups = src.split("def _difference_groups")[1]
    assert '"ic_difference_account": ["in", codes]' in groups
    assert real is not None and asked == []


def test_a_publish_on_a_warehouse_that_has_never_built_says_what_to_build_first():
    """review of #183, item 3: publish() and unpublish() from the form request
    the chart build; on a new site it cannot run yet, and the Close Lead is told."""
    note = "The warehouse has not built yet. ... approve a consolidation build first ..."
    upload = types.ModuleType("konsol.chart_upload")
    saved = sys.modules.get("konsol.chart_upload")
    sys.modules["konsol.chart_upload"] = upload
    try:
        for answer, shown in ((note, True), (None, False)):
            upload.unbuilt_warehouse_note = lambda: answer
            C.frappe.messages.clear()
            CALLS.clear()
            assert _doc("Published")._request_rebuild("Publish") == "BAPR-1"
            assert CALLS == [("governed._request_rebuild", "Publish")]   # still requested
            assert (note in C.frappe.messages) is shown
    finally:
        C.frappe.messages.clear()
        if saved is None:
            sys.modules.pop("konsol.chart_upload", None)
        else:
            sys.modules["konsol.chart_upload"] = saved

