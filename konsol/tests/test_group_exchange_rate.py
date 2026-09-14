"""Group Exchange Rate: the group's governed translation rates (konsol#103).

Decided 13 Sep 2026: one governed rate table owned by group finance, and one
source of truth for FX rates, published by konsol as TRUE rates; the ERP feed
only pre-fills drafts; per fiscal period, Closing and Average, into a group
reporting currency, entered as a quote per 1 / 10 / 100 / 1,000 / 10,000 units;
submit is the approval; rates lock when their period closes; a period cannot
close without them. The controller and the rules module run here against a
stub frappe; the close gate itself is EPM Fiscal Year's (test_fiscal_year_actions)."""
import ast
import calendar
import contextlib
import datetime
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


def _frappe(record, *, group_currencies=("CHF", "USD"), duplicate=None, user="approver@example.com",
            flags=None, refs=None, previous=None):
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
        exists=lambda dt, f=None: False,
        add_index=lambda *a, **k: record.setdefault("index", []).append((a, k)),
    )
    return frappe


def _default_period_dates(fiscal_year, fiscal_period):
    """The test double's default period_status.period_dates: the old month
    arithmetic (dbt build_date_from_year_period), so a test that doesn't care
    about konsol#189's declared calendar needs no changes. Override
    ``period_dates`` (it is imported by name into konsol.group_rates) for a
    test that does."""
    start = datetime.date(max(int(fiscal_year), 1900), min(max(int(fiscal_period), 1), 12), 1)
    end = datetime.date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    return start, end


def _rules_module(frappe, overrides=None):
    """konsol.group_rates, loaded by path against ``frappe``. konsol.period_status
    is stubbed too, so the module's ``from konsol.period_status import
    period_dates, PeriodNotDeclared`` resolves; override ``period_dates`` in
    ``overrides`` for a konsol#189 test (it becomes the module's own global,
    so period_start/period_end read the override on every call)."""
    period_status = types.ModuleType("konsol.period_status")
    period_status.period_dates = _default_period_dates
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    saved = {n: sys.modules.get(n) for n in ("frappe", "konsol.period_status")}
    sys.modules["frappe"] = frappe
    sys.modules["konsol.period_status"] = period_status
    try:
        spec = importlib.util.spec_from_file_location("konsol.group_rates", RULES)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    for k, v in (overrides or {}).items():
        setattr(module, k, v)
    return module


@contextlib.contextmanager
def _konsol(rules, clickhouse=None):
    """The controller's call-time imports (``from konsol import group_rates``,
    ``from konsol.clickhouse import ...``) resolve to these stubs."""
    konsol = types.ModuleType("konsol")
    konsol.group_rates = rules
    mods = {"konsol": konsol, "konsol.group_rates": rules}
    if clickhouse is not None:
        mods["konsol.clickhouse"] = clickhouse
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _controller(**kw):
    record = {"gates": [], "published": []}
    frappe = _frappe(record, **{k: v for k, v in kw.items()
                                if k not in ("period_open", "declared_periods")})
    period_open = kw.get("period_open", True)
    declared_periods = kw.get("declared_periods", {(2099, 12)})

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

        def check_if_latest(self):
            """Frappe's own check_if_latest: this is where document.py:1164's
            FOR UPDATE row lock happens (via load_doc_before_save), for a
            saved document. The overridden check_if_latest must call this
            (recorded here, into the same list as frappe.db.sql) only after
            its own year lock."""
            record.setdefault("sql", []).append("BASE_CHECK_IF_LATEST")

    def assert_open(fiscal_year, fiscal_period, action="run"):
        record["gates"].append((fiscal_year, fiscal_period, action))
        if not period_open:
            raise Refused(f"Cannot {action}: period closed")

    def assert_declared(fiscal_year, fiscal_period):
        record["gates"].append(("declared", fiscal_year, fiscal_period))
        if (int(fiscal_year), int(fiscal_period)) not in declared_periods:
            raise Refused(f"FY{fiscal_year} has no period {fiscal_period}: "
                          "it has not been declared in EPM Fiscal Year.")

    mods = {n: types.ModuleType(n) for n in (
        "frappe.model", "frappe.model.document", "konsol", "konsol.period_status")}
    mods["frappe"] = frappe
    mods["frappe.model.document"].Document = Document
    mods["konsol.period_status"].assert_open = assert_open
    mods["konsol.period_status"].assert_declared = assert_declared
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
    return module, record


def _doc(module, **fields):
    base = dict(doctype="Group Exchange Rate", name="GER-2099-12-EUR-CHF-Closing-01", docstatus=0,
                to_currency="CHF", from_currency="EUR", rate_type="Closing", fiscal_year=2099,
                fiscal_period=12, quote=0.9478, quoted_per="1", source="Manual")
    return module.GroupExchangeRate(**dict(base, **fields))


def _refused(fn, *a):
    try:
        fn(*a)
    except Refused:
        return True
    return False


# -- the doctype ------------------------------------------------------------------

def test_the_grain_and_the_approval_shape():
    d, f = _json(), _fields()
    assert d["is_submittable"] == 1 and d["module"] == "Consolidation"
    for name in ("to_currency", "from_currency"):
        assert (f[name]["fieldtype"], f[name]["options"], f[name]["reqd"]) == ("Link", "ISO Currency", 1)
    assert f["rate_type"]["options"].split("\n") == ["Closing", "Average"], "historical rates stay in Historical Equity Rate"
    assert (f["fiscal_year"]["fieldtype"], f["fiscal_period"]["fieldtype"]) == ("Int", "Int")
    assert f["quote"]["fieldtype"] == "Float" and f["quote"]["precision"] == "9" and f["quote"]["reqd"] == 1
    assert f["quoted_per"]["fieldtype"] == "Select" and f["quoted_per"]["default"] == "1"
    assert f["quoted_per"]["options"].split("\n") == ["1", "10", "100", "1000", "10000"]
    assert f["quote_label"]["read_only"] == 1 and f["quote_label"]["in_list_view"] == 1
    assert "rate" not in f and "inverse_quote" not in f and "erp_rate" not in f
    assert f["erp_quote"]["read_only"] == 1
    assert f["amended_from"]["options"] == "Group Exchange Rate"
    assert f["change_reason"]["mandatory_depends_on"] == "eval:doc.amended_from"
    assert f["source"]["read_only"] == 1 and f["source"]["no_copy"] == 1
    assert f["source"]["options"].split("\n") == ["Manual", "ERP pre-fill", "Adoption"]
    assert set(_json()["field_order"]) == set(f)


def test_fiscal_year_is_indexed_for_the_close_read():
    """PR #191 re-review 2 finding 1: the close's locking read
    (group_rates._approved_keys) filters WHERE fiscal_year = ... AND
    fiscal_period = .... The only index, "grain", leads with to_currency and
    from_currency (group_exchange_rate.py GRAIN / on_doctype_update), which
    that WHERE can't use, so the locking read scans (and locks) the whole
    table. A plain index on fiscal_year lets it narrow the scan to the
    year."""
    assert _fields()["fiscal_year"]["search_index"] == 1


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
    rules = _rules_module(_frappe({}, **{k: v for k, v in ctx.items() if k in ("refs", "previous")}))
    message = None
    with _konsol(rules):
        try:
            d.validate()
        except Refused as e:
            message = str(e)
    return message is not None, d, message


def test_a_plausible_rate_is_accepted_and_labelled():
    refused, d, _ = _validate()
    assert not refused and d.quote_label == "0.9478 CHF per EUR"
    assert not _validate(from_currency="USD", to_currency="CHF", rate_type="Average", quote=0.8762)[0]
    refused, d, _ = _validate(from_currency="JPY", to_currency="USD", quote=0.6607, quoted_per="100")
    assert not refused and d.quote_label == "0.6607 USD per 100 JPY"


def test_idr_and_vnd_into_usd_pass():
    """The #138 wide band [1e-4, 1e4] refused both, so a group holding either
    could never close. Quoted per 10,000 they keep their digits."""
    refused, d, _ = _validate(from_currency="IDR", to_currency="USD", quote=0.6116, quoted_per="10000")
    assert not refused and d.quote_label == "0.6116 USD per 10,000 IDR"
    assert not _validate(from_currency="VND", to_currency="USD", quote=0.394, quoted_per="10000")[0]


def test_a_quote_that_would_lose_its_digits_is_refused():
    """MariaDB keeps 9 decimal places: 0.0000611 keeps 5 significant digits.
    The direction never flips; a larger unit keeps the digits."""
    refused, _, msg = _validate(from_currency="IDR", to_currency="USD", quote=0.0000611)
    assert refused and "keeps only 5 significant digits" in msg
    assert "Quote it per a larger unit: 0.611 USD per 10,000 IDR" in msg
    assert not _validate(from_currency="JPY", to_currency="USD", quote=0.006607)[0], "7 digits is enough"


def test_a_100x_error_on_jpy_is_refused_on_the_true_rate():
    """The wide band could not catch it; the reference can. The check reads
    quote / quoted_per, whatever the unit."""
    refused, _, msg = _validate(from_currency="JPY", to_currency="USD", quote=66.07, quoted_per="100")
    assert refused and "0.6607 USD per JPY is about 98x above" in msg and "(#138)" in msg
    assert _validate(from_currency="JPY", to_currency="USD", quote=0.6607)[0], "per 1: 100x"
    assert _validate(from_currency="JPY", to_currency="USD", quote=0.6607, quoted_per="10000")[0], "100x below"
    assert _validate(from_currency="USD", to_currency="CHF", quote=87.62)[0]


def test_a_currency_with_no_reference_is_refused():
    refused, _, msg = _validate(from_currency="XAU", quote=2100.0)
    assert refused and "set USD Reference (log10) (usd_log10) on ISO Currency XAU" in msg
    assert _validate(_ctx={"refs": dict(REFS, EUR=0.0)})[0], "0 is 'not set' for any currency but USD"
    assert not _validate(from_currency="USD", to_currency="CHF", quote=0.8762,
                         _ctx={"refs": dict(REFS, USD=0.0)})[0], "USD is the anchor"


def test_the_guards_refuse():
    assert _validate(quote=0)[0]
    assert _validate(quote=-0.9)[0]
    assert _validate(quote=94.78)[0], "#138: 94.78 for 0.9478 is a scaling error"
    assert _validate(quoted_per="5")[0], "a unit off the list"
    assert _validate(from_currency="CHF", to_currency="CHF", quote=1)[0], "no rate into itself"
    assert _validate(rate_type="Default")[0]
    assert _validate(to_currency="EUR")[0], "EUR is no group's reporting currency here"
    assert _validate(fiscal_year=2150)[0]


def test_undeclared_period_refused():
    """konsol#189: periods are declared, never assumed. The Fiscal Period
    template is gone; the controller asks period_status.assert_declared, which
    knows only EPM Fiscal Year."""
    refused, _, msg = _validate(fiscal_period=14)
    assert refused and "FY2099 has no period 14" in msg and "not been declared" in msg
    assert not _validate(_ctx={"declared_periods": {(2099, 12), (2099, 14)}}, fiscal_period=14)[0]


def test_a_move_over_half_needs_a_reason():
    """Soft: ARS fell 55% in Dec 2023, so a big move is allowed with a reason.
    Compared as true rates, whatever unit each is quoted per."""
    ars = dict(from_currency="ARS", to_currency="USD", quote=1.23693488, quoted_per="1000")
    previous = ("GER-2099-11-ARS-USD-Closing-01", 0.272851296, "100", 2099, 11)
    refused, _, msg = _validate(_ctx={"previous": previous}, **ars)
    assert refused and "-55% from the previous approved rate FY2099 P11" in msg
    assert "This rate (0.00123693488 USD per ARS)" in msg and "(0.00272851296 USD per ARS)" in msg, msg
    assert not _validate(_ctx={"previous": previous}, change_reason="Devaluation, 13 Dec", **ars)[0]
    assert not _validate(_ctx={"previous": ("GER-P11", 1.25, "1000", 2099, 11)}, **ars)[0], "a small move"
    # from the ERP quote it was proposed from (per the same unit)
    refused, _, msg = _validate(quote=1.5, erp_quote=0.9478, source="Manual",
                                _before=dict(source="Manual", erp_quote=0.9478, quoted_per="1"))
    assert refused and "from the ERP quote" in msg
    # an amendment always needs one
    assert _validate(amended_from="GER-2099-12-EUR-CHF-Closing-01", change_reason="")[0]


def test_an_amendment_must_say_why():
    assert _validate(amended_from="GER-2099-12-EUR-CHF-Closing-01", change_reason="")[0]
    assert not _validate(amended_from="GER-2099-12-EUR-CHF-Closing-01", change_reason="Board rate")[0]


def test_a_pre_filled_rate_a_person_changed_is_manual():
    prefilled = dict(source="ERP pre-fill", erp_quote=0.9478, quoted_per="1")
    _, d, _ = _validate(source="ERP pre-fill", erp_quote=0.9478, quote=0.95, _before=prefilled)
    assert d.source == "Manual"
    _, d, _ = _validate(source="ERP pre-fill", erp_quote=0.9478, quote=0.9478, _before=prefilled)
    assert d.source == "ERP pre-fill"
    # re-quoting the same rate per another unit changes nothing; the ERP quote follows the unit
    refused, d, _ = _validate(source="ERP pre-fill", erp_quote=0.9478, quote=9.478, quoted_per="10",
                              _before=prefilled)
    assert not refused and d.source == "ERP pre-fill" and abs(d.erp_quote - 9.478) < 1e-12
    refused, d, _ = _validate(source="ERP pre-fill", erp_quote=0.9478, quote=9.6, quoted_per="10",
                              _before=prefilled)
    assert not refused and d.source == "Manual"


def test_a_forged_source_is_refused():
    """source and erp_quote are read-only in the form only: REST writes them."""
    refused, _, msg = _validate(source="Adoption")
    assert refused and "one-time rate adoption" in msg
    refused, _, msg = _validate(source="ERP pre-fill", erp_quote=0.9478)
    assert refused and "Pre-fill from ERP" in msg
    assert _validate(source="Manual", erp_quote=0.9478)[0], "an ERP quote only the pre-fill records"
    refused, _, _ = _validate(source="Adoption", _before=dict(source="Manual", erp_quote=0, quoted_per="1"))
    assert refused, "relabelling a saved rate"
    # the real writers, each under its own flag
    assert not _validate(source="ERP pre-fill", erp_quote=0.9478,
                         _ctx={"flags": {"konsol_prefilling_rates": True}})[0]
    assert not _validate(source="Adoption", _ctx={"flags": {"konsol_adopting_rates": True},
                                                  "user": "Administrator"})[0]
    assert _validate(source="Adoption", _ctx={"flags": {"konsol_adopting_rates": True}})[0], "only the system"
    # a saved pre-filled draft keeps its label when a person (no flag) saves it again
    assert not _validate(source="ERP pre-fill", erp_quote=0.9478,
                         _before=dict(source="ERP pre-fill", erp_quote=0.9478, quoted_per="1"))[0]


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


def test_ger_locks_year_before_own_row():
    """PR #191 re-review 2 finding 1: closing a period locks the year FOR
    UPDATE, then share-locks approved Group Exchange Rate rows
    (group_rates.assert_rates_complete -> _approved_keys(lock=True)). A GER
    save/submit/cancel locks the opposite way round: Frappe's
    check_if_latest (document.py:1164, via load_doc_before_save) takes a FOR
    UPDATE lock on the rate's own row first, and only reaches the year
    afterwards, in before_submit/before_cancel -> assert_open/period_row.
    Opposite order deadlocks with a close.

    check_if_latest must be overridden to share-lock the year first, then
    delegate to Frappe's own check_if_latest, for a saved (not new) rate."""
    module, record = _controller()
    d = _doc(module, _before={"fiscal_year": 2099})
    d.check_if_latest()
    assert record["sql"] == [
        "SELECT name, status FROM `tabEPM Fiscal Year` WHERE fiscal_year=%s LOCK IN SHARE MODE",
        "BASE_CHECK_IF_LATEST",
    ], record["sql"]


def test_ger_year_lock_is_on_the_row_not_the_index():
    """The year lock must land on the same record the close locks. `SELECT
    name ... WHERE fiscal_year=%s LOCK IN SHARE MODE` is answered from the
    unique fiscal_year index alone (it holds the primary key), so InnoDB
    share-locks only that index entry, and the close's `WHERE name=%s FOR
    UPDATE` on the row doesn't wait for it: live, the GER-cancel vs close
    deadlock still happened 3/3 with that query. Selecting a column outside
    the index (status, as period_row does) locks the row itself."""
    module, record = _controller()
    d = _doc(module, _before={"fiscal_year": 2099})
    d.check_if_latest()
    year_sql = [q for q in record["sql"] if "`tabEPM Fiscal Year`" in q]
    assert year_sql and all("LOCK IN SHARE MODE" in q for q in year_sql), record["sql"]
    assert all("status" in q.split("FROM")[0] for q in year_sql), \
        f"the year lock selects only indexed columns, so it locks the index entry, not the row: {year_sql}"


def test_new_ger_does_not_lock_a_year_in_check_if_latest():
    """Frappe's own check_if_latest never takes the row lock for a new
    document (load_doc_before_save returns early, document.py:1160-1161), so
    the override must not lock a year for one either."""
    module, record = _controller()
    d = _doc(module)
    assert d.is_new()
    d.check_if_latest()
    assert record.get("sql", []) == ["BASE_CHECK_IF_LATEST"], record.get("sql")


# -- publishing: the true rate, computed once, by konsol -------------------------------

APPROVED = [
    types.SimpleNamespace(name="GER-2099-12-JPY-USD-Closing-01", to_currency="USD", from_currency="JPY",
                          fiscal_year=2099, fiscal_period=12, rate_type="Closing", quote=0.6607, quoted_per="100",
                          modified=datetime.datetime(2099, 12, 31, 9, 0, 0)),
    types.SimpleNamespace(name="GER-2099-12-VND-USD-Closing-01", to_currency="USD", from_currency="VND",
                          fiscal_year=2099, fiscal_period=12, rate_type="Closing", quote=0.394, quoted_per="10000",
                          modified=None)]


def _publisher(lock=1, fail_at=None, flags=None):
    """group_rates with a stub MariaDB (GET_LOCK, the locking read) and a stub
    ClickHouse; returns (rules, clickhouse stub, record)."""
    record = {"db": [], "ch": [], "stamped": [], "failed": []}
    frappe = _frappe({})
    for name in ("in_install", "in_import", "in_migrate", "in_patch"):
        setattr(frappe.flags, name, (flags or {}).get(name, False))
    frappe.conf = types.SimpleNamespace(db_name="zzdb")
    frappe.logger = lambda *a, **k: types.SimpleNamespace(error=lambda *a, **k: None, exception=lambda *a, **k: None)

    def sql(query, params=(), as_dict=False, **k):
        if "GET_LOCK" in query:
            record["db"].append(("lock", params))
            return [(lock,)]
        if "RELEASE_LOCK" in query:
            record["db"].append(("release", params))
            return [(1,)]
        raise AssertionError(f"read on the caller's connection: {query}")
    frappe.db.sql = sql

    class FreshDB:
        """The publish's own connection."""
        def sql(self, query, as_dict=False):
            record["db"].append(("fresh read", query))
            assert as_dict and "docstatus = 1" in query
            return APPROVED

        def close(self):
            record["db"].append(("fresh close", None))
    ch = types.ModuleType("konsol.clickhouse")

    def execute(query, params=None):
        record["ch"].append(query)
        if fail_at and query.startswith(fail_at):
            raise RuntimeError("boom")
        return ""
    ch.execute = execute
    ch._stamp_watermark = lambda table, n, modified=None: record["stamped"].append((table, n, modified))
    ch._record_sync_failure = lambda table, kind, message: record["failed"].append((table, kind))
    rules = _rules_module(frappe)
    rules._fresh_db = FreshDB
    return rules, ch, record


def test_a_publish_swaps_the_whole_set_in_one_at_a_time():
    """Joint review of #174: TRUNCATE + batched INSERTs, unserialised, let two
    publishes duplicate every key and a reader see part of the set."""
    rules, ch, record = _publisher()
    with _konsol(rules, ch):
        assert rules.publish_rates(force=True) == 2
    lock = ("konsol_group_rates_publish:zzdb", 120)
    kinds = [k for k, _ in record["db"]]
    assert kinds == ["lock", "fresh read", "fresh close", "release"], \
        "the rows are read after the lock is held, on a connection of their own, closed at once"
    read = record["db"][1][1]
    assert "LOCK IN SHARE MODE" not in read and "FOR UPDATE" not in read, "a plain read: it takes no row lock"
    assert record["db"][0] == ("lock", lock) and record["db"][-1] == ("release", (lock[0],))
    main, shadow = "epm_staging.group_exchange_rates", "epm_staging.group_exchange_rates__publishing"
    kinds = [q.split(" (")[0] if q.startswith("INSERT") else q for q in record["ch"]]
    assert kinds == [f"DROP TABLE IF EXISTS {shadow}", f"CREATE TABLE {shadow} AS {main}", f"INSERT INTO {shadow}",
                     f"EXCHANGE TABLES {main} AND {shadow}", f"DROP TABLE IF EXISTS {shadow}"]
    assert not any(q.startswith(("TRUNCATE", f"INSERT INTO {main} ")) for q in record["ch"])
    insert = record["ch"][2]
    assert "('USD', 'JPY', 2099, 12, 'Closing', 0.006607, 'GER-2099-12-JPY-USD-Closing-01')" in insert
    assert "('USD', 'VND', 2099, 12, 'Closing', 3.94e-05, 'GER-2099-12-VND-USD-Closing-01')" in insert
    assert record["stamped"] == [(main, 2, "2099-12-31 09:00:00")]


def test_a_publish_that_cannot_get_in_or_fails_changes_nothing_and_says_so():
    rules, ch, record = _publisher(lock=0)
    with _konsol(rules, ch):
        assert rules.publish_rates(force=True) is None
    assert record["ch"] == [] and [k for k, _ in record["db"]] == ["lock"]
    assert record["failed"] == [("epm_staging.group_exchange_rates", "publish_lock_timeout")], \
        "a publish that could not get in leaves a health record"
    rules, ch, record = _publisher(fail_at="EXCHANGE")
    with _konsol(rules, ch):
        assert rules.publish_rates(force=True) is None
    assert record["failed"] == [("epm_staging.group_exchange_rates", "publish_failed")]
    assert record["db"][-1][0] == "release" and record["stamped"] == []
    for flag in ("in_migrate", "in_patch", "in_install", "in_import"):
        rules, ch, record = _publisher(flags={flag: True})
        with _konsol(rules, ch):
            assert rules.publish_rates() is None, flag
        assert record["db"] == [] and record["ch"] == [], flag
    rules, ch, record = _publisher(flags={"in_migrate": True})
    with _konsol(rules, ch):
        assert rules.publish_rates(force=True) == 2, "the reconcile after a migrate forces it"


def test_the_publish_connects_as_frappe_connect_does():
    """The fresh connection is Frappe's own class, given what frappe.connect
    gives it (the site config), and connected before it is returned."""
    made = []

    class DB:
        def connect(self):
            made.append("connected")
    frappe = _frappe({})
    frappe.local = types.SimpleNamespace(conf=types.SimpleNamespace(
        db_socket=None, db_host="mariadb", db_port=3306, db_name="zzdb", db_password="zz-not-a-secret"))
    database = types.ModuleType("frappe.database")
    database.get_db = lambda **k: made.append(k) or DB()
    saved = sys.modules.get("frappe.database")
    sys.modules["frappe.database"] = database
    try:
        _rules_module(frappe)._fresh_db()
    finally:
        if saved is None:
            sys.modules.pop("frappe.database", None)
        else:
            sys.modules["frappe.database"] = saved
    assert made == [dict(socket=None, host="mariadb", port=3306, user="zzdb", password="zz-not-a-secret",
                         cur_db_name="zzdb"), "connected"]


def test_approve_and_cancel_republish_after_the_commit():
    module, record = _controller()
    queued, published = [], []
    ch = types.ModuleType("konsol.clickhouse")
    ch.after_commit_once = lambda key, fn: queued.append((key, fn))
    rules = _rules_module(_frappe({}), {"publish_rates": lambda force=False: published.append(force) or 7})
    with _konsol(rules, ch):
        d = _doc(module, docstatus=1)
        d.on_submit()
        d.on_cancel()
        assert [k for k, _ in queued] == [("resync_staging", "Group Exchange Rate")] * 2
        assert queued[0][1] == module.GroupExchangeRate.resync_staging
        assert module.GroupExchangeRate.resync_staging(force=True) == 7
    assert published == [True]
    assert rules.published_rows(APPROVED) == [
        ["USD", "JPY", 2099, 12, "Closing", 0.006607, "GER-2099-12-JPY-USD-Closing-01"],
        ["USD", "VND", 2099, 12, "Closing", 0.0000394, "GER-2099-12-VND-USD-Closing-01"]]
    module.on_doctype_update()
    assert record["index"][0][0][1] == ["to_currency", "from_currency", "fiscal_year", "fiscal_period", "rate_type"]


def test_reconcile_publishes_through_the_same_path():
    """reconcile_all calls resync_staging for a controller with a staging table
    and no field map, so a migrate republishes the true rates too."""
    module, _ = _controller()
    cls = module.GroupExchangeRate
    assert cls.CH_STAGING_TABLE == "epm_staging.group_exchange_rates" and callable(cls.resync_staging)
    assert not getattr(cls, "CH_TABLE", None) and not getattr(cls, "CH_FIELD_MAP", None)
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        src = f.read()
    assert 'getattr(cls, "resync_staging", None)' in src and "cls.resync_staging(force=True)" in src


def _ddl():
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        tree = ast.parse(f.read())
    return {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
            and getattr(n.targets[0], "id", "") in ("_REFERENCE_TABLE_DDL", "_RAW_TABLE_DDL", "_ADDED_COLUMNS")}


def test_the_staging_ddl_is_the_true_rate():
    """Keep in step with konsolidat's clickhouse/init-db.sql, character for character."""
    body = _ddl()["_REFERENCE_TABLE_DDL"]["epm_staging.group_exchange_rates"]
    assert body == (
        "(to_currency String, from_currency String, fiscal_year UInt16, "
        "fiscal_period UInt8, rate_type String, rate Float64, document String) "
        "ENGINE = MergeTree ORDER BY (to_currency, from_currency, fiscal_year, fiscal_period, rate_type)")
    module, _ = _controller()
    cols = [c.strip().split()[0] for c in body[1:body.index(")")].split(",")]
    assert cols == module.GroupExchangeRate.CH_STAGING_COLUMNS
    assert "epm_staging.group_exchange_rates" not in _ddl()["_ADDED_COLUMNS"], "nothing shipped to upgrade"


def test_the_currencies_ddl_carries_the_reference():
    consts = _ddl()
    # identical to konsolidat's clickhouse/init-db.sql; NaN = never synced
    assert consts["_REFERENCE_TABLE_DDL"]["epm_gold.currencies"] == (
        "(currency_code String, currency_name String, symbol String, minor_unit UInt8, "
        "usd_log10 Float64 DEFAULT nan) "
        "ENGINE = MergeTree ORDER BY currency_code")
    # a table that already shipped gets the column on the next migrate, at the
    # end of its CREATE so a fresh table and an upgraded one agree
    added = consts["_ADDED_COLUMNS"]
    assert added["epm_gold.currencies"] == [("usd_log10", "Float64 DEFAULT nan")]
    # _ADDED_COLUMNS also carries raw tables (konsol#159), created by ensure_raw_tables
    ddl = {**consts["_REFERENCE_TABLE_DDL"], **consts["_RAW_TABLE_DDL"]}
    for table, cols in added.items():
        body = ddl[table]
        assert body[:body.index(") ENGINE")].endswith(", ".join(f"{c} {t}" for c, t in cols)), table
    sql = []
    m = _clickhouse_module(lambda s, params=None: sql.append(s) or "")
    m.ensure_reference_tables()
    stmt = "ALTER TABLE epm_gold.currencies ADD COLUMN IF NOT EXISTS usd_log10 Float64 DEFAULT nan"
    assert stmt in sql and sql.index(stmt) > sql.index(
        "CREATE TABLE IF NOT EXISTS epm_gold.currencies " + consts["_REFERENCE_TABLE_DDL"]["epm_gold.currencies"])


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


# -- the rules ------------------------------------------------------------------------

def _rules(**overrides):
    return _rules_module(_frappe({}), overrides)


def test_a_quote_per_a_unit():
    """Decided 13 Sep 2026: quote per 1 / 10 / 100 / 1,000 / 10,000 units of the
    from-currency; the direction never flips; the true rate is quote / per."""
    r = _rules()
    assert r.QUOTED_PER == (1, 10, 100, 1000, 10000) and r.MIN_SIGNIFICANT_DIGITS == 6
    # divided in decimal: the nearest double to the exact quotient, not 0.006606999999999999
    assert r.true_rate(0.6607, "100") == 0.006607 and r.true_rate(0.394, 10000) == 0.0000394
    assert r.true_rate(0.9478, "1") == 0.9478 and r.true_rate("0.660700000", "100") == 0.006607
    assert r.true_rate(0, "100") == 0.0
    assert [r.significant_digits(q) for q in (0.0066, 0.66, 0.0000394, 150, 0.0001)] == [7, 9, 5, 12, 6]
    # the pre-fill and the adoption: the smallest unit that puts the quote at 0.1 or more
    assert r.choose_quoted_per(0.9478) == (0.9478, 1)
    assert r.choose_quoted_per(0.006607) == (0.6607, 100), "JPY -> USD per 100"
    assert r.choose_quoted_per(0.000741) == (0.741, 1000), "KRW per 1,000"
    assert r.choose_quoted_per(0.0000394) == (0.394, 10000), "VND per 10,000"
    assert r.choose_quoted_per(1 / 150) == (0.666666667, 100)
    assert r.choose_quoted_per(0.000001) == (0.01, 10000), "the largest unit when none reaches 0.1"
    assert r.quote_label(0.6607, "100", "JPY", "USD") == "0.6607 USD per 100 JPY"
    assert r.quote_label(0.394, 10000, "VND", "USD") == "0.394 USD per 10,000 VND"
    assert r.quote_label(0.9478, "1", "EUR", "CHF") == "0.9478 CHF per EUR"
    src = open(RULES).read()
    assert "inverse" not in src.replace("inverse of", ""), "nothing is inverted to store it"


CASES = os.path.join(APP_DIR, "tests", "fx_magnitude_cases.json")


def test_the_shared_case_table():
    """The same cases, rates, references and outcomes as konsolidat #176's
    dbt_project/tests/assert_fx_magnitude_cases.sql (fx_magnitude_problem):
    the two implementations of the rule cannot drift apart."""
    r = _rules()
    with open(CASES) as f:
        cases = json.load(f)["cases"]
    assert len(cases) == 13
    got = []
    for c in cases:
        refs = {c["from"]: r.usd_reference(c["from"], float(c["from_log10"])),
                c["to"]: r.usd_reference(c["to"], float(c["to_log10"]))}
        got.append(r.magnitude_verdict(c["from"], c["to"], float(c["rate"]), refs)[0])
    assert got == [c["expected"] for c in cases], [
        (c["note"], c["expected"], g) for c, g in zip(cases, got) if g != c["expected"]]


def test_one_definition_of_the_no_reference_rule():
    """konsol.fx_reference holds it; group_rates and currency_references use it."""
    import importlib as _importlib

    rule = _importlib.import_module("konsol.fx_reference")
    r = _rules()
    assert r.usd_reference is rule.usd_reference and r.REFERENCE_CURRENCY == rule.REFERENCE_CURRENCY
    for path in (RULES, os.path.join(APP_DIR, "currency_references.py")):
        tree = ast.parse(open(path).read())
        defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)} | {
            t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
        assert not defined & {"usd_reference", "is_unset", "REFERENCE_CURRENCY"}, (path, defined)


def test_the_magnitude_guard_reads_iso_currency():
    """One rule and one home: ISO Currency.usd_log10, read for the two currencies."""
    src = open(RULES).read()
    assert "WIDE_BAND" not in src and "TIGHT_BOUNDS" not in src and "WIDE_BOUNDS" not in src
    asked = []
    frappe = _frappe({})
    lookup = frappe.get_all
    frappe.get_all = lambda doctype, **k: asked.append((doctype, k["filters"], k["fields"])) or lookup(doctype, **k)
    r = _rules_module(frappe)
    assert (r.REFERENCE_FIELD, r.REFERENCE_CURRENCY, r.MAGNITUDE_TOLERANCE_DECADES) == ("usd_log10", "USD", 1.0)
    assert r.magnitude_verdict("JPY", "USD", 0.006607) == ("ok", None)
    assert asked == [("ISO Currency", {"name": ["in", ["JPY", "USD"]]}, ["name", "usd_log10"])]
    verdict, why = r.magnitude_verdict("EUR", "USD", 108.9)
    assert verdict == "implausible" and "x above" in why
    assert r.magnitude_problem("EUR", "USD", 1.08) is None and r.magnitude_problem("EUR", "USD", 108.9) == why
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


def test_the_previous_rate_is_read_as_a_true_rate():
    frappe = _frappe({}, previous=("GER-2099-11-JPY-USD-Closing-01", 0.6607, "100", 2099, 11))
    r = _rules_module(frappe)
    rate, label = r.previous_approved("USD", "JPY", "Closing", 2099, 12)
    assert rate == 0.006607 and label == "FY2099 P11 (GER-2099-11-JPY-USD-Closing-01)"


def test_every_iso_currency_has_a_reference():
    with open(os.path.join(APP_DIR, "reference_data", "iso_currencies.json")) as f:
        rows = json.load(f)
    by_code = {r["currency_code"]: r.get("usd_log10") for r in rows}
    assert len(by_code) == 69
    r = _rules()
    assert not [c for c, v in by_code.items() if r.usd_reference(c, v) is None]
    with open(os.path.join(APP_DIR, "epm", "doctype", "iso_currency", "iso_currency.json")) as f:
        field = next(x for x in json.load(f)["fields"] if x["fieldname"] == "usd_log10")
    assert field["fieldtype"] == "Float" and "0.001" in field["description"]
    with open(os.path.join(APP_DIR, "epm", "doctype", "iso_currency", "iso_currency.py")) as f:
        assert '"usd_log10": "usd_log10"' in f.read(), "written through to epm_gold.currencies"


def test_the_period_date_is_the_warehouse_one():
    r = _rules()
    assert str(r.period_start(2024, 3)) == "2024-03-01"
    assert str(r.period_start(2024, 0)) == "2024-01-01"
    assert str(r.period_start(2024, 13)) == "2024-12-01"


def test_period_dates_come_from_the_declared_calendar():
    """konsol#189: a 13-period year's P02 is a declared 28-day window, not
    the calendar month of February. period_start/period_end read exactly
    what period_status.period_dates says, never invent one."""
    declared = {(2024, 2): (datetime.date(2024, 1, 29), datetime.date(2024, 2, 25))}
    r = _rules_module(_frappe({}), {"period_dates": lambda fy, fp: declared[(fy, fp)]})
    assert (str(r.period_start(2024, 2)), str(r.period_end(2024, 2))) == ("2024-01-29", "2024-02-25")
    assert r.period_end(2024, 2) != datetime.date(2024, 2, 29), "not Feb's calendar month"


def test_undeclared_period_has_no_dates():
    """An undeclared period raises PeriodNotDeclared; no date is invented."""
    r = _rules_module(_frappe({}))

    def undeclared(fy, fp):
        raise r.PeriodNotDeclared(f"FY{fy} has no period {fp}: it has not been declared.")
    r.period_dates = undeclared
    for fn in (r.period_start, r.period_end):
        try:
            fn(2099, 14)
        except r.PeriodNotDeclared as e:
            assert "FY2099 has no period 14" in str(e)
        else:
            raise AssertionError("an undeclared period must raise PeriodNotDeclared")


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
    assert q["how"] == "inverse of USD→JPY" and q["rate"] == 1 / 150.0, "full precision until it is stored"
    [q] = r.resolve_quotes(ROWS, "GBP", "EUR", "Closing")
    assert q["how"] == "cross via USD" and abs(q["rate"] - 1.27 / 1.08) < 1e-12


def test_average_falls_back_to_the_erp_default_type_and_says_so():
    r = _rules()
    [q] = r.resolve_quotes(ROWS[:2], "EUR", "CHF", "Average")
    assert (q["erp_type"], q["rate"]) == ("Default", 0.945)
    assert r.resolve_quotes(ROWS, "EUR", "SEK", "Closing") == []


def test_two_erp_sources_are_both_shown_and_a_disagreement_is_flagged():
    r = _rules()
    quotes = r.resolve_quotes(ROWS, "EUR", "CHF", "Closing")
    assert [q["source"] for q in quotes] == ["d365_fo", "erpnext"]
    note = r.describe_quotes(quotes, 2024, 3, "EUR", "CHF")
    assert note.startswith("ERP SOURCES DISAGREE") and "0.9478 CHF per EUR" in note and "0.948 CHF per EUR" in note


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
    attempted = []
    insert = Doc.insert
    Doc.insert = lambda self: attempted.append(self["from_currency"]) or insert(self)
    asked = []
    r = _rules_module(frappe, {
        "required_pairs": lambda fp, fy: {("EUR", "CHF"), ("GBP", "CHF"), ("SEK", "CHF"), ("XAU", "CHF"),
                                          ("JPY", "CHF"), ("NOK", "CHF")},
        # the period end quotes EUR -> CHF Closing at 0.95; the period start at 0.9478
        # (one row per source, type and pair, as erp_quote_rows returns them)
        "erp_quote_rows": lambda as_of: asked.append(str(as_of)) or [
            ("d365_fo", "Closing", "EUR", "CHF", 0.95, "2024-03-31") if str(as_of) == "2024-03-31"
            and r[:4] == ("d365_fo", "Closing", "EUR", "CHF") else r for r in ROWS] + [
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
    assert all(d["source"] == "ERP pre-fill" and d["erp_quote"] == d["quote"] and "docstatus" not in d
               for d in drafts)
    eur_avg = next(d for d in drafts if d["from_currency"] == "EUR" and d["rate_type"] == "Average")
    assert (eur_avg["quote"], eur_avg["quoted_per"]) == (0.945, "1") and "Default" in eur_avg["source_note"]
    jpy = next(d for d in drafts if d["from_currency"] == "JPY")
    assert (jpy["quote"], jpy["quoted_per"]) == (0.588235294, "100"), "CHF per 100 JPY"
    # the note gives every ERP quote as the true rate it means, and the unit it is entered per
    assert "d365_fo Closing 0.00588235294 CHF per JPY" in jpy["source_note"], jpy["source_note"]
    assert "Entered as 0.588235294 CHF per 100 JPY" in jpy["source_note"]
    assert "0.945 CHF per EUR" in eur_avg["source_note"] and "Entered as" not in eur_avg["source_note"]
    assert out["existing"] == ["GBP → CHF Closing"]
    assert "SEK → CHF Average" in out["no_quote"]
    refused = dict(x.split(": ", 1) for x in out["refused"])
    assert "No magnitude reference for XAU" in refused["XAU → CHF Closing"]
    assert "x above" in refused["NOK → CHF Closing"], "NOK 8.3 CHF is a scaling error"
    assert refused["SEK → CHF Closing"] == "moves +80%"
    # the magnitude guard runs before the insert: a refused quote is never inserted,
    # so only the controller's refusal (SEK) logged a message, and it was cleared
    assert "XAU" not in attempted and "NOK" not in attempted and "SEK" in attempted
    assert record.get("cleared") == [1] and frappe.flags.konsol_prefilling_rates is False
    # a Closing rate is struck at the period end, an Average spans it from the start
    assert sorted(asked) == ["2024-03-01", "2024-03-31"]
    eur = {d["rate_type"]: d["quote"] for d in drafts if d["from_currency"] == "EUR"}
    assert eur == {"Closing": 0.95, "Average": 0.945}


def test_the_pre_fill_falls_back_to_the_tree_for_the_period():
    """No ledgers yet for the period: tree_pairs, asked for that period (it
    filters out equity-accounted currencies; test_group_rates_bench runs it)."""
    asked = []
    frappe = _frappe({})
    frappe.only_for = lambda roles: None
    r = _rules_module(frappe, {"required_pairs": lambda fy, fp: set(),
                               "tree_pairs": lambda fy, fp: asked.append((fy, fp)) or set(),
                               "erp_quote_rows": lambda as_of: []})
    assert r.prefill_from_erp("2099", "13") == {"created": [], "existing": [], "no_quote": [], "refused": []}
    assert asked == [(2099, 13)]
    assert str(r.period_end(2099, 2)) == "2099-02-28" and str(r.period_end(2099, 13)) == "2099-12-31"
    assert str(r.period_end(2096, 2)) == "2096-02-29" and str(r.period_end(2099, 0)) == "2099-01-31"


def _gate(pairs=None, approved=(), error=None, built=True, groups=()):
    record = {}
    frappe = _frappe(record)
    original_sql = frappe.db.sql

    def sql(query, params=(), *a, **k):
        if "FROM `tabGroup Exchange Rate`" in query:
            record.setdefault("sql", []).append(query)
            return [(f, t, rt) for f, t, rt in approved]
        return original_sql(query, params, *a, **k)

    frappe.db.sql = sql

    def needs(fy, fp):
        if error:
            raise error
        return set(pairs or ()), list(groups)

    def ledgers():
        if isinstance(built, Exception):
            raise built
        return built
    return _rules_module(frappe, {"translation_needs": needs, "ledgers_built": ledgers})


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
    # a group the period translates into has no reporting currency: the warehouse
    # guard refuses it, so the gate does, with every rate approved
    r = _gate(pairs, full, groups=["ZZ_NOCURRENCY"])
    assert r.rate_gate(2024, 3) == ([], None, ["Consolidation Group ZZ_NOCURRENCY has no reporting currency"])
    try:
        r.assert_rates_complete(2024, 3)
    except Refused as e:
        assert "Consolidation Group ZZ_NOCURRENCY has no reporting currency" in str(e)
    else:
        raise AssertionError("a group with no reporting currency must refuse the close")


def test_rate_gate_reads_lock():
    """PR #191 re-review finding 4: the close path's read of approved Group
    Exchange Rates can return this transaction's REPEATABLE READ snapshot, a
    plain read (frappe.get_all) missing a rate cancelled and committed while a
    close waited on the year lock (period/year close hold it, then call
    assert_rates_complete -> rate_gate(..., lock=True) ->
    _approved_keys(..., lock=True)). That path must read them LOCK IN SHARE
    MODE, like period_status.period_row and
    fiscal_calendar.periods_in_use(lock=True)."""
    record = {}
    r = _rules_module(_frappe(record))
    r._approved_keys(2024, 3, lock=True)
    queries = [q for q in record.get("sql", []) if "tabGroup Exchange Rate" in q]
    assert queries, "expected a read of Group Exchange Rate for the approved keys"
    assert all("LOCK IN SHARE MODE" in q for q in queries), queries


def test_rate_gate_lock_only_for_close():
    """konsol#189 62h: rate_gate is also called by home_api._rate_gate for the
    read-only home screen, on every view - LOCK IN SHARE MODE there would take
    share locks on Group Exchange Rate rows and can block rate saves for the
    request's duration. Only the close gate (assert_rates_complete ->
    rate_gate(..., lock=True)) needs the lock (PR #191 re-review finding 4);
    rate_gate(fy, fp) with defaults, as home_api calls it, must read plain."""
    pairs = {("EUR", "CHF")}
    full = [(f, t, rt) for f, t in pairs for rt in ("Closing", "Average")]
    record = {}
    frappe = _frappe(record)
    original_sql = frappe.db.sql

    def sql(query, params=(), *a, **k):
        if "FROM `tabGroup Exchange Rate`" in query:
            record.setdefault("sql", []).append(query)
            return [(f, t, rt) for f, t, rt in full]
        return original_sql(query, params, *a, **k)
    frappe.db.sql = sql

    r = _rules_module(frappe, {"translation_needs": lambda fy, fp: (pairs, [])})

    r.rate_gate(2024, 3)
    home_queries = [q for q in record.get("sql", []) if "tabGroup Exchange Rate" in q]
    assert home_queries, "expected a read of Group Exchange Rate for the approved keys"
    assert not any("LOCK IN SHARE MODE" in q for q in home_queries), \
        "rate_gate's default (home_api's call) must not take a row lock: " + repr(home_queries)

    record["sql"] = []
    assert not _refused(r.assert_rates_complete, 2024, 3)
    close_queries = [q for q in record.get("sql", []) if "tabGroup Exchange Rate" in q]
    assert close_queries, "expected a read of Group Exchange Rate for the approved keys"
    assert all("LOCK IN SHARE MODE" in q for q in close_queries), \
        "assert_rates_complete's close path must lock: " + repr(close_queries)


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
    assert _gate(error=ConnectionError("down")).rate_gate(2024, 3) == (None, "ConnectionError", [])


def test_the_gate_sorts_pairs_from_groups_with_no_currency():
    """The SQL runs against ClickHouse in test_group_rates_bench (equity
    excluded, a group without a currency named, the pre-fill's fallback
    filtered); here, what the gate makes of the rows, and the bound period."""
    calls = []
    rows = [("IDR", "USD", "GROUP_CORP"), ("VND", "USD", "GROUP_CORP"), ("EUR", "", "ZZ_NOCURRENCY"),
            ("GBP", "", "ZZ_NOCURRENCY"), ("KRW", "", "ZZ_OTHER")]
    r = _rules_module(_frappe({}), {"_ch_rows": lambda sql, params=None: calls.append(params) or rows})
    assert r.translation_needs(2024, 3) == ({("IDR", "USD"), ("VND", "USD")}, ["ZZ_NOCURRENCY", "ZZ_OTHER"])
    assert r.required_pairs(2024, 3) == {("IDR", "USD"), ("VND", "USD")}
    calls.clear()
    r._ch_rows = lambda sql, params=None: calls.append(params) or [("VND", "USD")]
    assert r.tree_pairs(2099, 13) == {("VND", "USD")}
    assert calls == [{"fy": 2099, "fp": 13}]


def test_the_gate_reads_existence_before_passing():
    sql = []
    r = _rules_module(_frappe({}), {"_ch_rows": lambda s, params=None: sql.append(s) or [[0]]})
    assert r.ledgers_built() is False and sql == ["EXISTS TABLE epm_gold.gold_trial_balance"]


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


def test_the_adoption_enters_each_rate_per_the_unit_that_keeps_it():
    """The rate a period was translated at, entered so its true rate survives
    MariaDB: JPY -> USD 0.0066 becomes 0.66 per 100, its ERP quote with it."""
    made = []
    frappe = _frappe({}, user="Administrator")
    lookup = frappe.get_all
    frappe.get_all = lambda doctype, **k: lookup(doctype, **k) if doctype == "ISO Currency" else []
    frappe.utils = types.SimpleNamespace(today=lambda: "2026-09-13")
    frappe.logger = lambda *a: types.SimpleNamespace(info=lambda *a: None)
    frappe.db.savepoint = lambda name: None

    class Doc(dict):
        name = "GER-X"

        def insert(self, **k):
            made.append(dict(self))

        def submit(self):
            pass
    frappe.get_doc = Doc
    r = _rules_module(frappe, {
        "translated_rates": lambda: [("JPY", "USD", 2024, 3, [0.00663730291], [0.0066])],
        "erp_quote_rows": lambda as_of: [("d365_fo", "Closing", "JPY", "USD", 0.00663730291, "2024-03-01")]})
    out = r.adopt_erp_rates()
    assert len(out["adopted"]) == 2 and frappe.flags.konsol_adopting_rates is False
    closing = next(d for d in made if d["rate_type"] == "Closing")
    assert (closing["quote"], closing["quoted_per"], closing["erp_quote"]) == (0.663730291, "100", 0.663730291)
    assert closing["source"] == "Adoption"
    average = next(d for d in made if d["rate_type"] == "Average")
    assert (average["quote"], average["quoted_per"], average["erp_quote"]) == (0.66, "100", None)


def _adopter(refs, used, made=None):
    frappe = _frappe({}, user="Administrator", refs=refs)
    frappe.local = types.SimpleNamespace(site="konsolidat.local")
    frappe.utils = types.SimpleNamespace(today=lambda: "2026-09-13")
    frappe.logger = lambda *a: types.SimpleNamespace(info=lambda *a: None)
    frappe.db.savepoint = lambda name: None
    lookup = frappe.get_all
    frappe.get_all = lambda doctype, **k: lookup(doctype, **k) if doctype == "ISO Currency" else []

    class Doc(dict):
        name = "GER-X"

        def insert(self, **k):
            if made is None:
                raise AssertionError("wrote a row")
            made.append(dict(self))

        def submit(self):
            pass
    frappe.get_doc = Doc
    return _rules_module(frappe, {"translated_rates": lambda: used, "erp_quote_rows": lambda as_of: []})


def test_the_adoption_will_not_start_without_every_reference():
    """Joint review of #174: with no usd_log10 every row was refused and the
    patch still looked done. Now nothing is written, the patch runs again, and
    the message names every currency and what to do."""
    used = [("JPY", "USD", 2024, 3, [0.0066], [0.0066]), ("XBB", "USD", 2024, 3, [0.5], [0.5])]
    r = _adopter(dict(REFS, JPY=0.0), used)
    try:
        r.adopt_erp_rates()
    except RuntimeError as e:
        msg = str(e)
        assert "no magnitude reference (ISO Currency usd_log10) for JPY, XBB" in msg
        assert "Create or edit ISO Currency JPY, set USD Reference (log10)." in msg
        assert "Create or edit ISO Currency XBB, set USD Reference (log10)." in msg
        assert "Then rerun `bench migrate`" in msg and msg.endswith("Nothing was adopted.")
        assert "bench --site konsolidat.local execute konsol.group_rates.adopt_erp_rates" in msg
    else:
        raise AssertionError("an adoption without references must stop")


def test_a_currency_the_adoption_skips_needs_no_reference():
    """Joint re-review of #174: a currency translated at the 1.0 parity
    fallback (or at two rates) is skipped, so its missing reference must not
    stop the upgrade before model sync. The adoption proceeds; the skip says it
    has no reference."""
    made = []
    used = [("EUR", "CHF", 2024, 3, [0.9478], [0.9422]),
            ("XAA", "USD", 2024, 3, [1.0], [1.0]),              # the parity fallback
            ("XAB", "USD", 2024, 3, [0.5, 0.6], [0.5, 0.7])]   # two rates
    out = _adopter(REFS, used, made).adopt_erp_rates()
    assert sorted((d["from_currency"], d["rate_type"]) for d in made) == [("EUR", "Average"), ("EUR", "Closing")]
    assert len(out["skipped"]) == 4 and out["refused"] == []
    assert all("no magnitude reference for XAA either" in s for s in out["skipped"] if s.startswith("XAA"))
    assert all("1.0 parity" in s for s in out["skipped"] if s.startswith("XAA"))
    assert all("no magnitude reference for XAB either" in s for s in out["skipped"] if s.startswith("XAB"))


def test_a_dry_run_names_the_currencies_with_no_reference():
    import contextlib as _contextlib
    import io

    used = [("XBB", "USD", 2024, 3, [0.5], [0.5])]
    printed = io.StringIO()
    with _contextlib.redirect_stdout(printed):
        out = _adopter(REFS, used).adopt_erp_rates(dry_run=True)
    assert len(out["adopted"]) == 2, "a dry run plans, and writes nothing"
    assert "konsol#103 adoption (dry run): konsol#103 adoption: no magnitude reference (ISO Currency usd_log10) for XBB" \
        in printed.getvalue()


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


def test_the_upgrade_patch_brings_its_own_column_and_references():
    """patches.txt has no sections, so the patch runs before model sync and
    before after_migrate: it reloads ISO Currency (usd_log10) and Group Exchange
    Rate, seeds the references, then adopts. test_group_rates_bench runs it on a
    real site without the column, and with the column at 0."""
    calls = []
    frappe = types.ModuleType("frappe")
    frappe.reload_doc = lambda module, dt, name: calls.append(("reload", name))
    refs = types.ModuleType("konsol.currency_references")
    refs.seed_iso_currencies = lambda: calls.append(("seed",)) or {"inserted": [], "filled": ["JPY"]}
    rules = types.ModuleType("konsol.group_rates")
    rules.adopt_erp_rates = lambda: calls.append(("adopt",)) or {"adopted": ["x"], "skipped": [], "refused": []}
    mods = {"frappe": frappe, "konsol": types.ModuleType("konsol"), "konsol.currency_references": refs,
            "konsol.group_rates": rules}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location(
            "patch_under_test", os.path.join(APP_DIR, "patches", "adopt_erp_rates_as_group_exchange_rates.py"))
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        out = patch.execute()
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    assert calls == [("reload", "iso_currency"), ("reload", "group_exchange_rate"), ("seed",), ("adopt",)]
    assert out == {"adopted": ["x"], "skipped": [], "refused": []}
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        assert "konsol.patches.adopt_erp_rates_as_group_exchange_rates" in [l.strip() for l in f]


# -- the close gate is wired into EPM Fiscal Year -----------------------------------------------

def test_closing_a_period_checks_its_group_rates():
    """konsol#189: periods close on EPM Fiscal Year's rows, so the gate is there
    (behaviour in test_fiscal_year_actions.test_closing_a_period_checks_its_group_rates);
    the retired Period Status controller no longer gates anything."""
    with open(os.path.join(APP_DIR, "epm", "doctype", "epm_fiscal_year", "epm_fiscal_year.py")) as f:
        assert "group_rates.assert_rates_complete(" in f.read()
    with open(os.path.join(APP_DIR, "epm", "doctype", "period_status", "period_status.py")) as f:
        assert "assert_rates_complete" not in f.read(), "Period Status is retired; it gates nothing"


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
