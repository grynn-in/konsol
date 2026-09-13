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

#: What the ISO Currency fixture says, for the stub (a subset; XAU has none).
REFS = {"USD": 0.0, "EUR": -0.03, "CHF": -0.05, "GBP": -0.1, "JPY": 2.17, "KRW": 3.13, "IDR": 4.21,
        "VND": 4.40, "ARS": 3.1, "SEK": 1.02, "XAU": 0.0}


def _frappe(record, *, period_open=True, group_currencies=("CHF", "USD"), duplicate=None,
            user="approver@example.com", flags=None, fiscal_periods=range(0, 14), refs=None, previous=None):
    frappe = types.ModuleType("frappe")
    for name in ("ValidationError", "MandatoryError", "DuplicateEntryError", "PermissionError"):
        setattr(frappe, name, type(name, (Exception,), {}))
    refs = REFS if refs is None else refs

    def throw(msg, exc=None, *a, **k):
        record.setdefault("thrown", []).append(getattr(exc, "__name__", None))
        raise Refused(msg)

    def sql(query, params=(), *a, **k):
        record.setdefault("sql", []).append(query)
        if "FROM `tabConsolidation Group`" in query:
            return [(1,)] if params[0] in group_currencies else []
        if "FOR UPDATE" in query:
            return [(duplicate,)] if duplicate else []
        if "ORDER BY fiscal_year DESC" in query:   # group_rates.previous_approved
            return [previous] if previous else []
        return []

    def get_all(doctype, filters=None, fields=None, **k):
        if doctype == "ISO Currency":
            return [types.SimpleNamespace(name=c, usd_log10=refs[c], get=lambda f, c=c: refs[c])
                    for c in filters["name"][1] if c in refs]
        return []

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.flags = types.SimpleNamespace(**(flags or {}))
    frappe.flags.get = lambda k, d=None: getattr(frappe.flags, k, d)
    frappe.session = types.SimpleNamespace(user=user)
    frappe.get_all = get_all
    frappe.clear_last_message = lambda: record.setdefault("cleared", []).append(1)
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
        """A draft: new unless ``_before`` (the saved version) is given."""
        def __init__(self, **fields):
            self.__dict__.update(fields)

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, name, default=None):
            return self.__dict__.get(name, default)

        def is_new(self):
            return self.__dict__.get("_before") is None

        def get_doc_before_save(self):
            before = self.__dict__.get("_before")
            return types.SimpleNamespace(**before) if before is not None else None

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
    """(refused, doc, message) for one validate() of a draft with ``fields``."""
    ctx = fields.pop("_ctx", {})
    module, record = _controller(**ctx)
    d = _doc(module, **fields)
    saved = sys.modules.get("konsol.group_rates"), sys.modules.get("konsol")
    rules = _rules_module(_frappe({}, **{k: v for k, v in ctx.items() if k in ("refs", "previous")}))
    konsol = types.ModuleType("konsol")
    konsol.group_rates = rules
    sys.modules["konsol"], sys.modules["konsol.group_rates"] = konsol, rules
    message = None
    try:
        d.validate()
    except Refused as e:
        message = str(e)
    finally:
        for name, old in zip(("konsol.group_rates", "konsol"), saved):
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    return message is not None, d, message


def test_a_plausible_true_rate_is_accepted():
    assert not _validate()[0]
    assert not _validate(from_currency="USD", to_currency="CHF", rate_type="Average", rate=0.8762)[0]
    # a small rate is quoted the other way round: 150.53 JPY per USD
    assert not _validate(from_currency="JPY", to_currency="USD", rate=150.53, inverse_quote=1)[0]


def test_idr_and_vnd_into_usd_pass():
    """The #138 wide band [1e-4, 1e4] refused both, so a group holding either
    could never close."""
    assert not _validate(from_currency="IDR", to_currency="USD", rate=16350.5, inverse_quote=1)[0]
    assert not _validate(from_currency="VND", to_currency="USD", rate=25380, inverse_quote=1)[0]
    refused, _, msg = _validate(from_currency="IDR", to_currency="USD", rate=0.0000611)
    assert refused and "below 0.1" in msg and "IDR per 1 USD" in msg, "entered the other way round"


def test_a_100x_error_on_jpy_is_refused():
    """The wide band could not catch it; the reference can."""
    refused, _, msg = _validate(from_currency="JPY", to_currency="USD", rate=15053, inverse_quote=1)
    assert refused and "x below" in msg and "(#138)" in msg
    assert _validate(from_currency="JPY", to_currency="USD", rate=1.5053, inverse_quote=1)[0]
    assert _validate(from_currency="USD", to_currency="CHF", rate=87.62)[0]


def test_a_currency_with_no_reference_is_refused():
    refused, _, msg = _validate(from_currency="XAU", rate=2100.0)
    assert refused and "set USD Reference (log10) (usd_log10) on ISO Currency XAU" in msg
    refs = dict(REFS, EUR=0.0)
    assert _validate(_ctx={"refs": refs})[0], "0 is 'not set' for any currency but USD"
    assert not _validate(from_currency="USD", to_currency="CHF", rate=0.8762,
                         _ctx={"refs": dict(REFS, USD=0.0)})[0], "USD is the anchor"


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


def test_a_move_over_half_needs_a_reason():
    """Soft: ARS fell 55% in Dec 2023, so a big move is allowed with a reason."""
    ars = dict(from_currency="ARS", to_currency="USD", rate=808.45, inverse_quote=1)
    previous = ("GER-2099-11-ARS-USD-Closing-01", 366.5, 1, 2099, 11)
    refused, _, msg = _validate(_ctx={"previous": previous}, **ars)
    assert refused and "-55% from the previous approved rate FY2099 P11" in msg
    assert not _validate(_ctx={"previous": previous}, change_reason="Devaluation, 13 Dec", **ars)[0]
    assert not _validate(_ctx={"previous": ("GER-P11", 800.0, 1, 2099, 11)}, **ars)[0], "a small move"
    # from the ERP quote it was proposed from (same direction as the rate)
    refused, _, msg = _validate(rate=1.5, erp_rate=0.9478, source="Manual",
                                _before=dict(source="Manual", erp_rate=0.9478, inverse_quote=0))
    assert refused and "from the ERP quote" in msg
    # an amendment always needs one
    assert _validate(amended_from="GER-2099-12-EUR-CHF-Closing-01", change_reason="")[0]


def test_an_amendment_must_say_why():
    assert _validate(amended_from="GER-2099-12-EUR-CHF-Closing-01", change_reason="")[0]
    assert not _validate(amended_from="GER-2099-12-EUR-CHF-Closing-01", change_reason="Board rate")[0]


def test_a_pre_filled_rate_a_person_changed_is_manual():
    prefilled = dict(source="ERP pre-fill", erp_rate=0.9478, inverse_quote=0)
    _, d, _ = _validate(source="ERP pre-fill", erp_rate=0.9478, rate=0.95, _before=prefilled)
    assert d.source == "Manual"
    _, d, _ = _validate(source="ERP pre-fill", erp_rate=0.9478, rate=0.9478, _before=prefilled)
    assert d.source == "ERP pre-fill"
    # turning the direction round is a change too, and the quote follows it
    refused, d, _ = _validate(source="ERP pre-fill", erp_rate=1.1236, rate=1.1236, inverse_quote=0,
                             from_currency="USD", to_currency="CHF", change_reason="flip",
                             _before=dict(prefilled, erp_rate=1.1236, inverse_quote=1))
    assert not refused and d.source == "Manual" and abs(d.erp_rate - 1 / 1.1236) < 1e-9


def test_a_forged_source_is_refused():
    """source and erp_rate are read-only in the form only: REST writes them."""
    refused, _, msg = _validate(source="Adoption")
    assert refused and "one-time rate adoption" in msg
    refused, _, msg = _validate(source="ERP pre-fill", erp_rate=0.9478)
    assert refused and "Pre-fill from ERP" in msg
    assert _validate(source="Manual", erp_rate=0.9478)[0], "an ERP quote only the pre-fill records"
    refused, _, _ = _validate(source="Adoption", _before=dict(source="Manual", erp_rate=0))
    assert refused, "relabelling a saved rate"
    # the real writers, each under its own flag
    assert not _validate(source="ERP pre-fill", erp_rate=0.9478,
                         _ctx={"flags": {"konsol_prefilling_rates": True}})[0]
    assert not _validate(source="Adoption", _ctx={"flags": {"konsol_adopting_rates": True},
                                                  "user": "Administrator"})[0]
    assert _validate(source="Adoption", _ctx={"flags": {"konsol_adopting_rates": True}})[0], "only the system"
    # a saved pre-filled draft keeps its label when a person (no flag) saves it again
    assert not _validate(source="ERP pre-fill", erp_rate=0.9478,
                         _before=dict(source="ERP pre-fill", erp_rate=0.9478, inverse_quote=0))[0]


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
        "fiscal_period UInt8, rate_type String, rate Float64, document String, inverse_quote UInt8 DEFAULT 0) "
        "ENGINE = MergeTree ORDER BY (to_currency, from_currency, fiscal_year, fiscal_period, rate_type)")
    module, _ = _controller()
    cols = [c.strip().split()[0] for c in body[1:body.index(")")].split(",")]
    assert cols == list(module.GroupExchangeRate.CH_FIELD_MAP)


def test_a_small_rate_is_quoted_the_other_way_round():
    """A Frappe Float is decimal(21,9): KRW -> USD 1/1350 would keep about 6
    significant digits, IDR fewer. The quote is kept where it has its digits,
    and the warehouse inverts it (inverse_quote)."""
    f = _fields()
    assert f["inverse_quote"]["fieldtype"] == "Check"
    r = _rules()
    assert r.as_stored(0.9478) == (0.9478, 0)
    assert r.as_stored(1 / 150) == (150.0, 1), "the ERP's USD -> JPY 150 stays exactly 150"
    quote, inverse = r.as_stored(1 / 16350.123456)
    assert inverse == 1 and abs(quote - 16350.123456) < 1e-6
    assert abs(r.effective_rate(quote, inverse) * 16350.123456 - 1) < 1e-12
    assert r.effective_rate(0.9478, 0) == 0.9478 and r.effective_rate(0, 1) == 0.0


# -- the rules ------------------------------------------------------------------------

def _rules(**overrides):
    return _rules_module(_frappe({}), overrides)


def test_the_magnitude_guard_reads_iso_currency():
    """One rule and one home: ISO Currency.usd_log10 (roughly log10 of units per
    1 USD). Refused when abs(log10(rate) - (usd_log10(to) - usd_log10(from))) > 1.
    No list of currencies and no bounds live in konsol any more."""
    src = open(RULES).read()
    assert "WIDE_BAND" not in src and "TIGHT_BOUNDS" not in src and "WIDE_BOUNDS" not in src
    r = _rules()
    assert (r.REFERENCE_FIELD, r.REFERENCE_CURRENCY, r.MAGNITUDE_TOLERANCE_DECADES) == ("usd_log10", "USD", 1.0)
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "usd_references")
    assert '"ISO Currency"' in ast.unparse(fn).replace("'", '"')
    body = ast.unparse(next(n for n in ast.parse(src).body
                            if isinstance(n, ast.FunctionDef) and n.name == "magnitude_problem"))
    assert "math.log10(rate) - expected" in body and "refs[to_currency] - refs[from_currency]" in body
    assert "abs(off) <= MAGNITUDE_TOLERANCE_DECADES" in body
    assert r.magnitude_problem("EUR", "USD", 1.08) is None
    assert "x above" in r.magnitude_problem("EUR", "USD", 108.9)
    assert r.magnitude_problem("USD", "JPY", 150.5) is None
    assert r.magnitude_problem("JPY", "USD", 0.00000664)
    assert r.magnitude_problem("IDR", "USD", 1 / 16350) is None
    assert r.magnitude_problem("VND", "USD", 1 / 25380) is None
    # the edge: 10x from the reference passes, just over it doesn't
    assert r.magnitude_problem("USD", "SEK", 10 ** 1.02 * 9.9) is None
    assert r.magnitude_problem("USD", "SEK", 10 ** 1.02 * 10.1)


def test_the_move_rule():
    r = _rules()
    assert r.move_problem(1.0, (1.4, "P11"), None) is None, "-29%"
    assert "-55%" in r.move_problem(0.45, (1.0, "P11"), None)
    assert "+60%" in r.move_problem(1.6, None, 1.0)
    assert r.move_problem(1.6, None, None) is None, "no previous rate, no quote: nothing to compare"
    assert r.MOVE_NEEDS_REASON == 0.5


def test_every_iso_currency_has_a_reference():
    with open(os.path.join(APP_DIR, "fixtures", "iso_currency.json")) as f:
        rows = json.load(f)
    by_code = {r["currency_code"]: r.get("usd_log10") for r in rows}
    assert len(by_code) == 66
    unset = sorted(c for c, v in by_code.items() if not isinstance(v, (int, float)) or (v == 0 and c != "USD"))
    assert not unset, unset
    assert all(-5 <= v <= 10 for v in by_code.values())
    # the anchors the rule was specified with
    for code, value in {"USD": 0, "EUR": -0.03, "JPY": 2.17, "KRW": 3.13, "IDR": 4.21, "VND": 4.40}.items():
        assert abs(by_code[code] - value) < 0.005, code
    with open(os.path.join(APP_DIR, "epm", "doctype", "iso_currency", "iso_currency.json")) as f:
        field = next(x for x in json.load(f)["fields"] if x["fieldname"] == "usd_log10")
    assert field["fieldtype"] == "Float"
    with open(os.path.join(APP_DIR, "epm", "doctype", "iso_currency", "iso_currency.py")) as f:
        assert '"usd_log10": "usd_log10"' in f.read(), "written through to epm_gold.currencies"


def test_the_currencies_ddl_carries_the_reference():
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        tree = ast.parse(f.read())
    consts = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
              and getattr(n.targets[0], "id", "") in ("_REFERENCE_TABLE_DDL", "_ADDED_COLUMNS")}
    # identical to konsolidat's clickhouse/init-db.sql; NaN = never synced
    assert consts["_REFERENCE_TABLE_DDL"]["epm_gold.currencies"] == (
        "(currency_code String, currency_name String, symbol String, minor_unit UInt8, "
        "usd_log10 Float64 DEFAULT nan) "
        "ENGINE = MergeTree ORDER BY currency_code")
    # a table that already shipped gets the column on the next migrate, at the
    # end of its CREATE so a fresh table and an upgraded one agree
    added = consts["_ADDED_COLUMNS"]
    assert ("usd_log10", "Float64 DEFAULT nan") in added["epm_gold.currencies"]
    assert ("inverse_quote", "UInt8 DEFAULT 0") in added["epm_staging.group_exchange_rates"]
    for table in ("epm_gold.currencies", "epm_staging.group_exchange_rates"):
        body = consts["_REFERENCE_TABLE_DDL"][table]
        columns = body[:body.index(") ENGINE")]
        for name, typ in added[table]:
            assert columns.endswith(f"{name} {typ}"), (table, name)
    sql = []
    m = _clickhouse_module(lambda s, params=None: sql.append(s) or "")
    m.ensure_reference_tables()
    for stmt in ("ALTER TABLE epm_gold.currencies ADD COLUMN IF NOT EXISTS usd_log10 Float64 DEFAULT nan",
                 "ALTER TABLE epm_staging.group_exchange_rates ADD COLUMN IF NOT EXISTS inverse_quote UInt8 DEFAULT 0"):
        assert stmt in sql and sql.index(stmt) > sql.index(
            "CREATE TABLE IF NOT EXISTS " + stmt.split()[2] + " " + consts["_REFERENCE_TABLE_DDL"][stmt.split()[2]])


def _clickhouse_module(execute):
    """konsol.clickhouse, loaded by path against stubs, with ``execute`` replaced."""
    mods = {n: types.ModuleType(n) for n in ("frappe", "requests", "requests.exceptions")}
    mods["frappe"].logger = lambda *a, **k: types.SimpleNamespace(warning=lambda *a, **k: None,
                                                                  error=lambda *a, **k: None)
    mods["frappe"].flags = types.SimpleNamespace()
    mods["requests"].exceptions = mods["requests.exceptions"]
    for name in ("ConnectionError", "Timeout", "HTTPError"):
        setattr(mods["requests.exceptions"], name, type(name, (Exception,), {}))
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("ch_under_test", os.path.join(APP_DIR, "clickhouse.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    module.execute = execute
    return module


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
    inserted, record = [], {}
    frappe = _frappe(record)
    frappe.only_for = lambda roles: inserted.append(("roles", roles))
    frappe.get_all = lambda *a, **k: [types.SimpleNamespace(from_currency="GBP", to_currency="CHF",
                                                             rate_type="Closing")]

    class Doc(dict):
        def __init__(self, d):
            super().__init__(d)
            self.name = f"GER-{d['from_currency']}-{d['rate_type']}"

        def insert(self):
            assert frappe.flags.konsol_prefilling_rates is True, "the pre-fill's own flag"
            if self["from_currency"] == "SEK":   # the controller refused (e.g. a move needing a reason)
                raise frappe.ValidationError("moves +80%")
            inserted.append(dict(self))
    frappe.get_doc = Doc
    r = _rules_module(frappe, {
        "required_pairs": lambda fp, fy: {("EUR", "CHF"), ("GBP", "CHF"), ("SEK", "CHF"), ("XAU", "CHF"),
                                          ("JPY", "CHF"), ("NOK", "CHF")},
        "erp_quote_rows": lambda as_of: ROWS + [
            ("d365_fo", "Closing", "XAU", "CHF", 2100.0, "2024-03-01"),
            ("d365_fo", "Closing", "SEK", "CHF", 0.085, "2024-03-01"),
            ("d365_fo", "Closing", "CHF", "JPY", 170.0, "2024-03-01"),
            ("d365_fo", "Closing", "NOK", "CHF", 8.3, "2024-03-01")],
        "usd_references": lambda codes: {c: REFS.get(c) if c != "XAU" else None for c in codes} | {"NOK": 1.03},
    })
    out = r.prefill_from_erp("2024", "3")
    assert inserted[0] == ("roles", ("EPM Analyst", "EPM Admin", "System Manager"))
    drafts = inserted[1:]
    assert {(d["from_currency"], d["rate_type"]) for d in drafts} == {
        ("EUR", "Closing"), ("EUR", "Average"), ("GBP", "Average"), ("JPY", "Closing")}
    assert all(d["source"] == "ERP pre-fill" and d["erp_rate"] == d["rate"] and "docstatus" not in d for d in drafts)
    eur_avg = next(d for d in drafts if d["from_currency"] == "EUR" and d["rate_type"] == "Average")
    assert eur_avg["rate"] == 0.945 and eur_avg["inverse_quote"] == 0 and "Default" in eur_avg["source_note"]
    jpy = next(d for d in drafts if d["from_currency"] == "JPY")
    assert (jpy["rate"], jpy["inverse_quote"]) == (170.0, 1), "the ERP's CHF -> JPY 170, kept exact"
    assert out["existing"] == ["GBP → CHF Closing"]
    assert "SEK → CHF Average" in out["no_quote"]
    refused = dict(x.split(": ", 1) for x in out["refused"])
    assert "No magnitude reference for XAU" in refused["XAU → CHF Closing"]
    assert "x above" in refused["NOK → CHF Closing"], "NOK 8.3 CHF is a scaling error"
    assert refused["SEK → CHF Closing"] == "moves +80%"
    # the guard runs before the insert, so only the controller's refusal logged a message
    assert record.get("cleared") == [1] and frappe.flags.konsol_prefilling_rates is False


def test_the_pre_fill_checks_the_magnitude_before_the_insert():
    """frappe.throw logs to message_log before it raises: a refused proposal
    must not pop a dialog, so the guard runs first and the rest are cleared."""
    body = ast.unparse(next(n for n in ast.parse(open(RULES).read()).body
                            if isinstance(n, ast.FunctionDef) and n.name == "prefill_from_erp"))
    assert body.index("magnitude_problem(") < body.index("doc.insert()")
    assert "frappe.clear_last_message()" in body


def _gate(pairs=None, approved=(), error=None, built=True):
    frappe = _frappe({})
    frappe.get_all = lambda *a, **k: [types.SimpleNamespace(from_currency=f, to_currency=t, rate_type=rt)
                                      for f, t, rt in approved]

    def required(fy, fp):
        if error:
            raise error
        return set(pairs or ())

    def ledgers():
        if isinstance(built, Exception):
            raise built
        return built
    return _rules_module(frappe, {"required_pairs": required, "ledgers_built": ledgers})


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
    unknown = Exception("Code: 60. DB::Exception: Table epm_gold.gold_trial_balance does not exist. "
                        "(UNKNOWN_TABLE) (version 24.8.4.13)")
    no_db = Exception("Code: 81. DB::Exception: Database epm_gold does not exist. (UNKNOWN_DATABASE)")
    assert not _refused(_gate(error=unknown, built=False).assert_rates_complete, 2024, 3), "never built"
    assert not _refused(_gate(error=no_db, built=False).assert_rates_complete, 2024, 3), "never built"
    # a built warehouse missing a table the pairs need (gold_entity_ownership) can't answer
    missing_ownership = Exception("Code: 60. DB::Exception: Table epm_gold.gold_entity_ownership does "
                                  "not exist. (UNKNOWN_TABLE)")
    assert _refused(_gate(error=missing_ownership, built=True).assert_rates_complete, 2024, 3)
    assert _refused(_gate(error=unknown, built=ConnectionError("down")).assert_rates_complete, 2024, 3)
    # the token, not a code substring: Code 600-609 is not UNKNOWN_TABLE
    other = Exception("Code: 605. DB::Exception: something else. (SOME_OTHER_ERROR)")
    assert _refused(_gate(error=other, built=False).assert_rates_complete, 2024, 3)
    assert _rules().ch_error_names(unknown) == {"UNKNOWN_TABLE"}
    assert _gate(error=ConnectionError("down")).rate_gate(2024, 3) == (None, "ConnectionError")


def test_the_gate_asks_only_for_what_translation_translates():
    """#103 review: the gate asked for every ancestor group; translation drops
    equity and 'none' methods, incomplete chains and rows outside the ownership
    window. The pairs now come from gold_entity_ownership with that filter."""
    sql = []
    frappe = _frappe({})
    r = _rules_module(frappe, {"_ch_rows": lambda s, params=None: sql.append((s, params)) or [("IDR", "USD")]})
    assert r.required_pairs(2024, 3) == {("IDR", "USD")}
    [(s, params)] = sql
    assert "epm_gold.gold_entity_ownership" in s and "consolidation_ancestry" not in s
    assert "consolidation_method NOT IN ('equity', 'none') AND has_complete_chain = 1" in s
    assert s.count("fiscal_year = {fy:UInt16} AND fiscal_period = {fp:UInt16}") == 2
    assert params == {"fy": 2024, "fp": 3}


def test_the_gate_reads_existence_before_passing():
    sql = []
    r = _rules_module(_frappe({}), {"_ch_rows": lambda s, params=None: sql.append(s) or [[0]]})
    assert r.ledgers_built() is False and sql == ["EXISTS TABLE epm_gold.gold_trial_balance"]


def _adoption(error):
    frappe = _frappe({}, user="Administrator")
    frappe.local = types.SimpleNamespace(site="konsolidat.local")

    def fail():
        raise error
    return _rules_module(frappe, {"translated_rates": fail})


def test_the_adoption_before_the_warehouse_exists():
    """init.sh can run bench migrate before ClickHouse is ready. A database
    that doesn't exist yet (UNKNOWN_DATABASE) has nothing to adopt, like a
    missing table; a ClickHouse that can't be reached fails loudly and names
    the command that finishes the job."""
    for text in ("Code: 81. DB::Exception: Database epm_gold does not exist. (UNKNOWN_DATABASE)",
                 "Code: 60. DB::Exception: Table epm_gold.gold_consolidated_trial_balance does not exist. "
                 "(UNKNOWN_TABLE)"):
        assert _adoption(Exception(text)).adopt_erp_rates() == {"adopted": [], "skipped": [], "refused": []}
    for error in (ConnectionError("Connection refused"),
                  Exception("Code: 606. DB::Exception: Code: 60 lookalike (SOMETHING_ELSE)")):
        try:
            _adoption(error).adopt_erp_rates()
        except RuntimeError as e:
            assert "bench --site konsolidat.local execute konsol.group_rates.adopt_erp_rates" in str(e)
            assert e.__cause__ is error
        else:
            raise AssertionError("an unreachable warehouse must fail the migrate")


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
