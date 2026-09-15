"""Business Combination controller (konsolidat#198, design 2a), host-run.

The controller is loaded by file path against a stub frappe (the pattern of
test_consolidation_group_ic_difference.py): the stub is an in-memory site —
declared fiscal periods, the group root with its Consolidation Policy, Closing
group rates, the chart, trial balances and Ownership Periods — and records
what the controller writes (inserted Ownership Periods, syncs, the flag).
The arithmetic itself is the real, pure ``konsol.business_combination_model``;
these tests pin how the controller feeds it and what it does with the answer.
"""
import calendar
import datetime
import importlib.util
import os
import sys
import types
from decimal import Decimal

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(APP_DIR)
DOCTYPE_DIR = os.path.join(APP_DIR, "consolidation", "doctype")
PATH = os.path.join(DOCTYPE_DIR, "business_combination", "business_combination.py")

HEADER_TABLE = "epm_staging.business_combinations"
CHILD_TABLES = {
    "Business Combination Consideration": "epm_staging.business_combination_consideration",
    "Business Combination Acquired Balance": "epm_staging.business_combination_acquired_balances",
    "Business Combination Cost": "epm_staging.business_combination_costs",
}


class Refused(Exception):
    pass


class _Doc:
    """Stand-in for frappe.model.document.Document: attributes, get, db_set."""

    def __init__(self, **kw):
        self.db_sets = []
        self.__dict__.update(kw)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def db_set(self, field, value, *a, **k):
        setattr(self, field, value)
        self.db_sets.append((field, value))


def _getdate(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def _add_months(date, months):
    date = _getdate(date)
    month = date.month - 1 + months
    year = date.year + month // 12
    month = month % 12 + 1
    return datetime.date(year, month, min(date.day, calendar.monthrange(year, month)[1]))


TODAY = ["2026-09-15"]


def _load():
    frappe = types.ModuleType("frappe")

    def throw(msg, *a, **k):
        raise Refused(msg)

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.flags = types.SimpleNamespace(from_business_combination=False)
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = _Doc
    utils = types.ModuleType("frappe.utils")
    utils.getdate = _getdate
    utils.add_months = _add_months
    utils.nowdate = lambda: TODAY[0]
    ch = types.ModuleType("konsol.clickhouse")
    ch.sync_doctype_after_commit = lambda *a, **k: None
    ps = types.ModuleType("konsol.period_status")
    ps.assert_open = lambda *a, **k: None
    ps.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    gr = types.ModuleType("konsol.group_rates")
    gr.true_rate = lambda quote, per: float(Decimal(str(quote or 0)) / int(per or 1))

    stubs = {"frappe": frappe, "frappe.model": model, "frappe.model.document": document,
             "frappe.utils": utils, "konsol.clickhouse": ch, "konsol.period_status": ps,
             "konsol.group_rates": gr}
    konsol_pkg = sys.modules.get("konsol")
    if konsol_pkg is None or not hasattr(konsol_pkg, "__path__"):
        stubs["konsol"] = types.ModuleType("konsol")
        stubs["konsol"].__path__ = [APP_DIR]
    saved = {k: sys.modules.get(k) for k in stubs}
    # The child controllers must bind to this Document stub.
    for name in [n for n in sys.modules if n.startswith("konsol.consolidation.doctype.business_combination")]:
        del sys.modules[name]
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("bc_under_test", PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


M = _load()


class _Row(dict):
    __getattr__ = dict.get


def _match(row, filters):
    """A Frappe filter dict against one in-memory row: equality or [op, value]."""
    for field, cond in filters.items():
        have = row.get(field)
        if isinstance(cond, (list, tuple)):
            op, want = cond
            if op == "is":
                if (want == "not set") != (have in (None, "")):
                    return False
                continue
            if have is None:
                return False
            if isinstance(want, str) and isinstance(have, (datetime.date,)):
                want = _getdate(want)
            if isinstance(have, str) and isinstance(want, str) and field.endswith("_date"):
                have, want = _getdate(have), _getdate(want)
            ok = {"<": have < want, "<=": have <= want, ">": have > want,
                  ">=": have >= want, "!=": have != want, "=": have == want}[op]
            if not ok:
                return False
        elif field.endswith("_date") and have is not None:
            if _getdate(have) != _getdate(cond):
                return False
        elif have != cond:
            return False
    return True


# -- the case ------------------------------------------------------------------

PERIOD_DEC_2025 = {"fiscal_year": 2025, "fiscal_period": 12, "period_code": "P12",
                   "period_type": "Regular", "start_date": "2025-12-01", "end_date": "2025-12-31"}

ROOT_IFRS_PARTIAL = {
    "name": "ZZG", "consolidation_group": "ZZG", "reporting_currency": "EUR",
    "accounting_framework": "IFRS", "framework_note": "", "goodwill_method": "partial",
    "goodwill_treatment": "Impairment only", "goodwill_amortisation_years": 0,
    "acquisition_costs_treatment": "Expense", "measurement_period": "Off",
    "bargain_purchase": "Recognise gain",
    "goodwill_account": "ZZ1800", "fair_value_adjustment_account": "ZZ1810",
    "investment_account": "ZZ1500", "nci_account": "ZZ3900",
    "bargain_purchase_gain_account": "ZZ4900", "disposal_gain_loss_account": "ZZ4950",
    "disposal_proceeds_account": "ZZ1900", "goodwill_amortisation_expense_account": "",
    "acquisition_costs_account": "ZZ6900",
}

#: name -> (status, is_group, account_type)
ACCOUNTS = {
    "ZZ1100": ("Published", 0, "Asset"), "ZZ2100": ("Published", 0, "Liability"),
    "ZZ3100": ("Published", 0, "Equity"),
    "ZZ1800": ("Published", 0, "Asset"), "ZZ1810": ("Published", 0, "Asset"),
    "ZZ1500": ("Published", 0, "Asset"), "ZZ3900": ("Published", 0, "Equity"),
    "ZZ4900": ("Published", 0, "Revenue"), "ZZ4950": ("Published", 0, "Revenue"),
    "ZZ1900": ("Published", 0, "Asset"), "ZZ6900": ("Published", 0, "Expense"),
}

# The design's worked example: a complete acquired balance sheet (Dr +, Cr −)
# with 650 of net assets, −650 of equity and a 930 step-up on the asset.
WORKED_BALANCES = [
    {"main_account": "ZZ1100", "book_amount": 1000, "fair_value_adjustment": 930},
    {"main_account": "ZZ2100", "book_amount": -350, "fair_value_adjustment": 0},
    {"main_account": "ZZ3100", "book_amount": -650, "fair_value_adjustment": 0},
]
CASH_8300 = [{"component": "Cash", "amount": 8300, "currency": "EUR"}]


class _Site:
    """In-memory site behind the stub frappe; records what the controller writes."""

    def __init__(self, *, periods=None, root=None, rates=None, accounts=None, tbs=(), ownership=(),
                 disposals=(), disposal_table=True):
        self.periods = [dict(p) for p in (periods if periods is not None else [PERIOD_DEC_2025])]
        self.root = dict(ROOT_IFRS_PARTIAL) if root is None else root
        self.rates = dict(rates or {})  # (from, to, fy, fp) -> (quote, quoted_per)
        self.accounts = dict(ACCOUNTS if accounts is None else accounts)
        self.tbs = [{"data_area_id": e, "fiscal_year": y, "fiscal_period": p, "docstatus": 1} for e, y, p in tbs]
        self.ownership = [dict(o) for o in ownership]
        #: Business Disposal rows {name, ownership_period, docstatus}; the table
        #: itself may not exist yet (a stack mid-migrate), and is then never read.
        self.disposals = [dict(d) for d in disposals]
        self.disposal_table = disposal_table
        self.inserted, self.synced, self.flag_at_insert, self.opened, self.queries = [], [], [], [], []
        self.flag_at_cancel = []
        self._install()

    # -- frappe surface ----------------------------------------------------
    def _install(self):
        site = self
        M.frappe.flags = types.SimpleNamespace(from_business_combination=False)
        M.frappe.db = types.SimpleNamespace(sql=self._sql, get_value=self._get_value, exists=self._exists,
                                            table_exists=self._table_exists)
        M.frappe.get_all = self._get_all
        M.frappe.get_doc = self._get_doc
        M.sync_doctype_after_commit = lambda dt, table, fm: site.synced.append((dt, table, tuple(fm)))
        M.assert_open = lambda fy, fp, action="run": site.opened.append((fy, fp, action))

    def _table_exists(self, doctype):
        return self.disposal_table if doctype == "Business Disposal" else True

    def _sql(self, query, values=None, as_dict=False):
        self.queries.append((" ".join(query.split()), values))
        assert "`tabEPM Fiscal Year Period`" in query and "`tabEPM Fiscal Year`" in query, query
        date = _getdate(values["d"] if isinstance(values, dict) else values[0])
        rows = [_Row(p) for p in self.periods
                if _getdate(p["start_date"]) <= date <= _getdate(p["end_date"])]
        rows.sort(key=lambda p: (p["period_type"] != "Regular", _getdate(p["start_date"])))
        return rows[:1]

    def _get_value(self, doctype, filters, fieldname=None, as_dict=False, **k):
        if doctype == "Consolidation Group":
            if filters.get("consolidation_group") != self.root["consolidation_group"]:
                return None
            return _Row(self.root) if as_dict else self.root.get(fieldname)
        if doctype == "Main Account":
            row = self.accounts.get(filters)
            if not row:
                return None
            status, is_group, account_type = row
            values = {"status": status, "is_group": is_group, "account_type": account_type}
            if isinstance(fieldname, (list, tuple)):
                return tuple(values[f] for f in fieldname)
            return values[fieldname]
        if doctype == "Ownership Period":
            for row in self.ownership:
                if _match(row, filters):
                    return row.get(fieldname) if isinstance(fieldname, str) else _Row(row)
            return None
        if doctype == "Business Disposal":
            assert self.disposal_table, "Business Disposal read while its table does not exist"
            for row in self.disposals:
                if _match(row, filters):
                    return row.get(fieldname) if isinstance(fieldname, str) else _Row(row)
            return None
        raise AssertionError(f"unexpected get_value on {doctype}")

    def _exists(self, doctype, filters):
        rows = {"Trial Balance Submission": self.tbs, "Ownership Period": self.ownership}[doctype]
        if isinstance(filters, str):  # frappe.db.exists(doctype, name)
            filters = {"name": filters}
        for row in rows:
            if _match(row, filters):
                return row.get("name", True)
        return None

    def _get_all(self, doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **k):
        if doctype == "Ownership Period":
            rows = [_Row(r) for r in self.ownership if _match(r, filters or {})]
            if order_by:
                field, _, direction = order_by.partition(" ")
                rows.sort(key=lambda r: r.get(field), reverse=direction.lower() == "desc")
            return rows[:limit_page_length] if limit_page_length else rows
        assert doctype == "Group Exchange Rate", doctype
        assert filters.get("docstatus") == 1 and filters.get("rate_type") == "Closing", filters
        key = (filters["from_currency"], filters["to_currency"], filters["fiscal_year"], filters["fiscal_period"])
        if key not in self.rates:
            return []
        quote, per = self.rates[key]
        return [_Row(quote=quote, quoted_per=per)]

    def _get_doc(self, arg, name=None):
        site = self
        if isinstance(arg, dict):
            doc = _OwnershipPeriodDoc(**arg)
            doc.docstatus = 0
            # The site row behind the inserted period, so a later get_doc /
            # exists (the deal's cancel) finds what the approval created.
            row = {k: v for k, v in arg.items() if k != "doctype"}

            def insert():
                site.flag_at_insert.append(M.frappe.flags.from_business_combination)
                # Frappe's set_new_name (_set_amended_name): an amended document
                # is named after the one it amends — X → X-1, and X-1 → X-2 when
                # X-1 itself amends something; only otherwise does the format:
                # rule apply, which would collide with the cancelled row's name.
                amended_from = doc.get("amended_from")
                if amended_from:
                    source = next(r for r in site.ownership if r["name"] == amended_from)
                    if source.get("amended_from"):
                        stem, _, counter = amended_from.rpartition("-")
                        doc.name = f"{stem}-{int(counter) + 1}"
                    else:
                        doc.name = f"{amended_from}-1"
                else:
                    doc.name = f"OP-{doc.consolidation_group}-{doc.data_area_id}-{doc.effective_date}"
                if any(r["name"] == doc.name for r in site.ownership):
                    raise Refused(f"Duplicate entry '{doc.name}' for key 'PRIMARY'")
                row.update(name=doc.name, docstatus=0)
                site.inserted.append(doc)
                site.ownership.append(row)
                return doc

            def submit():
                doc.docstatus = row["docstatus"] = 1
                doc.submitted = True
                return doc

            doc.insert, doc.submit = insert, submit
            return doc
        for row in self.ownership:
            if row["name"] == name:
                doc = _OwnershipPeriodDoc(**row)

                def submit(doc=doc, row=row):
                    doc.docstatus = row["docstatus"] = 1
                    doc.submitted = True

                def cancel(doc=doc, row=row):
                    site.flag_at_cancel.append(M.frappe.flags.from_business_combination)
                    doc.docstatus = row["docstatus"] = 2
                    doc.cancelled = True

                doc.submit, doc.cancel = submit, cancel
                doc.save = lambda: None
                self.loaded = doc
                return doc
        raise AssertionError(f"no Ownership Period {name}")


class _OwnershipPeriodDoc(_Doc):
    CH_TABLE = "epm_staging.ownership_periods"
    CH_FIELD_MAP = {"consolidation_group": "consolidation_group", "data_area_id": "data_area_id"}
    submitted = False

    def update(self, values):
        self.__dict__.update(values)


def _deal(**over):
    fields = dict(
        name="BC-ZZG-ZZE-2025-12-31", doctype="Business Combination", docstatus=0,
        consolidation_group="ZZG", acquired_entity="ZZE", acquisition_date="2025-12-31",
        share_acquired_pct=100, consideration_currency="EUR",
        consideration=[dict(r) for r in CASH_8300],
        acquired_balances=[dict(r) for r in WORKED_BALANCES], costs=[],
        amended_from=None, ownership_period=None, nci_measurement=None,
    )
    fields.update(over)
    return M.BusinessCombination(**fields)


def _refused(fn):
    try:
        fn()
    except Refused as e:
        return str(e)
    raise AssertionError("expected the controller to refuse")


# -- validate: the purchase price allocation -----------------------------------

def test_validate_computes_the_worked_example_and_copies_the_nci_measurement():
    _Site()
    deal = _deal()
    deal.validate()
    assert deal.total_consideration == 8300.0
    assert deal.net_assets_acquired == 650.0
    assert deal.fair_value_adjustments == 930.0
    assert deal.goodwill == 6720.0
    assert deal.bargain_purchase_gain == 0.0
    assert deal.nci_at_acquisition == 0.0
    assert deal.costs_expensed == 0.0
    assert deal.nci_measurement == "partial"


def test_validate_at_eighty_percent_partial_measures_the_nci():
    _Site()
    deal = _deal(share_acquired_pct=80)
    deal.validate()
    assert deal.goodwill == 7036.0
    assert deal.nci_at_acquisition == 316.0


def test_validate_expenses_costs_under_an_expense_policy():
    _Site()
    deal = _deal(costs=[{"kind": "Legal", "amount": 120, "currency": "EUR"}])
    deal.validate()
    assert deal.costs_expensed == 120.0
    assert deal.goodwill == 6720.0


def test_validate_refuses_a_date_no_declared_period_covers():
    _Site()
    message = _refused(_deal(acquisition_date="2027-03-31").validate)
    assert "2027-03-31" in message and "declared" in message.lower()


def test_validate_refuses_a_group_without_a_root():
    _Site()
    message = _refused(_deal(consolidation_group="ZZ-NOWHERE").validate)
    assert "ZZ-NOWHERE" in message


def test_validate_refuses_a_missing_closing_rate():
    _Site()
    deal = _deal(consideration=[{"component": "Cash", "amount": 8300, "currency": "USD"}])
    message = _refused(deal.validate)
    assert "USD" in message and "Closing" in message
    assert "P12" in message and "2025" in message


def test_validate_translates_a_deferred_line_at_the_periods_closing_rate():
    _Site(rates={("USD", "EUR", 2025, 12): (90, 100)})  # 0.90 EUR per USD, quoted per 100
    deal = _deal(consideration=[{"component": "Cash", "amount": 8000, "currency": "EUR"},
                                {"component": "Deferred", "amount": 1000, "currency": "USD"}])
    deal.validate()
    assert deal.total_consideration == 8900.0


def test_validate_requires_the_balance_sheet_only_without_an_earlier_trial_balance():
    _Site()
    message = _refused(_deal(acquired_balances=[]).validate)
    assert "Acquired Balance Sheet is required" in message

    _Site(tbs=[("ZZE", 2025, 12)])
    _deal(acquired_balances=[]).validate()  # a trial balance in the acquisition period
    _Site(tbs=[("ZZE", 2024, 3)])
    _deal(acquired_balances=[]).validate()  # an earlier year

    _Site(tbs=[("ZZE", 2026, 1)])
    message = _refused(_deal(acquired_balances=[]).validate)
    assert "Acquired Balance Sheet is required" in message  # only a later one


def test_validate_throws_the_models_sentences():
    _Site()
    message = _refused(_deal(share_acquired_pct=0).validate)
    assert "Share Acquired" in message
    site = _Site()
    site.root["nci_account"] = ""
    message = _refused(_deal(share_acquired_pct=80).validate)
    assert "Non-controlling Interest Account is required" in message


def test_validate_answers_is_equity_from_the_charts_account_type():
    site = _Site()
    site.accounts["ZZ3100"] = ("Published", 0, "Liability")  # no equity line any more
    message = _refused(_deal().validate)
    assert "no equity line" in message


# -- amendments: the measurement period ----------------------------------------

def test_amending_needs_a_twelve_month_measurement_period():
    _Site()
    message = _refused(_deal(amended_from="BC-ZZG-ZZE-2025-12-31").validate)
    assert "Measurement Period" in message and "12 months" in message


def test_amending_inside_twelve_months_is_allowed_and_after_is_refused():
    site = _Site()
    site.root["measurement_period"] = "12 months"
    TODAY[0] = "2026-12-31"
    try:
        _deal(amended_from="BC-ZZG-ZZE-2025-12-31").validate()
        TODAY[0] = "2027-01-01"
        message = _refused(_deal(amended_from="BC-ZZG-ZZE-2025-12-31").validate)
        assert "2026-12-31" in message
    finally:
        TODAY[0] = "2026-09-15"


# -- submit, cancel, delete ----------------------------------------------------

def test_before_submit_asserts_the_acquisition_period_is_open():
    site = _Site()
    deal = _deal()
    deal.validate()
    deal.before_submit()
    assert site.opened and site.opened[0][:2] == (2025, 12)
    assert "business combination" in site.opened[0][2]

    def closed(fy, fp, action="run"):
        raise Refused(f"Cannot {action}: fiscal period {fp} of FY{fy} is closed.")

    M.assert_open = closed
    message = _refused(deal.before_submit)
    assert "closed" in message


def test_before_submit_refuses_a_required_account_that_went_missing():
    site = _Site()
    deal = _deal(share_acquired_pct=80)
    deal.validate()
    site.root["nci_account"] = ""  # the root changed between save and approval
    message = _refused(deal.before_submit)
    assert "Non-controlling Interest Account is required" in message


def test_on_submit_creates_and_submits_the_ownership_period_under_the_flag():
    site = _Site()
    deal = _deal(share_acquired_pct=80)
    deal.validate()
    deal.on_submit()
    assert len(site.inserted) == 1
    period = site.inserted[0]
    assert period.doctype == "Ownership Period"
    assert period.consolidation_group == "ZZG" and period.data_area_id == "ZZE"
    assert str(period.effective_date) == "2025-12-31"
    assert period.ownership_pct == 80
    assert period.consolidation_method == "full"
    assert str(period.acquisition_date) == "2025-12-31"
    assert period.is_first_acquisition == 1
    assert period.acquisition_price == 8300.0
    assert period.fair_value_adjustment == 930.0
    assert period.submitted is True
    assert site.flag_at_insert == [True]
    assert M.frappe.flags.from_business_combination is False
    assert ("ownership_period", period.name) in deal.db_sets


def test_on_submit_at_half_or_below_records_the_equity_method():
    site = _Site()
    deal = _deal(share_acquired_pct=40)
    deal.validate()
    deal.on_submit()
    assert site.inserted[0].consolidation_method == "equity"


def test_on_submit_is_not_the_first_acquisition_after_an_earlier_period():
    site = _Site(ownership=[{"name": "OP-ZZG-ZZE-2020-01-01", "consolidation_group": "ZZG",
                             "data_area_id": "ZZE", "effective_date": "2020-01-01",
                             "end_date": "2025-12-30", "docstatus": 1}])
    deal = _deal(share_acquired_pct=80)
    deal.validate()
    deal.on_submit()
    assert len(site.inserted) == 1
    assert site.inserted[0].is_first_acquisition == 0


def test_on_submit_links_an_existing_period_on_the_acquisition_date():
    existing = {"name": "OP-ZZG-ZZE-2025-12-31", "consolidation_group": "ZZG", "data_area_id": "ZZE",
                "effective_date": "2025-12-31", "end_date": None, "ownership_pct": 80,
                "consolidation_method": "full", "docstatus": 1}
    site = _Site(ownership=[existing])
    deal = _deal(share_acquired_pct=80)
    deal.validate()
    deal.on_submit()
    assert site.inserted == []
    assert ("ownership_period", "OP-ZZG-ZZE-2025-12-31") in deal.db_sets
    written = dict(site.loaded.db_sets)
    assert written["acquisition_price"] == 8300.0
    assert written["fair_value_adjustment"] == 930.0
    assert str(written["acquisition_date"]) == "2025-12-31"
    assert ("Ownership Period", "epm_staging.ownership_periods") in [s[:2] for s in site.synced]
    assert M.frappe.flags.from_business_combination is False


def test_on_submit_updates_the_linked_period_even_when_its_date_differs():
    """A migrated deal (P9) links its source Ownership Period and is dated by
    that period's own acquisition_date, which may differ from its
    effective_date. The linked period is the one the approval updates — not
    one matched by date, which would insert a second period overlapping the
    linked one (P9b)."""
    linked = {"name": "OP-ZZG-ZZE-2025-12-01", "consolidation_group": "ZZG", "data_area_id": "ZZE",
              "effective_date": "2025-12-01", "end_date": None, "ownership_pct": 80,
              "consolidation_method": "full", "acquisition_date": "2025-12-31", "docstatus": 1}
    site = _Site(ownership=[linked])
    deal = _deal(share_acquired_pct=80, ownership_period="OP-ZZG-ZZE-2025-12-01")
    deal.validate()
    deal.on_submit()
    assert site.inserted == [], "no second period overlapping the linked one"
    assert site.loaded.name == "OP-ZZG-ZZE-2025-12-01"
    written = dict(site.loaded.db_sets)
    assert written["acquisition_price"] == 8300.0
    assert written["fair_value_adjustment"] == 930.0
    assert str(written["acquisition_date"]) == "2025-12-31"
    # The linked period is this acquisition's own period, not an earlier one.
    assert written["is_first_acquisition"] == 1
    assert ("ownership_period", "OP-ZZG-ZZE-2025-12-01") in deal.db_sets
    assert ("Ownership Period", "epm_staging.ownership_periods") in [s[:2] for s in site.synced]
    assert M.frappe.flags.from_business_combination is False


def test_on_submit_submits_a_linked_draft_period_with_a_different_date():
    linked = {"name": "OP-ZZG-ZZE-2025-12-01", "consolidation_group": "ZZG", "data_area_id": "ZZE",
              "effective_date": "2025-12-01", "end_date": None, "ownership_pct": 80,
              "consolidation_method": "full", "docstatus": 0}
    site = _Site(ownership=[linked])
    deal = _deal(share_acquired_pct=80, ownership_period="OP-ZZG-ZZE-2025-12-01")
    deal.validate()
    deal.on_submit()
    assert site.inserted == []
    assert site.loaded.name == "OP-ZZG-ZZE-2025-12-01"
    assert site.loaded.submitted is True
    assert site.loaded.acquisition_price == 8300.0
    assert ("ownership_period", "OP-ZZG-ZZE-2025-12-01") in deal.db_sets


def test_on_submit_falls_back_to_the_date_lookup_when_the_link_is_blank_or_stale():
    # A link to a period deleted since: today's behaviour, a new period on the date.
    site = _Site()
    deal = _deal(share_acquired_pct=80, ownership_period="OP-ZZG-ZZE-1999-01-01")
    deal.validate()
    deal.on_submit()
    assert len(site.inserted) == 1
    assert str(site.inserted[0].effective_date) == "2025-12-31"
    # A cancelled linked period is not updated either.
    cancelled = {"name": "OP-ZZG-ZZE-2025-12-01", "consolidation_group": "ZZG", "data_area_id": "ZZE",
                 "effective_date": "2025-12-01", "end_date": None, "ownership_pct": 80, "docstatus": 2}
    site = _Site(ownership=[cancelled])
    deal = _deal(share_acquired_pct=80, ownership_period="OP-ZZG-ZZE-2025-12-01")
    deal.validate()
    deal.on_submit()
    assert len(site.inserted) == 1
    assert not hasattr(site, "loaded")


# -- the deal is the source of its Ownership Period (PR #202 findings 5, 6) ----

def test_on_submit_aligns_a_linked_draft_period_to_the_deal_before_submitting_it():
    """A Draft period the approval submits must say what the approved deal
    says: the share acquired and the acquisition date. The deal is the source
    even when the Draft was declared by hand with other values."""
    linked = {"name": "OP-ZZG-ZZE-2025-12-01", "consolidation_group": "ZZG", "data_area_id": "ZZE",
              "effective_date": "2025-12-01", "end_date": None, "ownership_pct": 80,
              "consolidation_method": "full", "docstatus": 0}
    site = _Site(ownership=[linked])
    deal = _deal(share_acquired_pct=60, ownership_period="OP-ZZG-ZZE-2025-12-01")
    deal.validate()
    deal.on_submit()
    assert site.inserted == []
    assert site.loaded.name == "OP-ZZG-ZZE-2025-12-01"
    assert site.loaded.submitted is True
    assert site.loaded.ownership_pct == 60
    assert str(site.loaded.effective_date) == "2025-12-31"
    assert site.loaded.acquisition_price == 8300.0


def test_on_submit_records_whether_the_deal_created_its_ownership_period():
    site = _Site()
    deal = _deal(share_acquired_pct=80)
    deal.validate()
    deal.on_submit()
    assert len(site.inserted) == 1
    assert ("created_ownership_period", 1) in deal.db_sets

    existing = {"name": "OP-ZZG-ZZE-2025-12-31", "consolidation_group": "ZZG", "data_area_id": "ZZE",
                "effective_date": "2025-12-31", "end_date": None, "ownership_pct": 80,
                "consolidation_method": "full", "docstatus": 1}
    site = _Site(ownership=[existing])
    deal = _deal(share_acquired_pct=80)
    deal.validate()
    deal.on_submit()
    assert site.inserted == []
    assert ("created_ownership_period", 1) not in deal.db_sets


def test_on_cancel_cancels_the_ownership_period_the_deal_created():
    """The approval created the period; undoing the approval cancels it again
    (its own before_cancel keeps the closed-period gate), under the flag."""
    site = _Site()
    deal = _deal(share_acquired_pct=80)
    deal.validate()
    deal.on_submit()
    created = site.inserted[0].name
    site.synced.clear()
    deal.on_cancel()
    assert site.loaded.name == created
    assert site.loaded.cancelled is True
    assert site.loaded.docstatus == 2
    assert site.flag_at_cancel == [True]
    assert M.frappe.flags.from_business_combination is False
    # The period was cancelled, not blanked: nothing written field by field.
    assert site.loaded.db_sets == []
    assert ("Business Combination", HEADER_TABLE) in [s[:2] for s in site.synced]


def test_on_cancel_resets_the_deal_fields_on_a_period_that_pre_existed():
    """The period was declared before the deal and only received the deal's
    figures: undoing the approval clears those four fields and leaves the
    period standing, under the flag, with its own re-sync."""
    existing = {"name": "OP-ZZG-ZZE-2025-12-31", "consolidation_group": "ZZG", "data_area_id": "ZZE",
                "effective_date": "2025-12-31", "end_date": None, "ownership_pct": 80,
                "consolidation_method": "full", "docstatus": 1}
    site = _Site(ownership=[existing])
    deal = _deal(share_acquired_pct=80)
    deal.validate()
    deal.on_submit()
    site.synced.clear()
    deal.on_cancel()
    assert site.loaded.name == "OP-ZZG-ZZE-2025-12-31"
    assert not getattr(site.loaded, "cancelled", False)
    assert site.loaded.docstatus == 1
    assert site.loaded.db_sets[-4:] == [
        ("acquisition_date", None),
        ("is_first_acquisition", 0),
        ("acquisition_price", 0),
        ("fair_value_adjustment", 0),
    ]
    assert site.flag_at_cancel == []
    assert M.frappe.flags.from_business_combination is False
    synced = [s[:2] for s in site.synced]
    assert synced.index(("Ownership Period", "epm_staging.ownership_periods")) \
        < synced.index(("Business Combination", HEADER_TABLE))


def test_on_cancel_skips_a_blank_or_gone_ownership_period_link():
    site = _Site()
    deal = _deal()
    deal.on_cancel()  # never approved with a period: nothing to undo
    assert not hasattr(site, "loaded")
    assert ("Business Combination", HEADER_TABLE) in [s[:2] for s in site.synced]

    site = _Site()
    deal = _deal(ownership_period="OP-ZZG-ZZE-1999-01-01", created_ownership_period=1)
    deal.on_cancel()  # the period was deleted since
    assert not hasattr(site, "loaded")
    assert M.frappe.flags.from_business_combination is False


def test_json_records_whether_the_deal_created_its_ownership_period():
    import json
    with open(os.path.join(DOCTYPE_DIR, "business_combination", "business_combination.json")) as f:
        fields = {f["fieldname"]: f for f in json.load(f)["fields"]}
    field = fields["created_ownership_period"]
    assert field["fieldtype"] == "Check"
    assert field["read_only"] == 1
    assert field["no_copy"] == 1
    assert "cancel" in field["description"].lower()
    # The deal that SUBMITS a Draft period owns it the same as one it created.
    assert "created or submitted" in field["description"].lower()


# -- the period across cancel and amend (PR #202 second review B1, B4) --------

def _cancelled_period(name, amended_from=None):
    return {"name": name, "consolidation_group": "ZZG", "data_area_id": "ZZE",
            "effective_date": "2025-12-31", "end_date": None, "ownership_pct": 80,
            "consolidation_method": "full", "amended_from": amended_from, "docstatus": 2}


def test_approve_cancel_amend_approve_names_the_new_period_after_the_cancelled_one():
    """Approve → cancel → amend → approve: the cancelled Ownership Period still
    holds the format: name for that node and date, so a second insert with the
    same name is a duplicate. The new period is recorded as an amendment of
    the cancelled one (amended_from), which Frappe names …-1."""
    site = _Site()
    site.root["measurement_period"] = "12 months"
    first = _deal(share_acquired_pct=80)
    first.validate()
    first.on_submit()
    first.on_cancel()
    assert site.ownership[0]["docstatus"] == 2 and site.ownership[0]["name"] == "OP-ZZG-ZZE-2025-12-31"

    amendment = _deal(name="BC-ZZG-ZZE-2025-12-31-1", share_acquired_pct=80,
                      amended_from="BC-ZZG-ZZE-2025-12-31")
    amendment.validate()
    amendment.on_submit()
    assert len(site.inserted) == 2
    period = site.inserted[1]
    assert period.amended_from == "OP-ZZG-ZZE-2025-12-31"
    assert period.name == "OP-ZZG-ZZE-2025-12-31-1"
    assert period.submitted is True
    assert str(period.effective_date) == "2025-12-31" and period.ownership_pct == 80
    assert ("ownership_period", "OP-ZZG-ZZE-2025-12-31-1") in amendment.db_sets
    assert ("created_ownership_period", 1) in amendment.db_sets
    assert M.frappe.flags.from_business_combination is False


def test_a_new_period_amends_the_latest_cancelled_one_when_there_are_several():
    site = _Site(ownership=[_cancelled_period("OP-ZZG-ZZE-2025-12-31"),
                            _cancelled_period("OP-ZZG-ZZE-2025-12-31-1", amended_from="OP-ZZG-ZZE-2025-12-31")])
    deal = _deal(share_acquired_pct=80)
    deal.validate()
    deal.on_submit()
    assert len(site.inserted) == 1
    assert site.inserted[0].amended_from == "OP-ZZG-ZZE-2025-12-31-1"
    assert site.inserted[0].name == "OP-ZZG-ZZE-2025-12-31-2"


def test_a_new_period_carries_no_amended_from_when_nothing_was_cancelled():
    # Another node's or another date's cancelled period is not this one's history.
    other = dict(_cancelled_period("OP-ZZG-ZZE-2025-12-01"), effective_date="2025-12-01")
    site = _Site(ownership=[other])
    deal = _deal(share_acquired_pct=80)
    deal.validate()
    deal.on_submit()
    assert len(site.inserted) == 1
    assert not site.inserted[0].get("amended_from")
    assert site.inserted[0].name == "OP-ZZG-ZZE-2025-12-31"


def test_the_deal_that_submits_a_draft_period_owns_it_and_its_cancel_cancels_the_period():
    """A Draft period the approval submits has no deal behind it but this one:
    cancelling the deal cancels the period again, exactly as for a period the
    deal created — not a blanking of four fields that leaves a submitted
    period standing with no approval behind it."""
    for linked in (True, False):
        draft = {"name": "OP-ZZG-ZZE-2025-12-31", "consolidation_group": "ZZG", "data_area_id": "ZZE",
                 "effective_date": "2025-12-31", "end_date": None, "ownership_pct": 80,
                 "consolidation_method": "full", "docstatus": 0}
        site = _Site(ownership=[draft])
        deal = _deal(share_acquired_pct=80, ownership_period="OP-ZZG-ZZE-2025-12-31" if linked else None)
        deal.validate()
        deal.on_submit()
        assert site.inserted == [], linked
        assert site.loaded.submitted is True, linked
        assert ("created_ownership_period", 1) in deal.db_sets, linked
        deal.on_cancel()
        assert site.loaded.name == "OP-ZZG-ZZE-2025-12-31", linked
        assert site.loaded.cancelled is True and site.loaded.docstatus == 2, linked
        assert site.loaded.db_sets == [], linked  # cancelled, not blanked
        assert site.flag_at_cancel == [True], linked
        assert M.frappe.flags.from_business_combination is False, linked


def test_json_ownership_period_carries_the_standard_amended_from_link():
    import json
    with open(os.path.join(DOCTYPE_DIR, "ownership_period", "ownership_period.json")) as f:
        fields = {f["fieldname"]: f for f in json.load(f)["fields"]}
    field = fields["amended_from"]
    assert field["fieldtype"] == "Link"
    assert field["options"] == "Ownership Period"
    assert field["read_only"] == 1
    assert field["no_copy"] == 1
    assert field["print_hide"] == 1


def test_submit_cancel_and_delete_sync_the_header_and_the_three_children():
    expected = {("Business Combination", HEADER_TABLE)} | {(dt, t) for dt, t in CHILD_TABLES.items()}
    for hook in ("on_submit", "on_cancel", "after_delete"):
        site = _Site()
        deal = _deal()
        deal.validate()
        getattr(deal, hook)()
        assert {s[:2] for s in site.synced} == expected, hook


def test_before_cancel_asserts_the_acquisition_period_is_open():
    site = _Site()
    deal = _deal()
    deal.before_cancel()
    assert site.opened and site.opened[0][:2] == (2025, 12)
    assert "cancel" in site.opened[0][2]


# -- a sold holding cannot be pulled from under its disposal (B3) --------------

#: The approved disposal that sold the holding this deal's approval started.
SOLD = {"name": "BD-ZZG-ZZE-2026-06-30", "ownership_period": "OP-ZZG-ZZE-2025-12-31", "docstatus": 1}


def test_before_cancel_refuses_while_a_business_disposal_sold_the_holding():
    """Undoing the acquisition under an approved disposal of the same holding
    would leave a disposal of nothing: the disposal goes first, by name."""
    _Site(disposals=[SOLD])
    message = _refused(_deal(docstatus=1, ownership_period="OP-ZZG-ZZE-2025-12-31").before_cancel)
    assert "Business Disposal BD-ZZG-ZZE-2026-06-30 sold this holding. Cancel it first." in message


def test_before_cancel_passes_when_no_approved_disposal_links_the_period():
    cases = [
        ([dict(SOLD, docstatus=0)], True),   # a Draft disposal is not a sale yet
        ([dict(SOLD, docstatus=2)], True),   # a cancelled one is none any more
        ([dict(SOLD, ownership_period="OP-ZZG-ZZX-2025-12-31")], True),  # another holding's
        ([], True),
        ([SOLD], False),  # the doctype is not installed yet: never read (guarded like has_deals)
    ]
    for disposals, table in cases:
        site = _Site(disposals=disposals, disposal_table=table)
        _deal(docstatus=1, ownership_period="OP-ZZG-ZZE-2025-12-31").before_cancel()
        assert site.opened and site.opened[0][:2] == (2025, 12), (disposals, table)


# -- the warehouse contract ----------------------------------------------------

def test_field_maps_follow_the_ddl_column_order():
    assert M.BusinessCombination.CH_TABLE == HEADER_TABLE
    assert tuple(M.BusinessCombination.CH_FIELD_MAP) == (
        "name", "consolidation_group", "acquired_entity", "acquisition_date", "share_acquired_pct",
        "consideration_currency", "total_consideration", "net_assets_acquired", "fair_value_adjustments",
        "goodwill", "bargain_purchase_gain", "nci_at_acquisition", "ownership_period")
    children = M.BusinessCombination.CHILD_CONTROLLERS
    assert set(children) == set(CHILD_TABLES)
    maps = {
        "Business Combination Consideration": ("parent", "idx", "component", "amount", "currency",
                                               "settlement_date", "description"),
        "Business Combination Acquired Balance": ("parent", "idx", "main_account", "book_amount",
                                                  "fair_value_adjustment", "note"),
        "Business Combination Cost": ("parent", "idx", "kind", "amount", "currency", "description"),
    }
    for doctype, cls in children.items():
        assert cls.CH_TABLE == CHILD_TABLES[doctype], doctype
        assert tuple(cls.CH_FIELD_MAP) == maps[doctype], doctype
        assert all(k == v for k, v in cls.CH_FIELD_MAP.items()), doctype
        # A child row carries its parent's docstatus: only approved deals' lines
        # reach the warehouse, like the header (resolve_sync_filters keys on
        # is_submittable, which a child table is not).
        assert cls.CH_SYNC_FILTERS == {"docstatus": 1}, doctype


def test_source_sets_the_flag_around_the_ownership_period_and_syncs_four_tables():
    with open(PATH) as f:
        src = f.read()
    assert "frappe.flags.from_business_combination = True" in src
    assert "frappe.flags.from_business_combination = False" in src
    assert "def on_submit" in src and "def on_cancel" in src and "def after_delete" in src
    assert "sync_doctype_after_commit" in src
    assert "self.save()" not in src
    assert "frappe.db.commit" not in src
    assert HEADER_TABLE in src
    for doctype in CHILD_TABLES:
        assert f'"{doctype}"' in src, doctype
