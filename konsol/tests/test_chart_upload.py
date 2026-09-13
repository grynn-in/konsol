"""The chart upload (konsol#182): konsol/chart_upload.py, the canonical way a
site gets its group chart.

Runs the three endpoints against a stub frappe: Main Account rows with lft, a
transaction that rollback() restores, and recorded inserts, saves and rebuild
requests. The rules are the real group_chart_model; the file reader
(tb_bulk._read_table) is a stub handing back a table.
"""
import copy
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(APP_DIR, "chart_upload.py")
BS, PL = "Balance Sheet", "Profit and Loss"
HEAD = ["main_account", "account_name", "account_type", "statement_section", "parent_account", "chart_of_accounts"]


class Refused(Exception):
    pass


class _Row(dict):
    __getattr__ = dict.get


class Site:
    def __init__(self, rows=(), admin=True, can_create=True, fail_on=None, tables=None, built=True):
        self.built = built   # the warehouse has built a trial balance (or an exception: cannot ask)
        self.rows = {r["main_account"]: dict(r) for r in rows}
        self.admin, self.can_create, self.fail_on = admin, can_create, fail_on
        self.tables = tables or {}
        self.inserted, self.saved, self.rebuilds, self.reads = [], [], [], []
        self.rollbacks = 0
        self.snapshot = copy.deepcopy(self.rows)

    def begin(self):
        """A request starts: what rollback() returns to."""
        self.snapshot = copy.deepcopy(self.rows)


def row(code, status="Draft", lft=1, **kw):
    r = {"main_account": code, "name": code, "account_name": f"ZZ {code}", "chart_of_accounts": "ZZCOA",
         "parent_account": None, "is_group": 0, "account_type": "Asset", "statement_section": BS,
         "normal_balance": "Debit", "time_balance": "balance", "fx_method": "closing", "is_posting": 1,
         "allow_ic": 0, "status": status, "lft": lft}
    r.update(kw)
    return r


def load(site):
    frappe = types.ModuleType("frappe")

    def throw(msg, exc=None, *a, **k):
        raise Refused(msg)

    def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit_page_length=None):
        assert doctype == "Main Account"
        rows = [r for r in site.rows.values() if all(r.get(k) == v for k, v in (filters or {}).items())]
        if order_by:
            assert order_by == "lft asc", order_by
            rows.sort(key=lambda r: r["lft"])
        if pluck:
            return [r["main_account"] for r in rows]
        return [_Row({f: r.get(f) for f in fields} | {"name": r["main_account"]}) for r in rows]

    class Doc:
        def __init__(self, values):
            self.__dict__.update(values)

        def update(self, fields):
            self.__dict__.update(fields)

        def _fail(self):
            if self.main_account == site.fail_on:
                raise RuntimeError(f"save failed: {site.fail_on}")

        def insert(self, *a, **k):
            self._fail()
            site.inserted.append(self.main_account)
            site.rows[self.main_account] = {**self.__dict__, "name": self.main_account,
                                            "lft": 100 + len(site.inserted)}
            return self

        def save(self, *a, **k):
            self._fail()
            site.saved.append(self.main_account)
            site.rows[self.main_account].update(self.__dict__)
            return self

    def get_doc(first, name=None):
        if isinstance(first, dict):
            return Doc(first)
        return Doc(copy.deepcopy(site.rows[name]))

    def rollback():
        site.rollbacks += 1
        site.rows = copy.deepcopy(site.snapshot)

    frappe.throw, frappe.get_all, frappe.get_doc = throw, get_all, get_doc
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.has_permission = lambda doctype, ptype: site.can_create
    frappe.db = types.SimpleNamespace(rollback=rollback)

    sl = types.ModuleType("konsol.schema_lifecycle")

    def check_epm_admin():
        if not site.admin:
            raise Refused("You need the 'EPM Admin' role to publish or unpublish.")

    def request_governed_rebuild(doc, action, scope="full"):
        site.rebuilds.append((doc.main_account, action, scope))
        return f"BAPR-{len(site.rebuilds):05d}"

    sl.check_epm_admin, sl.request_governed_rebuild = check_epm_admin, request_governed_rebuild
    tb = types.ModuleType("konsol.tb_bulk")

    def read_table(url):
        site.reads.append(url)
        return site.tables[url]

    tb._read_table = read_table
    spec = importlib.util.spec_from_file_location("gcm_for_chart_upload", os.path.join(APP_DIR, "group_chart_model.py"))
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    konsol = types.ModuleType("konsol")
    konsol.group_chart_model = model
    stubs = {"frappe": frappe, "konsol": konsol, "konsol.group_chart_model": model,
             "konsol.schema_lifecycle": sl, "konsol.tb_bulk": tb}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("chart_upload_under_test", PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    rates = types.ModuleType("konsol.group_rates")

    def ledgers_built():
        if isinstance(site.built, Exception):
            raise site.built
        return site.built

    rates.ledgers_built = ledgers_built
    # imported at call time: keep the stubs installed for the calls
    mod._stub_modules = {"konsol.tb_bulk": tb, "konsol.group_rates": rates}
    return mod


def call(site, name, *args):
    mod = load(site)
    site.begin()
    saved = {k: sys.modules.get(k) for k in mod._stub_modules}
    sys.modules.update(mod._stub_modules)
    try:
        return getattr(mod, name)(*args)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


GOOD = [HEAD, ["ZZ9000", "Heading", "", "", "", "ZZCOA"],
        ["ZZ1000", "Cash", "Asset", "BS", "ZZ9000", "ZZCOA"],
        ["ZZ4000", "Sales", "Revenue", "P&L", "ZZ9000", "ZZCOA"]]
BAD = GOOD + [["ZZ4100", "Bad", "Revenue", "P&L", "ZZ9000", "ZZCOA"][:3] + ["BS", "ZZ9000", "ZZCOA"]]


def refused(fn):
    try:
        fn()
    except Refused as e:
        return str(e)
    raise AssertionError("not refused")


def test_the_admin_guard_refuses_an_analyst():
    site = Site(admin=False, tables={"/f.csv": GOOD})
    for name, arg in (("check_chart_file", "/f.csv"), ("load_chart", "/f.csv"), ("publish_chart", "ZZCOA")):
        assert "EPM Admin" in refused(lambda: call(site, name, arg)), name
    assert site.reads == [] and site.inserted == [] and site.saved == []
    # the create permission is required too
    site = Site(can_create=False, tables={"/f.csv": GOOD})
    assert "Close Lead" in refused(lambda: call(site, "load_chart", "/f.csv"))
    assert site.reads == []


def test_the_check_writes_nothing():
    site = Site(tables={"/f.csv": GOOD})
    report = call(site, "check_chart_file", "/f.csv")
    assert report["ok"] and report["insert"] == ["ZZ9000", "ZZ1000", "ZZ4000"] and "writes" not in report
    assert site.inserted == [] and site.saved == [] and site.rebuilds == [] and site.rows == {}


def test_a_file_with_problems_loads_nothing():
    """All or nothing: the whole file is checked before the first write."""
    site = Site(tables={"/f.csv": BAD})
    out = call(site, "load_chart", "/f.csv")
    assert out["loaded"] is False and any("ZZ4100" in e for e in out["errors"])
    assert site.inserted == [] and site.saved == [] and site.rebuilds == [] and site.rollbacks == 0
    site = Site(tables={"/f.csv": [["code", "name"]]})   # unreadable header: the same
    out = call(site, "load_chart", "/f.csv")
    assert out["loaded"] is False and "missing column(s) chart_of_accounts" in out["errors"][0]


def test_a_failure_while_loading_rolls_everything_back():
    existing = [row("ZZ1000", account_name="Old name")]
    site = Site(rows=existing, fail_on="ZZ4000", tables={"/f.csv": GOOD})
    try:
        call(site, "load_chart", "/f.csv")
        raise AssertionError("did not raise")
    except RuntimeError as e:
        assert "save failed: ZZ4000" in str(e)
    assert site.rollbacks == 1
    assert site.rows == {"ZZ1000": row("ZZ1000", account_name="Old name")}   # its update undone too
    assert site.rebuilds == []


def test_loads_parents_first_as_drafts_and_requests_no_rebuild_for_drafts():
    site = Site(tables={"/f.csv": [GOOD[0], GOOD[2], GOOD[3], GOOD[1]]})   # the heading last in the file
    out = call(site, "load_chart", "/f.csv")
    assert out["loaded"] and site.inserted == ["ZZ9000", "ZZ1000", "ZZ4000"]
    assert {c: (r["status"], r["source"], r["source_note"]) for c, r in site.rows.items()} == {
        c: ("Draft", "Upload", "f.csv") for c in ("ZZ9000", "ZZ1000", "ZZ4000")}
    assert site.rebuilds == []   # nothing Published changed


def test_one_rebuild_only_when_a_published_row_changed():
    existing = [row("ZZ9000", status="Published", is_group=1, is_posting=0, account_type="", statement_section="",
                    account_name="Heading",
                    normal_balance="", time_balance="", fx_method="", lft=1),
                row("ZZ1000", status="Published", parent_account="ZZ9000", account_name="Old cash", lft=2),
                row("ZZ4000", status="Published", parent_account="ZZ9000", account_name="Old sales",
                    account_type="Revenue", statement_section=PL, normal_balance="Credit", time_balance="flow",
                    fx_method="average", lft=3)]
    site = Site(rows=existing, tables={"/f.csv": GOOD})
    out = call(site, "load_chart", "/f.csv")
    assert out["loaded"] and [c["main_account"] for c in out["published_changes"]] == ["ZZ1000", "ZZ4000"]
    assert site.rebuilds == [("ZZ1000", "Chart upload", "chart")]   # one, however many changed
    assert out["build"] == "BAPR-00001"
    assert all(site.rows[c]["status"] == "Published" for c in ("ZZ1000", "ZZ4000"))
    # the same file again: nothing changed, nothing requested
    site.rebuilds.clear()
    out = call(site, "load_chart", "/f.csv")
    assert out["unchanged"] == ["ZZ9000", "ZZ1000", "ZZ4000"] and site.rebuilds == [] and "build" not in out


def test_publish_goes_parent_first_by_lft_with_one_rebuild():
    rows = [row("ZZ1000", parent_account="ZZ9000", lft=2), row("ZZ9000", is_group=1, is_posting=0, lft=1,
                                                              account_type="", statement_section=""),
            row("ZZ0500", parent_account="ZZ9000", lft=3),
            row("ZZ7000", lft=4, status="Published"),                       # already Published: left alone
            row("OT1000", lft=5, chart_of_accounts="OTHER")]                # another chart: left alone
    site = Site(rows=rows)
    out = call(site, "publish_chart", "ZZCOA")
    assert out["published"] == site.saved == ["ZZ9000", "ZZ1000", "ZZ0500"]
    assert site.rebuilds == [("ZZ9000", "Publish chart", "chart")] and out["build"] == "BAPR-00001"
    assert site.rows["OT1000"]["status"] == "Draft"
    assert call(site, "publish_chart", "ZZCOA")["published"] == []   # nothing left: no rebuild either
    assert len(site.rebuilds) == 1


def test_publish_checks_every_draft_before_publishing_any():
    rows = [row("ZZ1000", lft=1), row("ZZ2000", lft=2, account_type="", fx_method=""),
            row("ZZ3000", lft=3, statement_section=PL, fx_method="historical")]
    site = Site(rows=rows)
    msg = refused(lambda: call(site, "publish_chart", "ZZCOA"))
    assert "Nothing was published" in msg and "ZZ2000 cannot be published without account_type" in msg
    assert "ZZ3000" in msg   # every problem, not the first
    assert site.saved == [] and site.rebuilds == []


def test_a_failure_while_publishing_rolls_everything_back():
    site = Site(rows=[row("ZZ1000", lft=1), row("ZZ2000", lft=2)], fail_on="ZZ2000")
    try:
        call(site, "publish_chart", "ZZCOA")
        raise AssertionError("did not raise")
    except RuntimeError:
        pass
    assert site.rollbacks == 1 and site.rebuilds == []
    assert {c: r["status"] for c, r in site.rows.items()} == {"ZZ1000": "Draft", "ZZ2000": "Draft"}


# -- review of #183, item 3: the first chart build on a site that has never built -------------------
# silver_main_accounts+ reads tables other scopes build (bronze, the period and
# reporting hierarchies), so on a new trial-balance-only site the chart build
# fails. The request is still made; the Close Lead is told what to build first.

def test_on_a_warehouse_that_has_never_built_publish_says_to_build_consolidation_first():
    site = Site(rows=[row("ZZ1000", lft=1)], built=False)
    out = call(site, "publish_chart", "ZZCOA")
    assert "load the trial balances and approve a consolidation build first" in out["note"], out
    assert site.rebuilds == [("ZZ1000", "Publish chart", "chart")]   # still requested
    site = Site(rows=[row("ZZ1000", lft=1)], built=True)
    assert call(site, "publish_chart", "ZZCOA").get("note") is None


def test_a_warehouse_that_cannot_be_asked_adds_no_note():
    site = Site(rows=[row("ZZ1000", lft=1)], built=ConnectionError("refused"))
    out = call(site, "publish_chart", "ZZCOA")
    assert out.get("note") is None and len(site.rebuilds) == 1


def test_a_load_that_requests_a_rebuild_on_an_unbuilt_warehouse_says_so():
    existing = [row("ZZ9000", status="Published", is_group=1, is_posting=0, account_type="", statement_section="",
                    normal_balance="", time_balance="", fx_method="", account_name="Heading", lft=1),
                row("ZZ1000", status="Published", parent_account="ZZ9000", account_name="Old cash", lft=2)]
    site = Site(rows=existing, built=False, tables={"/f.csv": GOOD[:3]})
    out = call(site, "load_chart", "/f.csv")
    assert out["loaded"] and out["build"] and "consolidation build first" in out["note"]
    site = Site(built=False, tables={"/f.csv": GOOD})   # Drafts only: no rebuild, no note
    assert call(site, "load_chart", "/f.csv").get("note") is None


def test_a_chart_with_allow_ic_on_every_leaf_loads_and_publishes_cleanly():
    """Guardrail: the real chart has allow_ic=1 on every postable account."""
    head = HEAD + ["allow_ic"]
    table = [head, ["ZZ9000", "Heading", "", "", "", "ZZCOA", ""]] + [
        [f"ZZ1{n}00", f"Asset {n}", "Asset", "BS", "ZZ9000", "ZZCOA", "yes"] for n in range(4)]
    site = Site(tables={"/ic.csv": table})
    out = call(site, "load_chart", "/ic.csv")
    assert out["loaded"] and out["errors"] == [] and out["not_ready"] == [], out
    assert all(site.rows[f"ZZ1{n}00"]["allow_ic"] == 1 for n in range(4))
    assert len(call(site, "publish_chart", "ZZCOA")["published"]) == 5

