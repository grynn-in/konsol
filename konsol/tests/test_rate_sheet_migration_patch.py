"""The Group Exchange Rate to Rate Sheet migration patches (konsol#592 pair 1,
task T25), host-run.

``migrate_group_exchange_rates_to_rate_sheets`` reads every GER row raw, plans
the sheets with ``rate_sheet_migration_model.plan``, stops on any refusal, and
inserts the planned sheets through the Rate Sheet controller as the
Administrator under ``MIGRATE_FLAG``. Each sheet gets an audit CSV of its GER
rows, and the CONSTANT Rate Set gets ONE audit CSV of every GER row, dropped
ones included, with its disposition. ``drop_budget_cycle_rate_set`` drops
Budget Cycle's ``rate_set`` column only (coordinator's T25 scope decision:
Approved Budget and Current Forecast keep theirs until pair 2).

A stub frappe holds the tables in memory. Its Rate Sheet ``submit`` cancels
the sheet a correction names in ``supersedes``, as the controller's
``_cancel_superseded`` does.
"""
import csv
import importlib.util
import io
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCHES = os.path.join(APP_DIR, "patches")
MIGRATE_PY = os.path.join(PATCHES, "migrate_group_exchange_rates_to_rate_sheets.py")
DROP_PY = os.path.join(PATCHES, "drop_budget_cycle_rate_set.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
CONTROLLER_PY = os.path.join(APP_DIR, "consolidation", "doctype", "rate_sheet", "rate_sheet.py")
MIGRATE_MODULE = "konsol.patches.migrate_group_exchange_rates_to_rate_sheets"
DROP_MODULE = "konsol.patches.drop_budget_cycle_rate_set"
RATE_SET_MODULE = "konsol.consolidation.doctype.rate_set.rate_set"

_spec = importlib.util.spec_from_file_location(
    "_rsmm_fixture_for_patch", os.path.join(APP_DIR, "tests", "test_rate_sheet_migration_model.py"))
FX = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FX)


class ZZValidationError(Exception):
    pass


class _Flags(dict):
    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


class _Doc:
    def __init__(self, site, data):
        self._site = site
        self.__dict__.update(data)
        self.flags = _Flags()
        self.docstatus = data.get("docstatus", 0)
        self.name = data.get("name")

    def get(self, key, default=None):
        return self.__dict__.get(key, default)

    def insert(self, ignore_permissions=None, set_name=None, **_):
        site = self._site
        if set_name:
            self.name = set_name
        self.inserted_as = {"user": site.frappe.session.user,
                            "migrating": bool(site.frappe.flags.get("konsol_migrating_rates")),
                            "ignore_validate": bool(self.flags.get("ignore_validate"))}
        site.calls.append(("insert", self.doctype, self.name))
        if self.doctype == "Rate Sheet":
            if site.fail_insert == self.name:
                raise ZZValidationError(f"ZZ controller refused {self.name}")
            assert self.name not in site.sheets, self.name
            site.sheets[self.name] = self
        elif self.doctype == "File":
            site.files.append(self)
        else:
            site.others.append(self)
        return self

    def submit(self):
        self._site.calls.append(("submit", self.name))
        self.docstatus = 1
        if self.get("supersedes"):
            self._site.sheets[self.supersedes].docstatus = 2

    def cancel(self):
        self._site.calls.append(("cancel", self.name))
        self.docstatus = 2


class _Site:
    """An upgrading site: GER rows, Budget Cycles (with the raw rate_set
    column), Approved Budgets, the calendar and the Rate Sets."""

    def __init__(self, rates, cycles=(), budgets=(), calendar=None, rate_sets=None, columns=None):
        self.rates = [dict(r) for r in rates]
        self.cycles = [dict(c) for c in cycles]
        self.budgets = [dict(b) for b in budgets]
        self.calendar = calendar if calendar is not None else FX.CALENDAR
        self.rate_sets = rate_sets if rate_sets is not None else [{"name": "ACTUAL", "is_actuals": 1}]
        self.columns = columns if columns is not None else {
            "Budget Cycle": ["name", "fiscal_year", "rate_set", "docstatus"],
            "Approved Budget": ["name", "fiscal_year", "budget_cycle", "rate_set", "docstatus"],
            "Current Forecast": ["name", "rate_set"],
        }
        self.sheets, self.files, self.others, self.calls = {}, [], [], []
        self.constant_set_calls = 0
        self.fail_insert = None
        self.frappe = None

    # -- the stub -------------------------------------------------------------------

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")
        frappe.ValidationError = ZZValidationError
        frappe.flags = _Flags()
        frappe.session = types.SimpleNamespace(user="Administrator")

        def throw(message, exc=None, *a, **k):
            raise (exc or ZZValidationError)(message)

        def set_user(user):
            site.calls.append(("set_user", user))
            frappe.session.user = user

        def reload_doc(module, kind, name, *a, **k):
            site.calls.append(("reload_doc", module, name))

        def get_doc(arg, name=None, **_):
            if isinstance(arg, dict):
                return _Doc(site, arg)
            assert arg == "Rate Sheet", arg
            return site.sheets[name]

        def table_exists(doctype, *a, **k):
            return doctype in ("Group Exchange Rate", "Budget Cycle", "Approved Budget",
                               "Current Forecast", "Rate Sheet", "Rate Set", "EPM Fiscal Year")

        def has_column(doctype, column):
            site.calls.append(("has_column", doctype, column))
            return column in site.columns.get(doctype, [])

        def exists(doctype, filters=None, *a, **k):
            if doctype == "Rate Sheet Row":
                return any(r.get("source") == "Migrated" for s in site.sheets.values()
                           for r in s.rates)
            if doctype == "File":
                return any(all(f.get(k) == v for k, v in filters.items()) for f in site.files)
            if doctype == "Rate Sheet":
                return filters in site.sheets
            raise AssertionError((doctype, filters))

        def get_value(doctype, name, field, *a, **k):
            assert (doctype, field) == ("Rate Sheet", "docstatus"), (doctype, field)
            return site.sheets[name].docstatus

        def sql(query, params=None, as_dict=False, **k):
            site.calls.append(("sql", query))
            q = " ".join(query.split())
            if "`tabGroup Exchange Rate`" in q:
                assert q.upper().startswith("SELECT"), q
                assert "docstatus" in q and "WHERE" not in q.upper(), q  # every row, every docstatus
                return [dict(r) for r in site.rates]
            if "`tabBudget Cycle`" in q:
                cols = site.columns["Budget Cycle"]
                return [{"name": c["name"], "fiscal_year": c["fiscal_year"], "docstatus": c["docstatus"],
                         "rate_set": c.get("rate_set") if "rate_set" in cols and "rate_set" in q else None}
                        for c in site.cycles]
            if "`tabApproved Budget`" in q:
                return [dict(b) for b in site.budgets]
            if "`tabEPM Fiscal Year`" in q:
                out = []
                for year, rows in sorted(site.calendar.items()):
                    for p in rows:
                        out.append({"fiscal_year": year, "period": p["period"], "type": p["type"],
                                    "start_date": f"{year}-01-01", "end_date": f"{year}-12-31"})
                return out
            if "`tabRate Set`" in q:
                return [{"name": s["name"]} for s in site.rate_sets if s["is_actuals"]]
            raise AssertionError(q)

        def sql_ddl(statement, *a, **k):
            site.calls.append(("sql_ddl", statement))
            for doctype, cols in site.columns.items():
                if f"`tab{doctype}`" in statement and "`rate_set`" in statement and "rate_set" in cols:
                    cols.remove("rate_set")

        def logger(*a, **k):
            return types.SimpleNamespace(info=lambda m, *a, **k: site.calls.append(("log.info", m)),
                                         warning=lambda m, *a, **k: site.calls.append(("log.warning", m)))

        frappe.throw, frappe.set_user, frappe.reload_doc, frappe.get_doc = throw, set_user, reload_doc, get_doc
        frappe.logger = logger
        frappe.db = types.SimpleNamespace(table_exists=table_exists, has_column=has_column, exists=exists,
                                          get_value=get_value, sql=sql, sql_ddl=sql_ddl)
        site.frappe = frappe
        return frappe

    def rate_set_module(self):
        site = self
        mod = types.ModuleType(RATE_SET_MODULE)
        mod.CONSTANT_NAME = "CONSTANT"

        def ensure_constant_set():
            site.constant_set_calls += 1
            return None

        mod.ensure_constant_set = ensure_constant_set
        return mod

    # -- reading the result ----------------------------------------------------------

    def files_on(self, doctype, name):
        return [f for f in self.files if f.attached_to_doctype == doctype and f.attached_to_name == name]

    def migration_audit(self):
        files = self.files_on("Rate Set", "CONSTANT")
        assert len(files) == 1, [f.file_name for f in files]
        return list(csv.DictReader(io.StringIO(files[0].content)))


def _run(site, path=MIGRATE_PY):
    saved = {k: sys.modules.get(k) for k in ("frappe", RATE_SET_MODULE)}
    sys.modules["frappe"] = site.module()
    sys.modules[RATE_SET_MODULE] = site.rate_set_module()
    try:
        spec = importlib.util.spec_from_file_location("_t25_patch_under_test", path)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        patch.execute()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _raises(site, path=MIGRATE_PY):
    try:
        _run(site, path)
    except Exception as e:  # noqa: BLE001
        return e
    raise AssertionError("the patch did not stop")


def _sheet_inserts(site):
    return [c for c in site.calls if c[:2] == ("insert", "Rate Sheet")]


# -- patch 1: the live-shaped migrate ---------------------------------------------------

def test_patch_modules_exist():
    assert os.path.exists(MIGRATE_PY), MIGRATE_PY
    assert os.path.exists(DROP_PY), DROP_PY


def test_live_shaped_rows_become_22_sheets_with_the_planned_docstatus():
    site = _Site(FX.live_rates())
    _run(site)
    assert len(site.sheets) == 22, sorted(site.sheets)
    by_status = {}
    for s in site.sheets.values():
        by_status.setdefault(s.docstatus, []).append(s.name)
    assert len(by_status[1]) == 21 and by_status.get(0) is None, by_status
    assert by_status[2] == ["RS-S-2012-12"], by_status
    second = site.sheets["RS-S-2012-12-2"]
    assert second.supersedes == "RS-S-2012-12"
    assert second.change_reason == "Wrong source day"
    assert all(s.rate_basis == "Statutory" and len(s.rates) == 14 for s in site.sheets.values())


def test_sheets_are_inserted_by_the_controller_as_administrator_under_the_flag():
    site = _Site(FX.live_rates())
    site_user = "analyst@example.com"
    site_module = site.module
    def module():
        frappe = site_module()
        frappe.session.user = site_user
        return frappe
    site.module = module
    _run(site)
    for s in site.sheets.values():
        assert s.inserted_as == {"user": "Administrator", "migrating": True, "ignore_validate": False}, \
            (s.name, s.inserted_as)
    # submit is the controller's own, and the supersede cancel comes from it, not a DB write
    assert ("submit", "RS-S-2012-12") in site.calls and ("submit", "RS-S-2012-12-2") in site.calls
    assert site.calls.index(("submit", "RS-S-2012-12")) < site.calls.index(("insert", "Rate Sheet",
                                                                              "RS-S-2012-12-2"))
    # the flag is cleared and the session user restored afterwards
    assert not site.frappe.flags.get("konsol_migrating_rates")
    assert site.frappe.session.user == site_user


def test_sheet_names_are_exactly_the_planners():
    rates = FX.live_rates()
    planned = [s["name"] for s in FX.run(rates)["sheets"]]
    site = _Site(rates)
    _run(site)
    assert [c[2] for c in _sheet_inserts(site)] == planned


def test_migrated_rows_carry_source_quotes_and_legacy_documents():
    site = _Site(FX.live_rates())
    _run(site)
    first = site.sheets["RS-S-2012-12"]
    cad = [r for r in first.rates if r["from_currency"] == "CAD"][0]
    assert cad["source"] == "Migrated"
    assert abs(cad["average_quote"] - 0.9811) < 1e-12
    assert cad["legacy_documents"].split("\n") == ["GER-2012-12-CAD-USD-Average-01",
                                                   "GER-2012-12-CAD-USD-Closing-01"]
    assert str(cad["quoted_per"]) == "1"
    jpy = [r for r in site.sheets["RS-S-2015-12"].rates if r["from_currency"] == "JPY"][0]
    assert jpy["quoted_per"] == "100", jpy  # a Select: the option's text


def test_every_sheet_gets_its_own_audit_csv_of_its_ger_rows():
    rates = FX.live_rates()
    plan = FX.run(rates)
    site = _Site(rates)
    _run(site)
    for s in plan["sheets"]:
        files = site.files_on("Rate Sheet", s["name"])
        assert len(files) == 1, (s["name"], len(files))
        rows = list(csv.DictReader(io.StringIO(files[0].content)))
        assert [r["name"] for r in rows] == [a["name"] for a in s["audit_rows"]]
        assert files[0].is_private == 1


# -- the migration audit: every GER row, dropped ones included ------------------------------

def _with_dropped():
    rates = FX.live_rates()
    # a pending correction (dropped), a cancelled rate nothing replaced (dropped), and a
    # budget-set rate no approved Cycle uses (dropped)
    rates.append(FX.ger("EUR", "Average", 2020, 12, 1.2, docstatus=0, seq=2,
                        supersedes="GER-2020-12-EUR-USD-Average-01"))
    rates.append(FX.ger("GBP", "Closing", 2026, 3, 1.3, docstatus=2, seq=1))
    rates.append(FX.ger("GBP", "Closing", 2026, 4, 1.3, rate_set="ZZBUD"))
    return rates


def test_the_migration_audit_holds_every_ger_row_dropped_ones_included():
    rates = _with_dropped()
    site = _Site(rates)
    _run(site)
    audit = site.migration_audit()
    assert sorted(r["name"] for r in audit) == sorted(r["name"] for r in rates)
    by_name = {r["name"]: r for r in audit}
    dropped = [r for r in audit if r["disposition"] == "dropped"]
    assert {r["name"] for r in dropped} == {"GER-2020-12-EUR-USD-Average-02", "GER-2026-03-GBP-USD-Closing-01",
                                            "GER-2026-04-GBP-USD-Closing-01"}, dropped
    assert all(r["reason"] and not r["rate_sheets"] for r in dropped), dropped
    assert "pending correction" in by_name["GER-2020-12-EUR-USD-Average-02"]["reason"]
    # a migrated row names every sheet version that holds it
    assert by_name["GER-2012-12-CAD-USD-Closing-01"]["disposition"] == "migrated"
    assert by_name["GER-2012-12-CAD-USD-Closing-01"]["rate_sheets"].split() == ["RS-S-2012-12", "RS-S-2012-12-2"]
    assert by_name["GER-2012-12-CAD-USD-Average-01"]["rate_sheets"].split() == ["RS-S-2012-12"]
    # the GER record itself is in the file
    assert by_name["GER-2012-12-CAD-USD-Average-02"]["change_reason"] == "Wrong source day"
    assert by_name["GER-2012-12-CAD-USD-Average-01"]["docstatus"] == "2"


def test_ensure_constant_set_runs_before_the_audit_is_attached_to_it():
    site = _Site(FX.live_rates())
    _run(site)
    assert site.constant_set_calls == 1
    assert site.files_on("Rate Set", "CONSTANT")


# -- refusals stop the migrate and insert nothing --------------------------------------------

def test_a_refusal_stops_it_and_inserts_nothing():
    rates = [r for r in FX.live_rates() if r["name"] != "GER-2015-12-EUR-USD-Average-01"]
    rates.append(FX.ger("AUD", "Closing", 2030, 12, 0.7))  # an undeclared year: a second refusal
    site = _Site(rates)
    err = _raises(site)
    msg = str(err)
    assert "EUR → USD FY2015 P12" in msg and "FY2030 P12" in msg, msg  # every refusal is listed
    assert site.sheets == {} and site.files == [] and site.others == []
    assert not _sheet_inserts(site)


def test_more_than_one_actuals_set_refuses_before_planning():
    site = _Site(FX.live_rates(), rate_sets=[{"name": "ACTUAL", "is_actuals": 1},
                                             {"name": "ZZACT", "is_actuals": 1}])
    err = _raises(site)
    assert "Is Actuals" in str(err), err
    assert site.sheets == {} and site.files == []


def test_a_controller_refusal_names_the_sheet():
    site = _Site(FX.live_rates())
    site.fail_insert = "RS-S-2015-12"
    err = _raises(site)
    assert "RS-S-2015-12" in str(err) and "ZZ controller refused" in str(err), err


def test_a_closing_period_sheet_is_refused_before_any_insert():
    """The planner migrates a Closing-period rate with a warning, but the
    controller's window refuses a Statutory sheet for a Closing period and
    the publish fails on such a sheet: the patch names it and inserts
    nothing, rather than write a sheet that breaks the publish."""
    rates = FX.live_rates()
    for rtype in ("Closing", "Average"):
        rates.append(FX.ger("EUR", rtype, 2019, 13, 1.1))
    site = _Site(rates)
    err = _raises(site)
    assert "RS-S-2019-13" in str(err) and "Closing period" in str(err), err
    assert site.sheets == {} and site.files == []


# -- Constant sheets from budget sets ----------------------------------------------------------

def _budget_rates(uniform=True):
    rows = []
    for fp in range(1, 13):
        for rtype in ("Closing", "Average"):
            q = 1.25 if uniform or fp < 12 else 1.30
            rows.append(FX.ger("EUR", rtype, 2025, fp, q, rate_set="ZZBUD"))
    return rows


def test_a_budget_set_on_an_approved_cycle_becomes_an_approved_constant_sheet():
    site = _Site(FX.live_rates() + _budget_rates(),
                 cycles=[{"name": "ZZBC-2025", "fiscal_year": 2025, "rate_set": "ZZBUD", "docstatus": 1}])
    _run(site)
    c = site.sheets["RS-C-2025"]
    assert c.rate_basis == "Constant" and c.docstatus == 1 and not c.get("fiscal_period")
    assert [r["rate"] for r in c.rates] == [1.25]
    # Budget Cycle's rate_set left the JSON (T22); the patch reads the raw column
    assert ("has_column", "Budget Cycle", "rate_set") in site.calls


def test_a_non_uniform_constant_draft_is_written_without_the_controller_validate():
    """Its blank rate is what the planner intends (a person enters it), but
    the controller's row rules refuse a blank rate on any save: the draft is
    written with validate skipped, and only that one."""
    site = _Site(FX.live_rates() + _budget_rates(uniform=False),
                 cycles=[{"name": "ZZBC-2025", "fiscal_year": 2025, "rate_set": "ZZBUD", "docstatus": 1}])
    _run(site)
    c = site.sheets["RS-C-2025"]
    assert c.docstatus == 0 and c.rates[0]["rate"] is None
    assert c.inserted_as["ignore_validate"] is True
    assert c.valid_from == "2025-01-01" and c.valid_to == "2025-12-31"
    assert c.period_label == "FY2025" and c.title == "Constant FY2025 Constant"
    others = [s for s in site.sheets.values() if s.name != "RS-C-2025"]
    assert not any(s.inserted_as["ignore_validate"] for s in others)


def test_a_correction_without_a_reason_gets_a_visible_migration_reason():
    rates = [dict(r) for r in FX.live_rates()]
    for r in rates:
        if r["name"] == "GER-2012-12-CAD-USD-Average-02":
            r["change_reason"] = None
    site = _Site(rates)
    _run(site)
    why = site.sheets["RS-S-2012-12-2"].change_reason
    assert "Group Exchange Rate" in why and "no reason" in why, why


# -- idempotent ------------------------------------------------------------------------------

def test_a_second_run_does_nothing():
    site = _Site(FX.live_rates())
    _run(site)
    sheets, files = dict(site.sheets), list(site.files)
    site.calls.clear()
    _run(site)
    assert site.sheets == sheets and site.files == files
    assert not _sheet_inserts(site) and not [c for c in site.calls if c[0] in ("submit", "cancel")]


def test_a_second_run_after_an_all_dropped_migrate_does_nothing():
    """No sheet was made (every row dropped), but the audit was attached:
    the second run must not attach another."""
    site = _Site([FX.ger("GBP", "Closing", 2026, 3, 1.3, docstatus=2)])
    _run(site)
    assert site.sheets == {} and len(site.files_on("Rate Set", "CONSTANT")) == 1
    _run(site)
    assert len(site.files_on("Rate Set", "CONSTANT")) == 1


def test_a_site_with_no_ger_rows_migrates_nothing():
    site = _Site([])
    _run(site)
    assert site.sheets == {} and site.files == []


def test_the_rate_sheet_doctypes_are_reloaded_first():
    site = _Site(FX.live_rates())
    _run(site)
    reloads = [c for c in site.calls if c[0] == "reload_doc"]
    assert ("reload_doc", "consolidation", "rate_sheet") in reloads
    assert ("reload_doc", "consolidation", "rate_sheet_row") in reloads
    first_insert = site.calls.index(_sheet_inserts(site)[0])
    assert all(site.calls.index(r) < first_insert for r in reloads)


def test_the_flag_name_is_the_controllers():
    with open(CONTROLLER_PY) as f:
        controller = f.read()
    with open(MIGRATE_PY) as f:
        patch = f.read()
    assert 'MIGRATE_FLAG = "konsol_migrating_rates"' in controller
    assert "konsol_migrating_rates" in patch or "MIGRATE_FLAG" in patch


# -- patch 2: Budget Cycle's rate_set column only ----------------------------------------------

def test_the_drop_touches_budget_cycle_only():
    site = _Site([])
    _run(site, DROP_PY)
    ddl = [c for c in site.calls if c[0] == "sql_ddl"]
    assert ddl == [("sql_ddl", "alter table `tabBudget Cycle` drop column `rate_set`")], ddl
    assert "rate_set" in site.columns["Approved Budget"] and "rate_set" in site.columns["Current Forecast"]
    assert not any(c[0] == "has_column" and c[1] != "Budget Cycle" for c in site.calls), site.calls


def test_the_drop_is_a_no_op_when_the_column_is_gone():
    site = _Site([])
    _run(site, DROP_PY)
    site.calls.clear()
    _run(site, DROP_PY)
    assert not [c for c in site.calls if c[0] == "sql_ddl"]


# -- patches.txt ------------------------------------------------------------------------------

def _patch_lines():
    with open(PATCHES_TXT) as f:
        return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]


def test_both_patches_are_listed_once_in_order_after_the_ger_patches():
    lines = _patch_lines()
    assert lines.count(MIGRATE_MODULE) == 1 and lines.count(DROP_MODULE) == 1
    migrate, drop = lines.index(MIGRATE_MODULE), lines.index(DROP_MODULE)
    assert migrate < drop
    # every patch that writes GER rows or their rate_set runs first
    for earlier in ("konsol.patches.adopt_erp_rates_as_group_exchange_rates",
                    "konsol.patches.stamp_rate_set_actual",
                    "konsol.patches.set_cancelled_workflow_state"):
        assert lines.index(earlier) < migrate, earlier
