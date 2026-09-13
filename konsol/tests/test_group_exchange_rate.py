"""Group Exchange Rate: the group's governed translation rates (konsol#103).

Decided 13 Sep 2026: one governed rate table owned by group finance; the ERP
feed only pre-fills drafts; per fiscal period, Closing and Average, into a group
reporting currency; submit is the approval; rates lock when their period
closes; a period cannot close without them. The controller, the rules module
and Period Status's gate run here against a stub frappe."""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GER_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "group_exchange_rate")
RULES = os.path.join(APP_DIR, "group_rates.py")


class Refused(Exception):
    pass


def _json():
    with open(os.path.join(GER_DIR, "group_exchange_rate.json")) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _json()["fields"]}


# -- a stub frappe ---------------------------------------------------------------

def _frappe(record, *, period_open=True, group_currencies=("CHF", "USD"), duplicate=None,
            user="approver@example.com", flags=None, fiscal_periods=range(0, 14)):
    frappe = types.ModuleType("frappe")
    for name in ("ValidationError", "MandatoryError", "DuplicateEntryError", "PermissionError"):
        setattr(frappe, name, type(name, (Exception,), {}))

    def throw(msg, exc=None, *a, **k):
        raise Refused(msg)

    def sql(query, params=(), *a, **k):
        record.setdefault("sql", []).append(query)
        if "FROM `tabConsolidation Group`" in query:
            return [(1,)] if params[0] in group_currencies else []
        if "FOR UPDATE" in query:
            return [(duplicate,)] if duplicate else []
        return []

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.flags = types.SimpleNamespace(**(flags or {}))
    frappe.flags.get = lambda k, d=None: getattr(frappe.flags, k, d)
    frappe.session = types.SimpleNamespace(user=user)
    frappe.db = types.SimpleNamespace(
        sql=sql,
        exists=lambda dt, f=None: dt == "Fiscal Period" and f["fiscal_period"] in fiscal_periods,
        add_index=lambda *a, **k: record.setdefault("index", []).append((a, k)),
    )
    return frappe


def _rules_module(frappe, overrides=None):
    """konsol.group_rates, loaded by path against ``frappe``."""
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("konsol.group_rates", RULES)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    for k, v in (overrides or {}).items():
        setattr(module, k, v)
    return module


def _controller(**kw):
    record = {"gates": [], "syncs": []}
    frappe = _frappe(record, **{k: v for k, v in kw.items() if k != "period_open"})
    period_open = kw.get("period_open", True)

    class Document:
        def __init__(self, **fields):
            self.__dict__.update(fields)

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, name, default=None):
            return self.__dict__.get(name, default)

    def assert_open(fiscal_year, fiscal_period, action="run"):
        record["gates"].append((fiscal_year, fiscal_period, action))
        if not period_open:
            raise Refused(f"Cannot {action}: period closed")

    konsol = types.ModuleType("konsol")
    rules = _rules_module(frappe)
    konsol.group_rates = rules
    mods = {n: types.ModuleType(n) for n in (
        "frappe.model", "frappe.model.document", "frappe.model.naming", "konsol.clickhouse",
        "konsol.period_status")}
    mods.update({"frappe": frappe, "konsol": konsol, "konsol.group_rates": rules})
    mods["frappe.model.document"].Document = Document
    mods["frappe.model.naming"].make_autoname = lambda key, doc=None: key.replace(".##", "01")
    mods["konsol.clickhouse"].sync_doctype_after_commit = lambda *a: record["syncs"].append(a)
    mods["konsol.period_status"].assert_open = assert_open
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("ger_under_test", os.path.join(GER_DIR, "group_exchange_rate.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    # validate imports `from konsol import group_rates` at call time
    sys.modules.setdefault("konsol", konsol)
    return module, record


def _doc(module, **fields):
    base = dict(doctype="Group Exchange Rate", name="GER-2099-12-EUR-CHF-Closing-01", docstatus=0,
                to_currency="CHF", from_currency="EUR", rate_type="Closing", fiscal_year=2099,
                fiscal_period=12, rate=0.9478, source="Manual")
    return module.GroupExchangeRate(**dict(base, **fields))


def _refused(fn, *a):
    try:
        fn(*a)
    except Refused:
        return True
    return False


def _with_konsol(module_record_pair, fn):
    """Run ``fn`` with the stub konsol importable (validate's local import)."""
    module, record = module_record_pair
    saved_k, saved_r = sys.modules.get("konsol"), sys.modules.get("konsol.group_rates")
    return module, record


# -- the doctype ------------------------------------------------------------------

def test_the_grain_and_the_approval_shape():
    d, f = _json(), _fields()
    assert d["is_submittable"] == 1 and d["module"] == "Consolidation"
    for name in ("to_currency", "from_currency"):
        assert (f[name]["fieldtype"], f[name]["options"], f[name]["reqd"]) == ("Link", "ISO Currency", 1)
    assert f["rate_type"]["options"].split("\n") == ["Closing", "Average"], "historical rates stay in Historical Equity Rate"
    assert (f["fiscal_year"]["fieldtype"], f["fiscal_period"]["fieldtype"]) == ("Int", "Int")
    assert f["rate"]["fieldtype"] == "Float" and f["rate"]["precision"] == "9"
    assert f["amended_from"]["options"] == "Group Exchange Rate"
    assert f["change_reason"]["mandatory_depends_on"] == "eval:doc.amended_from"
    assert f["source"]["read_only"] == 1 and f["source"]["options"].split("\n") == ["Manual", "ERP pre-fill", "Adoption"]


def test_group_accountants_draft_and_the_close_lead_approves():
    """Submit is the approval (12 Sep conventions): the approver role holds
    submit and cancel; EPM Analyst drafts and may not."""
    perms = {p["role"]: p for p in _json()["permissions"]}
    approvers = {r for r, p in perms.items() if p.get("submit")}
    assert approvers == {"System Manager", "EPM Admin"}
    assert {r for r, p in perms.items() if p.get("cancel")} == approvers
    analyst = perms["EPM Analyst"]
    assert analyst.get("create") and analyst.get("write") and not analyst.get("submit")
    for role in ("EPM User", "Entity Accountant"):
        assert perms[role].get("read") and not perms[role].get("write")


# -- the controller ----------------------------------------------------------------

def _validate(**fields):
    module, record = _controller(**fields.pop("_ctx", {}))
    d = _doc(module, **fields)
    saved = sys.modules.get("konsol.group_rates"), sys.modules.get("konsol")
    rules = _rules_module(_frappe({}))
    konsol = types.ModuleType("konsol")
    konsol.group_rates = rules
    sys.modules["konsol"], sys.modules["konsol.group_rates"] = konsol, rules
    try:
        refused = _refused(d.validate)
    finally:
        for name, old in zip(("konsol.group_rates", "konsol"), saved):
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    return refused, d


def test_a_plausible_true_rate_is_accepted():
    assert not _validate()[0]
    assert not _validate(from_currency="JPY", to_currency="USD", rate=0.00664305)[0]
    assert not _validate(from_currency="USD", to_currency="CHF", rate_type="Average", rate=0.8762)[0]


def test_the_guards_refuse():
    assert _validate(rate=0)[0]
    assert _validate(rate=-0.9)[0]
    assert _validate(rate=94.78)[0], "#138: 94.78 for 0.9478 is a scaling error"
    assert _validate(from_currency="JPY", to_currency="USD", rate=0.0000066)[0]
    assert _validate(from_currency="CHF", to_currency="CHF", rate=1)[0], "no rate into itself"
    assert _validate(rate_type="Default")[0]
    assert _validate(to_currency="EUR")[0], "EUR is no group's reporting currency here"
    assert _validate(fiscal_period=14)[0]
    assert _validate(fiscal_year=2150)[0]


def test_an_amendment_must_say_why():
    assert _validate(amended_from="GER-2099-12-EUR-CHF-Closing-01", change_reason="")[0]
    assert not _validate(amended_from="GER-2099-12-EUR-CHF-Closing-01", change_reason="Board rate")[0]


def test_a_pre_filled_rate_a_person_changed_is_manual():
    _, d = _validate(source="ERP pre-fill", erp_rate=0.9478, rate=0.95)
    assert d.source == "Manual"
    _, d = _validate(source="ERP pre-fill", erp_rate=0.9478, rate=0.9478)
    assert d.source == "ERP pre-fill"


def test_submit_is_gated_on_the_open_period():
    module, record = _controller(period_open=False)
    assert _refused(_doc(module).before_submit)
    assert record["gates"] == [(2099, 12, "approve a group exchange rate")]
    module, record = _controller(period_open=True)
    _doc(module).before_submit()
    assert record["gates"] and any("FOR UPDATE" in q for q in record["sql"])


def test_only_the_system_adoption_skips_the_lock():
    ctx = dict(period_open=False, flags={"konsol_adopting_rates": True}, user="Administrator")
    module, record = _controller(**ctx)
    _doc(module, source="Adoption").before_submit()
    assert record["gates"] == [], "the adoption records a closed period's rate as already translated"
    module, _ = _controller(**ctx)
    assert _refused(_doc(module, source="Manual").before_submit), "only rows labelled Adoption"
    module, _ = _controller(**dict(ctx, user="someone@example.com"))
    assert _refused(_doc(module, source="Adoption").before_submit), "only the system"
    module, _ = _controller(period_open=False, user="Administrator")
    assert _refused(_doc(module, source="Adoption").before_submit), "only inside the adoption run"


def test_a_second_approved_rate_for_one_key_is_refused():
    module, _ = _controller(duplicate="GER-2099-12-EUR-CHF-Closing-01")
    assert _refused(_doc(module, name="GER-2099-12-EUR-CHF-Closing-02").before_submit)


def test_cancel_is_gated_and_a_cancelled_rate_is_kept():
    module, record = _controller(period_open=False)
    assert _refused(_doc(module, docstatus=1).before_cancel)
    assert record["gates"] == [(2099, 12, "cancel a group exchange rate")]
    module, _ = _controller()
    assert _refused(_doc(module, docstatus=2).on_trash)
    _doc(module, docstatus=0).on_trash()


def test_submitted_rows_write_through_after_the_commit():
    module, record = _controller()
    d = _doc(module)
    d.on_submit()
    d.on_cancel()
    assert record["syncs"] == [("Group Exchange Rate", module.GroupExchangeRate.CH_TABLE,
                                module.GroupExchangeRate.CH_FIELD_MAP)] * 2
    assert module.GroupExchangeRate.CH_TABLE == "epm_staging.group_exchange_rates"
    module.on_doctype_update()
    assert record["index"][0][0][1] == ["to_currency", "from_currency", "fiscal_year", "fiscal_period", "rate_type"]


def test_the_staging_ddl_matches_the_field_map():
    """Keep in step with konsolidat's clickhouse/init-db.sql (the same body)."""
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        tree = ast.parse(f.read())
    ddl = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
               and getattr(n.targets[0], "id", "") == "_REFERENCE_TABLE_DDL")
    body = ddl["epm_staging.group_exchange_rates"]
    assert body == (
        "(to_currency String, from_currency String, fiscal_year UInt16, "
        "fiscal_period UInt8, rate_type String, rate Float64, document String) "
        "ENGINE = MergeTree ORDER BY (to_currency, from_currency, fiscal_year, fiscal_period, rate_type)")
    module, _ = _controller()
    cols = [c.strip().split()[0] for c in body[1:body.index(")")].split(",")]
    assert cols == list(module.GroupExchangeRate.CH_FIELD_MAP)


# -- the rules ------------------------------------------------------------------------

def _rules(**overrides):
    return _rules_module(_frappe({}), overrides)


def test_the_magnitude_guard_is_the_dbt_one():
    """#138: the same bands as konsolidat's assert_exchange_rate_sane_magnitude."""
    r = _rules()
    assert r.WIDE_BAND == {"JPY", "KRW", "IDR", "VND", "HUF", "CLP", "ISK", "INR", "RUB", "PHP", "TRY", "THB", "CZK"}
    assert (r.TIGHT_BOUNDS, r.WIDE_BOUNDS) == ((0.05, 20.0), (0.0001, 10000.0))
    assert r.magnitude_problem("EUR", "USD", 1.08) is None
    assert "outside [0.05, 20]" in r.magnitude_problem("EUR", "USD", 108.9)
    assert r.magnitude_problem("USD", "JPY", 150.5) is None
    assert r.magnitude_problem("JPY", "USD", 0.00000664)


def test_the_period_date_is_the_warehouse_one():
    r = _rules()
    assert str(r.period_start(2024, 3)) == "2024-03-01"
    assert str(r.period_start(2024, 0)) == "2024-01-01"
    assert str(r.period_start(2024, 13)) == "2024-12-01"


ROWS = [
    ("d365_fo", "Closing", "EUR", "CHF", 0.9478, "2024-03-01"),
    ("d365_fo", "Default", "EUR", "CHF", 0.945, "2024-03-01"),
    ("d365_fo", "Closing", "USD", "JPY", 150.0, "2024-03-01"),
    ("d365_fo", "Closing", "EUR", "USD", 1.08, "2024-03-01"),
    ("d365_fo", "Closing", "GBP", "USD", 1.27, "2024-03-01"),
    ("d365_fo", "Default", "GBP", "CHF", 1.11, "2024-03-01"),
    ("erpnext", "Closing", "EUR", "CHF", 0.9480, "2024-03-01"),
]


def test_quotes_direct_inverse_and_a_derived_cross():
    r = _rules()
    [q] = [q for q in r.resolve_quotes(ROWS, "EUR", "CHF", "Closing") if q["source"] == "d365_fo"]
    assert (q["rate"], q["how"], q["erp_type"]) == (0.9478, "direct", "Closing")
    [q] = r.resolve_quotes(ROWS, "JPY", "USD", "Closing")
    assert q["how"] == "inverse of USD→JPY" and abs(q["rate"] - 1 / 150.0) < 1e-9  # stored at 9 dp
    [q] = r.resolve_quotes(ROWS, "GBP", "EUR", "Closing")
    assert q["how"] == "cross via USD" and abs(q["rate"] - 1.27 / 1.08) < 1e-9


def test_average_falls_back_to_the_erp_default_type_and_says_so():
    r = _rules()
    [q] = r.resolve_quotes(ROWS[:2], "EUR", "CHF", "Average")
    assert (q["erp_type"], q["rate"]) == ("Default", 0.945)
    assert r.resolve_quotes(ROWS, "EUR", "SEK", "Closing") == []


def test_two_erp_sources_are_both_shown_and_a_disagreement_is_flagged():
    r = _rules()
    quotes = r.resolve_quotes(ROWS, "EUR", "CHF", "Closing")
    assert [q["source"] for q in quotes] == ["d365_fo", "erpnext"]
    note = r.describe_quotes(quotes, 2024, 3)
    assert note.startswith("ERP SOURCES DISAGREE") and "0.9478" in note and "0.948" in note


def test_the_pre_fill_proposes_drafts_and_never_submits():
    src = open(RULES).read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "prefill_from_erp")
    assert [ast.unparse(d) for d in fn.decorator_list] == ["frappe.whitelist(methods=['POST'])"]
    body = ast.unparse(fn)
    assert "frappe.only_for(PREFILL_ROLES)" in body
    assert ".submit(" not in body and "docstatus" not in body.replace("'docstatus': ['<', 2]", "")
    assert _rules().PREFILL_ROLES == ("EPM Analyst", "EPM Admin", "System Manager")


def test_the_pre_fill_in_action():
    inserted = []
    frappe = _frappe({})
    frappe.only_for = lambda roles: inserted.append(("roles", roles))
    frappe.get_all = lambda *a, **k: [types.SimpleNamespace(from_currency="GBP", to_currency="CHF",
                                                             rate_type="Closing")]

    class Doc(dict):
        def __init__(self, d):
            super().__init__(d)
            self.name = f"GER-{d['from_currency']}-{d['rate_type']}"

        def insert(self):
            if self["rate"] > 20:
                raise frappe.ValidationError("outside [0.05, 20]")
            inserted.append(dict(self))
    frappe.get_doc = Doc
    r = _rules_module(frappe, {
        "required_pairs": lambda fy, fp: {("EUR", "CHF"), ("GBP", "CHF"), ("SEK", "CHF"), ("XAU", "CHF")},
        "erp_quote_rows": lambda as_of: ROWS + [("d365_fo", "Closing", "XAU", "CHF", 2100.0, "2024-03-01")],
    })
    out = r.prefill_from_erp("2024", "3")
    assert inserted[0] == ("roles", ("EPM Analyst", "EPM Admin", "System Manager"))
    drafts = inserted[1:]
    assert {(d["from_currency"], d["rate_type"]) for d in drafts} == {("EUR", "Closing"), ("EUR", "Average"), ("GBP", "Average")}
    assert all(d["source"] == "ERP pre-fill" and d["erp_rate"] == d["rate"] and "docstatus" not in d for d in drafts)
    eur_avg = next(d for d in drafts if d["from_currency"] == "EUR" and d["rate_type"] == "Average")
    assert eur_avg["rate"] == 0.945 and "Default" in eur_avg["source_note"]
    assert out["existing"] == ["GBP → CHF Closing"]
    assert "SEK → CHF Closing" in out["no_quote"]
    assert any(x.startswith("XAU → CHF Closing") for x in out["refused"])


def _gate(pairs=None, approved=(), error=None):
    frappe = _frappe({})
    frappe.get_all = lambda *a, **k: [types.SimpleNamespace(from_currency=f, to_currency=t, rate_type=rt)
                                      for f, t, rt in approved]

    def required(fy, fp):
        if error:
            raise error
        return set(pairs or ())
    return _rules_module(frappe, {"required_pairs": required})


def test_the_close_gate():
    pairs = {("EUR", "CHF"), ("JPY", "USD")}
    full = [(f, t, rt) for f, t in pairs for rt in ("Closing", "Average")]
    assert not _refused(_gate(pairs, full).assert_rates_complete, 2024, 3)
    r = _gate(pairs, full[:-1])
    try:
        r.assert_rates_complete(2024, 3)
    except Refused as e:
        assert "no approved group exchange rate for" in str(e) and str(e).count("→") == 1
    else:
        raise AssertionError("a missing rate must refuse the close")
    assert not _refused(_gate(set()).assert_rates_complete, 2024, 3), "no ledgers, nothing owed"


def test_the_close_gate_fails_closed_when_the_warehouse_cannot_answer():
    assert _refused(_gate(error=ConnectionError("down")).assert_rates_complete, 2024, 3)
    unknown = Exception("Code: 60. DB::Exception: Unknown table ... (UNKNOWN_TABLE)")
    assert not _refused(_gate(error=unknown).assert_rates_complete, 2024, 3), "never built: no ledgers"


def test_the_adoption_plan():
    r = _rules()
    used = [
        ("EUR", "CHF", 2024, 3, [0.9478], [0.9422]),
        ("JPY", "USD", 2024, 3, [1.0], [0.0066]),
        ("GBP", "USD", 2024, 3, [1.27, 1.28], [1.26]),
    ]
    actions = r.plan_adoption(used, {("GBP", "USD", 2024, 3, "Average")}, {(2024, 3): ROWS}, "2026-09-13")
    by_key = {a[1]: a for a in actions}
    eur = by_key[("EUR", "CHF", 2024, 3, "Closing")]
    assert eur[0] == "adopt" and eur[2] == 0.9478 and eur[3] == 0.9478
    assert "d365_fo Closing quote" in eur[4] and "Authored by the system" in eur[4]
    avg = by_key[("EUR", "CHF", 2024, 3, "Average")]
    assert avg[0] == "adopt" and avg[3] is None and "matches no current ERP quote" in avg[4]
    assert by_key[("JPY", "USD", 2024, 3, "Closing")][0] == "skip" and "1.0 parity" in by_key[("JPY", "USD", 2024, 3, "Closing")][2]
    assert by_key[("GBP", "USD", 2024, 3, "Closing")][0] == "skip"
    assert by_key[("GBP", "USD", 2024, 3, "Average")][2] == "already has an approved group rate"


def test_the_adoption_runs_as_the_system_only_and_a_dry_run_writes_nothing():
    frappe = _frappe({}, user="someone@example.com")
    assert _refused(_rules_module(frappe).adopt_erp_rates)
    frappe = _frappe({}, user="Administrator")
    frappe.get_all = lambda *a, **k: []
    frappe.get_doc = lambda d: (_ for _ in ()).throw(AssertionError("a dry run wrote"))
    frappe.utils = types.SimpleNamespace(today=lambda: "2026-09-13")
    frappe.logger = lambda *a: types.SimpleNamespace(info=lambda *a: None)
    r = _rules_module(frappe, {
        "translated_rates": lambda: [("EUR", "CHF", 2024, 3, [0.9478], [0.9422])],
        "erp_quote_rows": lambda as_of: ROWS})
    out = r.adopt_erp_rates(dry_run=True)
    assert len(out["adopted"]) == 2 and frappe.flags.konsol_adopting_rates is False


def test_the_upgrade_patch_loads_the_doctype_then_adopts():
    with open(os.path.join(APP_DIR, "patches", "adopt_erp_rates_as_group_exchange_rates.py")) as f:
        body = f.read()
    assert body.index('frappe.reload_doc("consolidation", "doctype", "group_exchange_rate")') < body.index("adopt_erp_rates()")
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        assert "konsol.patches.adopt_erp_rates_as_group_exchange_rates" in [l.strip() for l in f]


# -- Period Status: the close gate is wired in -----------------------------------------------

def _period_status(previous, status):
    calls = []
    frappe = _frappe({})
    frappe.get_roles = lambda: ["System Manager"]
    frappe.db.exists = lambda *a, **k: True
    frappe.db.get_value = lambda *a, **k: previous
    mods = {n: types.ModuleType(n) for n in ("frappe.model", "frappe.model.document", "frappe.utils",
                                             "konsol", "konsol.group_rates")}
    mods["frappe"] = frappe
    mods["frappe.model.document"].Document = type("Document", (), {
        "__init__": lambda self, **kw: self.__dict__.update(kw), "is_new": lambda self: previous is None})
    mods["frappe.utils"].now_datetime = lambda: "NOW"
    mods["konsol.group_rates"].assert_rates_complete = lambda fy, fp: calls.append((fy, fp))
    mods["konsol"].group_rates = mods["konsol.group_rates"]
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location(
            "ps_under_test", os.path.join(APP_DIR, "epm", "doctype", "period_status", "period_status.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.PeriodStatus(name="PS-2024-3", fiscal_year="2024", fiscal_period=3, status=status).validate()
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return calls


def test_closing_a_period_checks_its_group_rates():
    assert _period_status("Open", "Closed") == [("2024", 3)]
    assert _period_status(None, "Locked") == [("2024", 3)]
    assert _period_status("Closed", "Locked") == []
    assert _period_status("Closed", "Open") == []
    assert _period_status("Open", "Open") == []


# -- wiring -----------------------------------------------------------------------------------

def test_an_approved_rate_requests_a_consolidation_build():
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    trig = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", "") == "_dbt_trigger_doctypes")
    assert "Group Exchange Rate" in trig
    with open(os.path.join(APP_DIR, "tasks.py")) as f:
        tree = ast.parse(f.read())
    build_map = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                     and getattr(n.targets[0], "id", "") == "DOCTYPE_BUILD_MAP")
    assert build_map["Group Exchange Rate"]["scope"] == "consolidation"
