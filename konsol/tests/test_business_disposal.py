"""Business Disposal (konsolidat#198, design 2a): the disposal is a declared
input, the policy lives on the Consolidation Group root, and only the
accounting mechanics are programmed. Host-run.

Three parts: the JSON shape (parent, Proceeds child, workflow, INSTALLED), the
pure ``konsol.business_disposal_model`` (totals and the refusal sentences), and
the controller loaded by file path against a stub frappe (the pattern of
test_business_combination.py): an in-memory site with declared periods, the
group root, Closing rates, the chart and the entity's Ownership Periods, that
records what the controller writes (the closed period, syncs, the flag).
"""
import datetime
import importlib
import importlib.util
import json
import os
import re
import sys
import types
from decimal import Decimal

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(APP_DIR)
DOCTYPE_DIR = os.path.join(APP_DIR, "consolidation", "doctype")
PATH = os.path.join(DOCTYPE_DIR, "business_disposal", "business_disposal.py")

HEADER_TABLE = "epm_staging.business_disposals"
CHILD_TABLE = "epm_staging.business_disposal_proceeds"
CHILD_FOLDER = "business_disposal_proceeds"
CHILD_NAME = "Business Disposal Proceeds"

RETAINED_SENTENCE = (
    "Partial disposals that keep an interest are not supported yet; "
    "dispose of the full holding or record the change as an ownership step"
)


# -- the JSON shape ------------------------------------------------------------

def _json(folder, filename=None):
    path = os.path.join(DOCTYPE_DIR, folder, (filename or folder) + ".json")
    with open(path) as f:
        return json.load(f)


def _fields(doc):
    return {f["fieldname"]: f for f in doc["fields"]}


def test_every_doctype_folder_is_a_package_with_json_and_controller():
    for folder in ("business_disposal", CHILD_FOLDER):
        base = os.path.join(DOCTYPE_DIR, folder)
        for name in ("__init__.py", folder + ".json", folder + ".py"):
            assert os.path.exists(os.path.join(base, name)), f"{folder}/{name} missing"


def test_business_disposal_header_shape():
    doc = _json("business_disposal")
    assert doc["name"] == "Business Disposal"
    assert doc["doctype"] == "DocType"
    assert doc["module"] == "Consolidation"
    assert doc["is_submittable"] == 1
    assert doc.get("istable", 0) == 0
    assert doc["autoname"] == "format:BD-{consolidation_group}-{disposed_entity}-{disposal_date}"
    assert doc["title_field"] == "disposed_entity"
    assert doc["track_changes"] == 1


def test_deal_tab_carries_the_declared_inputs():
    fields = _fields(_json("business_disposal"))
    assert fields["tab_deal"]["fieldtype"] == "Tab Break"
    expected = {
        "consolidation_group": ("Data", None),
        "disposed_entity": ("Link", "Entity"),
        "disposal_date": ("Date", None),
        "share_disposed_pct": ("Percent", None),
        "proceeds_currency": ("Link", "ISO Currency"),
    }
    for fn, (ftype, options) in expected.items():
        assert fields[fn]["fieldtype"] == ftype, fn
        assert fields[fn].get("reqd") == 1, f"{fn} is always required"
        if options:
            assert fields[fn]["options"] == options, fn
    # Must be 0: said by a sentence on validate, never by a default.
    retained = fields["retained_interest_pct"]
    assert retained["fieldtype"] == "Percent"
    assert retained.get("reqd", 0) == 0
    assert "default" not in retained
    assert "not supported yet" in retained["description"]
    assert fields["ownership_period"]["fieldtype"] == "Link"
    assert fields["ownership_period"]["options"] == "Ownership Period"
    assert fields["ownership_period"]["read_only"] == 1


def test_proceeds_table_and_read_only_result():
    fields = _fields(_json("business_disposal"))
    assert fields["proceeds"]["fieldtype"] == "Table"
    assert fields["proceeds"]["options"] == CHILD_NAME
    assert fields["proceeds"]["reqd"] == 1, "a disposal has at least one proceeds line"
    assert fields["tab_result"]["fieldtype"] == "Tab Break"
    assert fields["total_proceeds"]["fieldtype"] == "Currency"
    assert fields["total_proceeds"]["read_only"] == 1
    assert "default" not in fields["total_proceeds"]


def test_status_is_the_workflow_state_and_the_document_can_be_amended():
    doc = _json("business_disposal")
    fields = _fields(doc)
    assert fields["status"]["fieldtype"] == "Select"
    assert fields["status"]["options"] == "Draft\nPending Approval\nApproved\nCancelled"
    assert fields["status"]["read_only"] == 1
    assert fields["description"]["fieldtype"] == "Small Text"
    assert fields["amended_from"]["fieldtype"] == "Link"
    assert fields["amended_from"]["options"] == "Business Disposal"
    assert fields["amended_from"]["read_only"] == 1
    for f in fields.values():
        assert "default" not in f or f["fieldname"] == "status", f["fieldname"]
    roles = {p["role"]: p for p in doc["permissions"]}
    assert roles["EPM Admin"]["submit"] == 1 and roles["EPM Admin"]["cancel"] == 1
    assert roles["EPM Analyst"]["create"] == 1 and roles["EPM Analyst"].get("submit", 0) == 0
    assert roles["EPM User"].get("write", 0) == 0


def test_reopen_data_records_what_the_period_held_before_the_close():
    """PR #202 finding 4: cancelling the approval must give the Ownership
    Period back what it held before the disposal closed it, not blank it. The
    approval records those values on the disposal in a hidden, read-only
    section; the cancel restores exactly them."""
    doc = _json("business_disposal")
    fields = _fields(doc)
    assert fields["reopen_section"]["fieldtype"] == "Section Break"
    assert fields["reopen_section"]["label"] == "Reopen Data"
    assert fields["reopen_section"]["hidden"] == 1
    expected = {"previous_end_date": "Date", "previous_is_disposal": "Check",
                "previous_disposal_date": "Date", "previous_disposal_price": "Currency"}
    order = [f["fieldname"] for f in doc["fields"]]
    for fn, ftype in expected.items():
        assert fields[fn]["fieldtype"] == ftype, fn
        assert fields[fn]["read_only"] == 1, f"{fn} is set by the approval"
        assert fields[fn]["no_copy"] == 1, f"{fn} belongs to this approval, not an amendment"
        assert order.index(fn) > order.index("reopen_section"), fn
        assert "before" in fields[fn]["description"].lower(), fn


def test_proceeds_child_fields():
    doc = _json(CHILD_FOLDER)
    assert doc["name"] == CHILD_NAME
    assert doc["module"] == "Consolidation"
    assert doc["istable"] == 1
    assert doc.get("is_submittable", 0) == 0
    fields = _fields(doc)
    assert fields["component"]["fieldtype"] == "Select"
    assert fields["component"]["options"] == "Cash\nDeferred\nContingent\nOther"
    assert fields["component"]["reqd"] == 1
    assert fields["amount"]["fieldtype"] == "Currency" and fields["amount"]["reqd"] == 1
    assert fields["currency"]["fieldtype"] == "Link" and fields["currency"]["options"] == "ISO Currency"
    assert fields["currency"]["reqd"] == 1
    assert fields["settlement_date"]["fieldtype"] == "Date" and fields["settlement_date"].get("reqd", 0) == 0
    assert fields["description"]["fieldtype"] == "Data" and fields["description"].get("reqd", 0) == 0


def test_workflow_copies_the_approval_shape_and_is_installed():
    wf = _json("business_disposal", "business_disposal_workflow")
    assert wf["doctype"] == "Workflow"
    assert wf["document_type"] == "Business Disposal"
    assert wf["workflow_name"] == "Business Disposal Workflow"
    assert wf["is_active"] == 1
    assert wf["workflow_state_field"] == "status"
    states = {s["state"]: s for s in wf["states"]}
    assert set(states) == {"Draft", "Pending Approval", "Approved", "Cancelled"}
    assert states["Draft"]["doc_status"] == "0" and states["Draft"]["allow_edit"] == "EPM Analyst"
    assert states["Pending Approval"]["doc_status"] == "0" and states["Pending Approval"]["allow_edit"] == "EPM Admin"
    assert states["Approved"]["doc_status"] == "1" and states["Approved"]["allow_edit"] == "EPM Admin"
    transitions = {(t["state"], t["action"]): t["next_state"] for t in wf["transitions"]}
    assert transitions == {("Draft", "Send for Approval"): "Pending Approval",
                           ("Pending Approval", "Reject"): "Draft",
                           ("Pending Approval", "Approve"): "Approved"}
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        src = f.read()
    installed = re.findall(r'"([^"]+)"', src.split("INSTALLED = (")[1].split(")")[0])
    assert "Business Disposal" in installed
    assert "Business Combination" in installed and "Consolidation Adjustment" in installed


def test_a_cancelled_disposal_is_marked_cancelled():
    """Frappe's `set_workflow_state_on_action` writes the state whose
    `doc_status` is "2" when a document is cancelled; without one the disposal
    keeps saying "Approved" after its cancel (PR #202 finding 2). Cancel drives
    the state, so no transition leads into it."""
    wf = _json("business_disposal", "business_disposal_workflow")
    cancelled = [s for s in wf["states"] if s["doc_status"] == "2"]
    assert len(cancelled) == 1, "exactly one cancelled state"
    assert cancelled[0]["state"] == "Cancelled"
    assert cancelled[0]["allow_edit"] == "EPM Admin"
    assert not [t for t in wf["transitions"] if t["next_state"] == "Cancelled"], "cancel is not a transition"
    assert "Cancelled" in _fields(_json("business_disposal"))["status"]["options"].split("\n")


# -- the pure model --------------------------------------------------------------

def _model():
    return importlib.import_module("konsol.business_disposal_model")


ROOT_IFRS = {
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
HEADER = {"consolidation_group": "ZZG", "disposed_entity": "ZZE", "disposal_date": "2025-12-31",
          "share_disposed_pct": 80, "retained_interest_pct": 0, "proceeds_currency": "EUR"}
CASH_9000 = [{"component": "Cash", "amount": 9000, "currency": "EUR"}]
EUR_ONLY = lambda currency: 1.0 if currency == "EUR" else None  # noqa: E731


def _facts(**over):
    facts = {"current_holding_pct": 80, "rate_to_group": EUR_ONLY, "is_published_leaf": lambda a: True}
    facts.update(over)
    return facts


def test_totals_sums_the_proceeds_translated_by_the_callback():
    m = _model()
    rate = lambda c: {"EUR": 1.0, "USD": 0.9}[c]  # noqa: E731
    lines = [{"component": "Cash", "amount": 8000, "currency": "EUR"},
             {"component": "Deferred", "amount": 1000, "currency": "USD"},
             {"component": "Contingent", "amount": 100}]  # no currency: the header's
    result = m.totals(HEADER, lines, rate)
    assert result["total_proceeds"] == Decimal("9000.00")
    assert isinstance(result["total_proceeds"], Decimal)


def test_totals_refuses_a_currency_with_no_rate_by_name():
    m = _model()
    try:
        m.totals(HEADER, [{"component": "Cash", "amount": 1, "currency": "USD"}], EUR_ONLY)
    except ValueError as e:
        assert "USD" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_a_full_disposal_at_the_current_holding_has_no_problems():
    m = _model()
    assert m.problems(HEADER, CASH_9000, ROOT_IFRS, _facts()) == []
    # A disposal with no proceeds is a real case (the warehouse names it by a warning).
    zero = [{"component": "Cash", "amount": 0, "currency": "EUR"}]
    assert m.problems(HEADER, zero, ROOT_IFRS, _facts()) == []


def test_at_least_one_proceeds_line_and_no_negative_amount():
    m = _model()
    found = m.problems(HEADER, [], ROOT_IFRS, _facts())
    assert any("at least one Proceeds line" in s for s in found), found
    found = m.problems(HEADER, [{"component": "Cash", "amount": -5, "currency": "EUR"}], ROOT_IFRS, _facts())
    assert any("Proceeds line 1 (Cash)" in s and "below 0" in s for s in found), found


def test_a_retained_interest_is_refused_by_the_sentence():
    m = _model()
    header = dict(HEADER, share_disposed_pct=60, retained_interest_pct=20)
    found = m.problems(header, CASH_9000, ROOT_IFRS, _facts())
    assert any(RETAINED_SENTENCE in s for s in found), found


def test_the_share_must_equal_the_groups_current_holding():
    m = _model()
    found = m.problems(dict(HEADER, share_disposed_pct=60), CASH_9000, ROOT_IFRS, _facts(current_holding_pct=80))
    assert any("60" in s and "80" in s and "current holding" in s for s in found), found
    found = m.problems(HEADER, CASH_9000, ROOT_IFRS, _facts(current_holding_pct=None))
    assert any("no Ownership Period" in s and "ZZE" in s and "2025-12-31" in s for s in found), found
    for bad in (0, -1, 101, None, ""):
        found = m.problems(dict(HEADER, share_disposed_pct=bad), CASH_9000, ROOT_IFRS, _facts(current_holding_pct=bad))
        assert any("Share Disposed must be above 0 and at most 100" in s for s in found), (bad, found)


def test_the_policy_and_the_disposal_accounts_are_checked():
    m = _model()
    root = dict(ROOT_IFRS, accounting_framework="", disposal_gain_loss_account="")
    found = m.problems(HEADER, CASH_9000, root, _facts())
    assert any("Accounting Framework is required once the group has a Business Combination" in s
               for s in found), found
    assert any("Gain or Loss on Disposal Account (P&L) is required" in s for s in found), found
    # The goodwill and proceeds accounts are needed too; the acquisition-only ones are not.
    root = dict(ROOT_IFRS, goodwill_account="", disposal_proceeds_account="", nci_account="",
                fair_value_adjustment_account="")
    found = m.problems(HEADER, CASH_9000, root, _facts())
    assert any("Goodwill Account is required" in s for s in found), found
    assert any("Disposal Proceeds Account is required" in s for s in found), found
    assert not any("Non-controlling" in s or "Fair Value Adjustment Account" in s for s in found), found
    # An account that is not a Published leaf is named.
    found = m.problems(HEADER, CASH_9000, ROOT_IFRS, _facts(is_published_leaf=lambda a: a != "ZZ1900"))
    assert any("ZZ1900" in s for s in found), found


# -- the controller under a stub frappe ----------------------------------------

class Refused(Exception):
    pass


class _Doc:
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
    for name in [n for n in sys.modules if n.startswith("konsol.consolidation.doctype.business_disposal")]:
        del sys.modules[name]
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("bd_under_test", PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


M = _load() if os.path.exists(PATH) else None


class _Row(dict):
    __getattr__ = dict.get


def _match(row, filters):
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
            if field.endswith("_date"):
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


PERIOD_DEC_2025 = {"fiscal_year": 2025, "fiscal_period": 12, "period_code": "P12",
                   "period_type": "Regular", "start_date": "2025-12-01", "end_date": "2025-12-31"}
#: name -> (status, is_group, account_type)
ACCOUNTS = {"ZZ1800": ("Published", 0, "Asset"), "ZZ4950": ("Published", 0, "Revenue"),
            "ZZ1900": ("Published", 0, "Asset")}
HOLDING_80 = {"name": "OP-ZZG-ZZE-2020-01-01", "consolidation_group": "ZZG", "data_area_id": "ZZE",
              "effective_date": "2020-01-01", "end_date": None, "ownership_pct": 80,
              "consolidation_method": "full", "docstatus": 1}


class _OwnershipPeriodDoc(_Doc):
    CH_TABLE = "epm_staging.ownership_periods"
    CH_FIELD_MAP = {"consolidation_group": "consolidation_group", "data_area_id": "data_area_id"}


class _Site:
    def __init__(self, *, periods=None, root=None, rates=None, accounts=None, ownership=None):
        self.periods = [dict(p) for p in (periods if periods is not None else [PERIOD_DEC_2025])]
        self.root = dict(ROOT_IFRS) if root is None else root
        self.rates = dict(rates or {})  # (from, to, fy, fp) -> (quote, quoted_per)
        self.accounts = dict(ACCOUNTS if accounts is None else accounts)
        self.ownership = [dict(o) for o in (ownership if ownership is not None else [HOLDING_80])]
        self.synced, self.opened, self.flag_at_write, self.loaded = [], [], [], []
        site = self
        M.frappe.flags = types.SimpleNamespace(from_business_combination=False)
        M.frappe.db = types.SimpleNamespace(sql=self._sql, get_value=self._get_value, exists=self._exists)
        M.frappe.get_all = self._get_all
        M.frappe.get_doc = self._get_doc
        M.sync_doctype_after_commit = lambda dt, table, fm: site.synced.append((dt, table, tuple(fm)))
        M.assert_open = lambda fy, fp, action="run": site.opened.append((fy, fp, action))

    def _sql(self, query, values=None, as_dict=False):
        assert "`tabEPM Fiscal Year Period`" in query and "`tabEPM Fiscal Year`" in query, query
        date = _getdate(values["d"] if isinstance(values, dict) else values[0])
        rows = [_Row(p) for p in self.periods if _getdate(p["start_date"]) <= date <= _getdate(p["end_date"])]
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
            values = dict(zip(("status", "is_group", "account_type"), row))
            if isinstance(fieldname, (list, tuple)):
                return tuple(values[f] for f in fieldname)
            return values[fieldname]
        raise AssertionError(f"unexpected get_value on {doctype}")

    def _get_all(self, doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **k):
        if doctype == "Group Exchange Rate":
            assert filters.get("docstatus") == 1 and filters.get("rate_type") == "Closing", filters
            key = (filters["from_currency"], filters["to_currency"], filters["fiscal_year"], filters["fiscal_period"])
            if key not in self.rates:
                return []
            quote, per = self.rates[key]
            return [_Row(quote=quote, quoted_per=per)]
        if doctype == "Ownership Period":
            rows = [_Row(r) for r in self.ownership if _match(r, filters or {})]
            if order_by:
                field, _, direction = order_by.partition(" ")
                rows.sort(key=lambda r: _getdate(r[field]) if field.endswith("_date") else r[field],
                          reverse=direction.strip().lower() == "desc")
            return rows[:limit_page_length] if limit_page_length else rows
        raise AssertionError(f"unexpected get_all on {doctype}")

    def _exists(self, doctype, name=None, **k):
        assert doctype == "Ownership Period", doctype
        filters = dict(name) if isinstance(name, dict) else {"name": name}
        return next((row["name"] for row in self.ownership if _match(row, filters)), None)

    def _get_doc(self, doctype, name=None):
        assert doctype == "Ownership Period", doctype
        for row in self.ownership:
            if row["name"] == name:
                doc = _OwnershipPeriodDoc(**row)
                site = self
                original = doc.db_set

                def db_set(field, value, *a, **k):
                    site.flag_at_write.append(M.frappe.flags.from_business_combination)
                    original(field, value)

                doc.db_set = db_set
                self.loaded.append(doc)
                return doc
        raise AssertionError(f"no Ownership Period {name}")


def _deal(**over):
    fields = dict(
        name="BD-ZZG-ZZE-2025-12-31", doctype="Business Disposal", docstatus=0,
        consolidation_group="ZZG", disposed_entity="ZZE", disposal_date="2025-12-31",
        share_disposed_pct=80, retained_interest_pct=0, proceeds_currency="EUR",
        proceeds=[dict(r) for r in CASH_9000], amended_from=None, ownership_period=None,
    )
    fields.update(over)
    return M.BusinessDisposal(**fields)


def _refused(fn):
    try:
        fn()
    except Refused as e:
        return str(e)
    raise AssertionError("expected the controller to refuse")


def test_validate_computes_the_total_proceeds():
    _Site()
    deal = _deal()
    deal.validate()
    assert deal.total_proceeds == 9000.0


def test_validate_translates_a_deferred_line_at_the_periods_closing_rate():
    _Site(rates={("USD", "EUR", 2025, 12): (90, 100)})  # 0.90 EUR per USD, quoted per 100
    deal = _deal(proceeds=[{"component": "Cash", "amount": 8000, "currency": "EUR"},
                           {"component": "Deferred", "amount": 1000, "currency": "USD"}])
    deal.validate()
    assert deal.total_proceeds == 8900.0


def test_validate_refuses_a_missing_closing_rate_naming_the_period():
    _Site()
    message = _refused(_deal(proceeds=[{"component": "Cash", "amount": 1, "currency": "USD"}]).validate)
    assert "USD" in message and "Closing" in message and "P12" in message and "2025" in message


def test_validate_refuses_a_date_no_declared_period_covers():
    _Site()
    message = _refused(_deal(disposal_date="2027-03-31").validate)
    assert "2027-03-31" in message and "declared" in message.lower()


def test_validate_refuses_a_group_without_a_root():
    _Site()
    message = _refused(_deal(consolidation_group="ZZ-NOWHERE").validate)
    assert "ZZ-NOWHERE" in message


def test_validate_throws_the_models_sentences():
    _Site()
    message = _refused(_deal(share_disposed_pct=60, retained_interest_pct=20).validate)
    assert RETAINED_SENTENCE in message
    _Site()
    message = _refused(_deal(share_disposed_pct=60).validate)
    assert "current holding" in message and "80" in message
    site = _Site()
    site.root["disposal_gain_loss_account"] = ""
    message = _refused(_deal().validate)
    assert "Gain or Loss on Disposal Account (P&L) is required" in message


def test_validate_reads_the_holding_from_the_period_covering_the_disposal_date():
    ended = dict(HOLDING_80, end_date="2024-12-31")
    later = {"name": "OP-ZZG-ZZE-2026-01-01", "consolidation_group": "ZZG", "data_area_id": "ZZE",
             "effective_date": "2026-01-01", "end_date": None, "ownership_pct": 100, "docstatus": 1}
    _Site(ownership=[ended, later])
    message = _refused(_deal().validate)
    assert "no Ownership Period" in message
    # A draft or cancelled period is not a holding.
    _Site(ownership=[dict(HOLDING_80, docstatus=0)])
    assert "no Ownership Period" in _refused(_deal().validate)
    # The latest submitted period at or before the date wins.
    stepped = {"name": "OP-ZZG-ZZE-2023-01-01", "consolidation_group": "ZZG", "data_area_id": "ZZE",
               "effective_date": "2023-01-01", "end_date": None, "ownership_pct": 100, "docstatus": 1}
    _Site(ownership=[dict(HOLDING_80, end_date="2022-12-31"), stepped])
    _deal(share_disposed_pct=100).validate()


def test_before_submit_asserts_the_disposal_period_is_open_and_the_accounts_still_declared():
    site = _Site()
    deal = _deal()
    deal.validate()
    deal.before_submit()
    assert site.opened and site.opened[0][:2] == (2025, 12)
    assert "business disposal" in site.opened[0][2]
    site.root["disposal_proceeds_account"] = ""
    message = _refused(deal.before_submit)
    assert "Disposal Proceeds Account is required" in message

    def closed(fy, fp, action="run"):
        raise Refused(f"Cannot {action}: fiscal period {fp} of FY{fy} is closed.")

    M.assert_open = closed
    assert "closed" in _refused(deal.before_submit)


def test_on_submit_closes_the_ownership_period_under_the_flag_and_links_it():
    site = _Site()
    deal = _deal()
    deal.validate()
    deal.on_submit()
    assert len(site.loaded) == 1
    period = site.loaded[0]
    written = dict(period.db_sets)
    assert str(written["end_date"]) == "2025-12-31"
    assert written["is_disposal"] == 1
    assert str(written["disposal_date"]) == "2025-12-31"
    assert written["disposal_price"] == 9000.0
    assert site.flag_at_write and all(site.flag_at_write)
    assert M.frappe.flags.from_business_combination is False
    assert ("ownership_period", "OP-ZZG-ZZE-2020-01-01") in deal.db_sets
    assert ("Ownership Period", "epm_staging.ownership_periods") in [s[:2] for s in site.synced]


def test_on_submit_closes_the_linked_period_rather_than_the_one_matched_by_date():
    """A migrated disposal (P9) links its source Ownership Period; the approval
    closes that one, not whichever period ``_current_holding`` finds by date
    (P9b). A link to a period deleted since falls back to the current holding."""
    linked = {"name": "OP-ZZG-ZZE-2019-06-01", "consolidation_group": "ZZG", "data_area_id": "ZZE",
              "effective_date": "2019-06-01", "end_date": "2025-12-31", "ownership_pct": 80,
              "consolidation_method": "full", "docstatus": 1}
    site = _Site(ownership=[HOLDING_80, linked])
    deal = _deal(ownership_period="OP-ZZG-ZZE-2019-06-01")
    deal.validate()
    deal.on_submit()
    assert [p.name for p in site.loaded] == ["OP-ZZG-ZZE-2019-06-01"]
    written = dict(site.loaded[0].db_sets)
    assert str(written["end_date"]) == "2025-12-31"
    assert written["is_disposal"] == 1
    assert written["disposal_price"] == 9000.0
    assert site.flag_at_write and all(site.flag_at_write)
    assert M.frappe.flags.from_business_combination is False
    assert ("ownership_period", "OP-ZZG-ZZE-2019-06-01") in deal.db_sets

    site = _Site()
    deal = _deal(ownership_period="OP-ZZG-ZZE-1999-01-01")  # deleted since
    deal.validate()
    deal.on_submit()
    assert [p.name for p in site.loaded] == ["OP-ZZG-ZZE-2020-01-01"]
    assert ("ownership_period", "OP-ZZG-ZZE-2020-01-01") in deal.db_sets


def test_on_submit_ignores_a_linked_period_that_is_not_submitted():
    """PR #202 finding 3: only a submitted Ownership Period is a holding. A link
    to a cancelled or Draft period falls back to the current holding, as the
    Business Combination does."""
    for docstatus in (2, 0):
        stale = {"name": "OP-ZZG-ZZE-2019-06-01", "consolidation_group": "ZZG", "data_area_id": "ZZE",
                 "effective_date": "2019-06-01", "end_date": None, "ownership_pct": 80,
                 "consolidation_method": "full", "docstatus": docstatus}
        site = _Site(ownership=[HOLDING_80, stale])
        deal = _deal(ownership_period="OP-ZZG-ZZE-2019-06-01")
        deal.validate()
        deal.on_submit()
        assert [p.name for p in site.loaded] == ["OP-ZZG-ZZE-2020-01-01"], docstatus
        assert ("ownership_period", "OP-ZZG-ZZE-2020-01-01") in deal.db_sets, docstatus


def test_on_cancel_restores_what_the_period_held_before_the_close():
    """PR #202 finding 4: the period the disposal closed may already have had
    an end date (an ownership step planned after the disposal date); the
    cancel gives it back exactly what the approval recorded, not blanks."""
    planned = dict(HOLDING_80, end_date="2026-06-30", is_disposal=0, disposal_date=None, disposal_price=0)
    site = _Site(ownership=[planned])
    deal = _deal()
    deal.validate()
    deal.on_submit()
    recorded = dict(deal.db_sets)
    assert str(recorded["previous_end_date"]) == "2026-06-30"
    assert recorded["previous_is_disposal"] == 0
    assert recorded["previous_disposal_date"] is None
    assert recorded["previous_disposal_price"] == 0
    # The close itself is still written.
    assert str(dict(site.loaded[0].db_sets)["end_date"]) == "2025-12-31"

    del site.flag_at_write[:]
    deal.on_cancel()
    period = site.loaded[-1]
    assert period.name == "OP-ZZG-ZZE-2020-01-01"
    restored = period.db_sets[-4:]
    assert str(restored[0][1]) == "2026-06-30" and restored[0][0] == "end_date"
    assert restored[1] == ("is_disposal", 0)
    assert restored[2] == ("disposal_date", None)
    assert restored[3] == ("disposal_price", 0)
    assert site.flag_at_write and all(site.flag_at_write)
    assert M.frappe.flags.from_business_combination is False


def test_before_cancel_refuses_when_a_later_ownership_period_exists():
    """PR #202 finding 4: reopening a period under a later submitted period of
    the same node would overlap it; the later one goes first."""
    closed = dict(HOLDING_80, end_date="2025-12-31", is_disposal=1,
                  disposal_date="2025-12-31", disposal_price=9000)
    later = {"name": "OP-ZZG-ZZE-2026-01-01", "consolidation_group": "ZZG", "data_area_id": "ZZE",
             "effective_date": "2026-01-01", "end_date": None, "ownership_pct": 100, "docstatus": 1}
    _Site(ownership=[closed, later])
    message = _refused(_deal(docstatus=1, ownership_period="OP-ZZG-ZZE-2020-01-01").before_cancel)
    assert "Cancel the later Ownership Period(s) first" in message
    assert "OP-ZZG-ZZE-2026-01-01" in message
    # A later Draft or cancelled period, or another entity's period, does not block.
    others = [dict(later, docstatus=0), dict(later, name="OP-ZZG-ZZE-2026-02-01", docstatus=2),
              dict(later, name="OP-ZZG-ZZX-2026-01-01", data_area_id="ZZX")]
    site = _Site(ownership=[closed, *others])
    _deal(docstatus=1, ownership_period="OP-ZZG-ZZE-2020-01-01").before_cancel()
    assert site.opened and site.opened[0][:2] == (2025, 12)


def test_on_cancel_reopens_the_ownership_period_it_closed():
    """Cancelling an approved disposal undoes what its approval did: the period
    it ended is open again and the entity is no longer disposed of (P7b)."""
    site = _Site()
    deal = _deal()
    deal.validate()
    deal.on_submit()
    assert deal.ownership_period == "OP-ZZG-ZZE-2020-01-01"
    del site.synced[:]
    del site.flag_at_write[:]
    deal.on_cancel()
    assert len(site.loaded) == 2, "on_cancel loads the linked Ownership Period"
    period = site.loaded[-1]
    assert period.name == "OP-ZZG-ZZE-2020-01-01"
    assert period.db_sets[-4:] == [("end_date", None), ("is_disposal", 0),
                                   ("disposal_date", None), ("disposal_price", 0)]
    assert site.flag_at_write and all(site.flag_at_write), "the reset goes through the guard"
    assert M.frappe.flags.from_business_combination is False
    assert ("Ownership Period", "epm_staging.ownership_periods",
            tuple(_OwnershipPeriodDoc.CH_FIELD_MAP)) in site.synced
    # Reopened before the disposal itself leaves the warehouse.
    synced = [s[0] for s in site.synced]
    assert synced.index("Ownership Period") < synced.index("Business Disposal")


def test_on_cancel_skips_a_period_that_no_longer_exists_and_without_a_link():
    site = _Site()
    deal = _deal(ownership_period="OP-ZZG-ZZE-1999-01-01")  # deleted since
    deal.on_cancel()
    assert site.loaded == []
    assert M.frappe.flags.from_business_combination is False
    assert ("Business Disposal", HEADER_TABLE) in [s[:2] for s in site.synced], "still synced"
    site = _Site()
    _deal(ownership_period=None).on_cancel()
    assert site.loaded == []
    assert not any(s[0] == "Ownership Period" for s in site.synced)


def test_submit_cancel_and_delete_sync_the_header_and_the_child():
    expected = {("Business Disposal", HEADER_TABLE), (CHILD_NAME, CHILD_TABLE)}
    for hook in ("on_submit", "on_cancel", "after_delete"):
        site = _Site()
        deal = _deal()
        deal.validate()
        getattr(deal, hook)()
        assert expected <= {s[:2] for s in site.synced}, hook


def test_before_cancel_asserts_the_disposal_period_is_open():
    site = _Site()
    _deal().before_cancel()
    assert site.opened and site.opened[0][:2] == (2025, 12)
    assert "cancel" in site.opened[0][2]


def test_field_maps_follow_the_ddl_column_order():
    assert M.BusinessDisposal.CH_TABLE == HEADER_TABLE
    assert tuple(M.BusinessDisposal.CH_FIELD_MAP) == (
        "name", "consolidation_group", "disposed_entity", "disposal_date", "share_disposed_pct",
        "retained_interest_pct", "proceeds_currency", "total_proceeds", "ownership_period")
    child = M.BusinessDisposal.CHILD_CONTROLLERS[CHILD_NAME]
    assert child.CH_TABLE == CHILD_TABLE
    assert tuple(child.CH_FIELD_MAP) == ("parent", "idx", "component", "amount", "currency",
                                         "settlement_date", "description")
    assert all(k == v for k, v in child.CH_FIELD_MAP.items())
    assert child.CH_SYNC_FILTERS == {"docstatus": 1}, "only approved disposals' lines reach the warehouse"


def test_source_keeps_the_hooks_clean():
    with open(PATH) as f:
        src = f.read()
    assert "frappe.flags.from_business_combination = True" in src
    assert "frappe.flags.from_business_combination = False" in src
    assert "def on_submit" in src and "def on_cancel" in src and "def after_delete" in src
    assert "self.save()" not in src
    assert "frappe.db.commit" not in src
    assert HEADER_TABLE in src and f'"{CHILD_NAME}"' in src
