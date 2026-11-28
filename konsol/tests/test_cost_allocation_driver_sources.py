"""konsol#269 phase 2b-1, task A: a Cost Allocation Driver declares where its
values come from.

Q3 = 3B (Deepak Pai, 13 Nov 2026): three driver sources. Q1, Q2 and Q7 = A
(Deepak Pai, 27 Nov 2026): fixed weights carry dated validity windows that may
not overlap; an Account Set driver declares As Booked or Reversed; fixed
weights are scenario-agnostic.

Shape tests read the JSON. Controller tests load the driver by file path
against a stub frappe and stub the three reads (dimension status, In Budget,
members), the pattern of test_cost_allocation_doctypes.py. The patch runs
against a recording db stub.
"""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EPM_DIR = os.path.join(APP_DIR, "epm", "doctype")
DRIVER = "cost_allocation_driver"
FIXED = "cost_allocation_driver_fixed_weight"
ACCOUNT = "cost_allocation_driver_account"
DRIVER_PY = os.path.join(EPM_DIR, DRIVER, DRIVER + ".py")
PATCH_ID = "konsol.patches.set_cost_allocation_driver_source"
PATCH_PY = os.path.join(APP_DIR, "patches", "set_cost_allocation_driver_source.py")

UPLOADED_EVAL = "eval:doc.source=='Uploaded Statistic'"
FIXED_EVAL = "eval:doc.source=='Fixed Weights'"
ACCOUNT_EVAL = "eval:doc.source=='Account Set'"
LAYOUT_TYPES = {"Tab Break", "Section Break", "Column Break"}


def _doc(folder):
    with open(os.path.join(EPM_DIR, folder, folder + ".json")) as f:
        return json.load(f)


def _by_name(folder):
    return {f["fieldname"]: f for f in _doc(folder)["fields"]}


# -- shape: the driver ---------------------------------------------------------

def test_driver_source_is_a_required_select_defaulting_to_uploaded_statistic():
    f = _by_name(DRIVER)["source"]
    assert f["fieldtype"] == "Select"
    assert f["label"] == "Source"
    assert f["options"] == "Uploaded Statistic\nFixed Weights\nAccount Set"
    assert f["reqd"] == 1
    assert f["default"] == "Uploaded Statistic"
    assert f["description"] == "Where the driver's values come from."
    assert f.get("in_list_view") == 1


def test_driver_carry_forward_shows_only_for_an_uploaded_statistic():
    f = _by_name(DRIVER)["carry_forward"]
    assert f["depends_on"] == UPLOADED_EVAL


def test_driver_has_a_values_tab_with_two_labelled_sections():
    fields = _doc(DRIVER)["fields"]
    tabs = [f["label"] for f in fields if f["fieldtype"] == "Tab Break"]
    assert tabs == ["Driver", "Values"]
    values_tab = next(i for i, f in enumerate(fields)
                      if f["fieldtype"] == "Tab Break" and f["label"] == "Values")
    after = fields[values_tab + 1:]
    sections = [f["label"] for f in after if f["fieldtype"] == "Section Break"]
    assert sections == ["Fixed Weights", "Account Set"]
    # the source-specific fields live under the Values tab, in their section
    names = [f["fieldname"] for f in after if f["fieldtype"] not in LAYOUT_TYPES]
    assert names == ["fixed_weights", "accounts", "value_sign"]
    for f in after:
        if f["fieldtype"] == "Section Break":
            assert f["depends_on"] == (FIXED_EVAL if f["label"] == "Fixed Weights" else ACCOUNT_EVAL), f


def test_driver_fixed_weights_table_is_mandatory_and_shown_for_fixed_weights_only():
    f = _by_name(DRIVER)["fixed_weights"]
    assert (f["fieldtype"], f["options"]) == ("Table", "Cost Allocation Driver Fixed Weight")
    assert f["label"] == "Fixed Weights"
    assert f["depends_on"] == FIXED_EVAL
    assert f["mandatory_depends_on"] == FIXED_EVAL
    assert not f.get("reqd")
    assert f.get("description")


def test_driver_account_set_fields_are_mandatory_and_shown_for_account_set_only():
    fields = _by_name(DRIVER)
    f = fields["accounts"]
    assert (f["fieldtype"], f["options"]) == ("Table", "Cost Allocation Driver Account")
    assert f["label"] == "Accounts"
    assert f["depends_on"] == ACCOUNT_EVAL
    assert f["mandatory_depends_on"] == ACCOUNT_EVAL
    assert not f.get("reqd")
    assert "descendants" in f["description"]
    f = fields["value_sign"]
    assert f["fieldtype"] == "Select"
    assert f["label"] == "Value Sign"
    assert f["options"] == "\nAs Booked\nReversed"     # blank first: no default (CA-2B1 BUG-1)
    assert f["depends_on"] == ACCOUNT_EVAL
    assert f["mandatory_depends_on"] == ACCOUNT_EVAL
    assert not f.get("reqd")
    assert not f.get("default"), "no silent sign: the user declares it"
    assert f["description"] == "Reversed turns credit balances (e.g. revenue) positive."


def test_driver_source_sits_in_the_driver_section_before_carry_forward():
    names = [f["fieldname"] for f in _doc(DRIVER)["fields"]]
    assert names.index("source") < names.index("carry_forward")
    assert names.index("source") < names.index("values_tab")


# -- shape: the two child doctypes --------------------------------------------

def test_fixed_weight_child_shape():
    doc = _doc(FIXED)
    assert doc["name"] == "Cost Allocation Driver Fixed Weight"
    assert doc["doctype"] == "DocType"
    assert doc["istable"] == 1
    assert doc["module"] == "EPM"
    assert doc["engine"] == "InnoDB"
    got = [(f["fieldname"], f["fieldtype"], f.get("reqd", 0)) for f in doc["fields"]]
    assert got == [
        ("member", "Data", 1),
        ("value", "Float", 1),
        ("effective_from", "Date", 1),
        ("effective_to", "Date", 0),
    ]
    fields = _by_name(FIXED)
    assert fields["member"]["description"] == "Must be a member of the driver's dimension."
    assert fields["value"]["description"] == "A share, normalised over each step's receivers."
    assert fields["value"].get("non_negative") == 1
    # 2b-2 rv M1: without a precision the Desk rounds a Float to the site's
    # float_precision (ControlFloat.parse, often 3) before it is saved. Driver
    # values are stored at 9 places (Run Weight.driver_value); match it.
    assert fields["value"]["precision"] == "9"
    assert fields["effective_to"]["description"] == "Blank = still applies."
    for name in ("member", "value", "effective_from", "effective_to"):
        assert fields[name].get("in_list_view") == 1, name
    assert os.path.exists(os.path.join(EPM_DIR, FIXED, "__init__.py"))
    assert os.path.exists(os.path.join(EPM_DIR, FIXED, FIXED + ".py"))


def test_driver_account_child_shape():
    doc = _doc(ACCOUNT)
    assert doc["name"] == "Cost Allocation Driver Account"
    assert doc["doctype"] == "DocType"
    assert doc["istable"] == 1
    assert doc["module"] == "EPM"
    got = [(f["fieldname"], f["fieldtype"], f.get("options"), f.get("reqd", 0)) for f in doc["fields"]]
    assert got == [("main_account", "Link", "Main Account", 1)]
    f = _by_name(ACCOUNT)["main_account"]
    assert f["description"] == "A group account includes its descendants."
    assert f.get("in_list_view") == 1
    assert os.path.exists(os.path.join(EPM_DIR, ACCOUNT, "__init__.py"))
    assert os.path.exists(os.path.join(EPM_DIR, ACCOUNT, ACCOUNT + ".py"))


def test_child_tables_are_not_synced_to_clickhouse_in_this_task():
    """The run snapshots what it used; the driver's tables stay in Frappe."""
    for folder in (FIXED, ACCOUNT, DRIVER):
        with open(os.path.join(EPM_DIR, folder, folder + ".py")) as f:
            src = f.read()
        assert "CH_TABLE" not in src and "sync_doctype" not in src, folder


# -- controller ----------------------------------------------------------------

class Refused(Exception):
    pass


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def as_dict(self):
        return dict(self.__dict__)


def _load(status="Published", in_budget=1, members=("ZZIT", "ZZHR", "ZZP1"), broken_members=False):
    frappe = types.ModuleType("frappe")

    def throw(msg, *a, **k):
        raise Refused(msg)

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.db = types.SimpleNamespace(
        get_value=lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"db.get_value{a}")))
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        def get(self, key, default=None):
            return getattr(self, key, default)

    document.Document = Document
    stubs = {"frappe": frappe, "frappe.model": model, "frappe.model.document": document}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("cad_under_test", DRIVER_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    mod._dimension_status = lambda dim: status
    mod._dimension_in_budget = lambda dim: in_budget
    if broken_members:
        from konsol.cost_allocation_members import MemberReadError

        def broken(dim):
            raise MemberReadError(dim)
        mod._dimension_members = broken
    else:
        mod._dimension_members = lambda dim: set(members)
    return mod


def _driver(mod, **over):
    doc = mod.CostAllocationDriver.__new__(mod.CostAllocationDriver)
    doc.__dict__.update(dict(
        name="ZZ Headcount", driver_name="ZZ Headcount", dimension="ZZDIM",
        source="Uploaded Statistic", carry_forward=0, is_active=1,
        fixed_weights=[], accounts=[], value_sign=None))
    doc.__dict__.update(over)
    return doc


def _fw(member, value, frm, to=None):
    return _Row(member=member, value=value, effective_from=frm, effective_to=to, idx=0)


def _acct(name):
    return _Row(main_account=name, idx=0)


def _refusal(doc):
    try:
        doc.validate()
    except Refused as e:
        return str(e)
    return None


def test_source_constants_match_the_select_options():
    mod = _load()
    assert list(mod.SOURCES) == _by_name(DRIVER)["source"]["options"].split("\n")
    assert [""] + list(mod.VALUE_SIGNS) == _by_name(DRIVER)["value_sign"]["options"].split("\n")


def test_uploaded_statistic_with_empty_tables_is_accepted():
    assert _refusal(_driver(_load())) is None


def test_unpublished_dimension_is_still_refused():
    for status in ("Draft", "Inactive", None):
        msg = _refusal(_driver(_load(status=status)))
        assert msg and "Published" in msg, (status, msg)


def test_fixed_weights_accepts_members_with_disjoint_windows():
    doc = _driver(_load(), source="Fixed Weights", fixed_weights=[
        _fw("ZZIT", 3, "2026-01-01", "2026-06-30"),
        _fw("ZZIT", 4, "2026-07-01"),
        _fw("ZZHR", 0, "2026-01-01"),            # a zero share is allowed
    ])
    assert _refusal(doc) is None


def test_fixed_weights_accepts_date_objects_as_frappe_hands_them():
    import datetime as dt
    doc = _driver(_load(), source="Fixed Weights", fixed_weights=[
        _fw("ZZIT", 3, dt.date(2026, 1, 1), dt.date(2026, 6, 30)),
        _fw("ZZIT", 4, dt.date(2026, 7, 1), None),
    ])
    assert _refusal(doc) is None


def test_fixed_weights_needs_at_least_one_row():
    msg = _refusal(_driver(_load(), source="Fixed Weights"))
    assert msg and "at least one" in msg and "Fixed Weights" in msg


def test_fixed_weights_refuses_a_member_outside_the_dimension_and_names_it():
    doc = _driver(_load(), source="Fixed Weights", fixed_weights=[
        _fw("ZZIT", 1, "2026-01-01"), _fw("ZZNOPE", 1, "2026-01-01")])
    msg = _refusal(doc)
    assert msg and "ZZNOPE" in msg and "ZZDIM" in msg and "member" in msg.lower()
    assert "ZZIT" not in msg.split("member")[1].split("<br>")[0] or "ZZNOPE" in msg


def test_fixed_weights_refuses_a_negative_value():
    doc = _driver(_load(), source="Fixed Weights", fixed_weights=[_fw("ZZIT", -1, "2026-01-01")])
    msg = _refusal(doc)
    assert msg and "negative" in msg and "ZZIT" in msg


def test_fixed_weights_refuses_effective_to_before_effective_from():
    doc = _driver(_load(), source="Fixed Weights", fixed_weights=[
        _fw("ZZIT", 1, "2026-06-01", "2026-01-31")])
    msg = _refusal(doc)
    assert msg and "Effective To" in msg and "ZZIT" in msg


def test_fixed_weights_refuses_a_missing_effective_from():
    doc = _driver(_load(), source="Fixed Weights", fixed_weights=[_fw("ZZIT", 1, None)])
    msg = _refusal(doc)
    assert msg and "Effective From" in msg and "ZZIT" in msg


def test_fixed_weights_refuses_overlapping_windows_of_one_member():
    cases = {
        "open-ended then later": [_fw("ZZIT", 1, "2026-01-01"), _fw("ZZIT", 2, "2026-06-01")],
        "closed then inside": [_fw("ZZIT", 1, "2026-01-01", "2026-12-31"),
                               _fw("ZZIT", 2, "2026-06-01", "2026-06-30")],
        "touching day": [_fw("ZZIT", 1, "2026-01-01", "2026-06-30"),
                         _fw("ZZIT", 2, "2026-06-30")],
        "same from": [_fw("ZZIT", 1, "2026-01-01"), _fw("ZZIT", 1, "2026-01-01", "2026-01-31")],
    }
    for label, rows in cases.items():
        msg = _refusal(_driver(_load(), source="Fixed Weights", fixed_weights=rows))
        assert msg and "overlap" in msg and "ZZIT" in msg, (label, msg)
    # the same windows on two different members are fine
    doc = _driver(_load(), source="Fixed Weights", fixed_weights=[
        _fw("ZZIT", 1, "2026-01-01"), _fw("ZZHR", 2, "2026-01-01")])
    assert _refusal(doc) is None


def test_fixed_weights_rows_for_another_source_are_refused_not_ignored():
    for source in ("Uploaded Statistic", "Account Set"):
        doc = _driver(_load(), source=source, fixed_weights=[_fw("ZZIT", 1, "2026-01-01")],
                      accounts=[_acct("6000")] if source == "Account Set" else [],
                      value_sign="As Booked" if source == "Account Set" else None)
        msg = _refusal(doc)
        assert msg and "Fixed Weights" in msg and source in msg, (source, msg)


def test_fixed_weights_reports_a_failed_member_read_distinctly():
    doc = _driver(_load(broken_members=True), source="Fixed Weights",
                  fixed_weights=[_fw("ZZIT", 1, "2026-01-01")])
    msg = _refusal(doc)
    assert msg and "could not read the members of ZZDIM" in msg
    assert "not members" not in msg


def test_account_set_accepts_accounts_sign_and_in_budget_dimension():
    doc = _driver(_load(in_budget=1), source="Account Set",
                  accounts=[_acct("4000"), _acct("4100")], value_sign="Reversed")
    assert _refusal(doc) is None


def test_account_set_needs_at_least_one_account():
    doc = _driver(_load(), source="Account Set", value_sign="As Booked")
    msg = _refusal(doc)
    assert msg and "at least one" in msg and "account" in msg.lower()


def test_account_set_needs_a_value_sign():
    doc = _driver(_load(), source="Account Set", accounts=[_acct("4000")], value_sign=None)
    msg = _refusal(doc)
    assert msg and "Value Sign" in msg
    doc = _driver(_load(), source="Account Set", accounts=[_acct("4000")], value_sign="Upside Down")
    msg = _refusal(doc)
    assert msg and "Value Sign" in msg


def test_account_set_refuses_a_dimension_not_in_budget_and_says_why():
    doc = _driver(_load(in_budget=0), source="Account Set",
                  accounts=[_acct("4000")], value_sign="As Booked")
    msg = _refusal(doc)
    assert msg and "ZZDIM" in msg and "In Budget" in msg
    assert "trial balance" in msg, "the message says why: the scenario TB carries budget dimensions only"


def test_account_set_refuses_a_duplicate_account():
    doc = _driver(_load(), source="Account Set",
                  accounts=[_acct("4000"), _acct("4000")], value_sign="As Booked")
    msg = _refusal(doc)
    assert msg and "4000" in msg and "duplicate" in msg.lower()


def test_account_rows_or_sign_for_another_source_are_refused_not_ignored():
    for source in ("Uploaded Statistic", "Fixed Weights"):
        fixed = [_fw("ZZIT", 1, "2026-01-01")] if source == "Fixed Weights" else []
        msg = _refusal(_driver(_load(), source=source, accounts=[_acct("4000")], fixed_weights=fixed))
        assert msg and "Account" in msg and source in msg, (source, msg)
        msg = _refusal(_driver(_load(), source=source, value_sign="As Booked", fixed_weights=fixed))
        assert msg and "Value Sign" in msg and source in msg, (source, msg)


def test_in_budget_is_not_read_for_a_non_account_set_driver():
    mod = _load()

    def never(dim):
        raise AssertionError("In Budget read for a source that does not need it")
    mod._dimension_in_budget = never
    assert _refusal(_driver(mod)) is None
    assert _refusal(_driver(mod, source="Fixed Weights",
                            fixed_weights=[_fw("ZZIT", 1, "2026-01-01")])) is None


def test_unknown_source_is_refused_by_name():
    msg = _refusal(_driver(_load(), source="Guesswork"))
    assert msg and "Guesswork" in msg and "Source" in msg
    msg = _refusal(_driver(_load(), source=None))
    assert msg and "Source" in msg


def test_every_problem_is_listed_in_one_throw():
    doc = _driver(_load(status="Draft", in_budget=0), source="Account Set",
                  accounts=[], value_sign=None, fixed_weights=[_fw("ZZIT", 1, "2026-01-01")])
    msg = _refusal(doc)
    parts = msg.split("<br>")
    assert any("Published" in p for p in parts), msg
    assert any("at least one" in p for p in parts), msg
    assert any("Value Sign" in p for p in parts), msg
    assert any("In Budget" in p for p in parts), msg
    assert any("Fixed Weights" in p and "Account Set" in p for p in parts), msg
    assert len(parts) >= 5, msg


def test_no_commit_and_no_save_in_the_controller():
    with open(DRIVER_PY) as f:
        src = f.read()
    assert "db.commit" not in src and ".save(" not in src


# -- patch: existing drivers are stamped Uploaded Statistic -------------------

def test_patch_is_registered_in_patches_txt():
    lines = [l.strip() for l in open(os.path.join(APP_DIR, "patches.txt"))]
    assert PATCH_ID in lines
    assert os.path.exists(PATCH_PY)


def _run_patch(blank_names, has_column=True, table_exists=True):
    """Run the patch against a recording stub; returns the stub."""
    frappe = types.ModuleType("frappe")
    log = types.SimpleNamespace(sql=[], reloads=[])

    class DB:
        def table_exists(self, dt):
            return table_exists

        def has_column(self, dt, col):
            return has_column

        def sql(self, query, values=None, **k):
            log.sql.append((" ".join(query.split()), values))
            return []

    frappe.db = DB()
    frappe.reload_doc = lambda *a: log.reloads.append(a)
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("cad_patch_under_test", PATCH_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.frappe = frappe
        mod.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return log


def test_patch_stamps_blank_source_only_with_one_update():
    log = _run_patch(["ZZ Headcount"])
    updates = [(q, v) for q, v in log.sql if q.lower().startswith("update")]
    assert len(updates) == 1, log.sql
    q, v = updates[0]
    assert "`tabCost Allocation Driver`" in q
    assert "source" in q.lower()
    # only blank rows: a stamped row keeps its source, so a second run writes the same no-op
    assert "is null" in q.lower() and "''" in q
    assert "Uploaded Statistic" in (q + str(v))


def test_patch_adds_the_column_when_the_model_sync_has_not_run_yet():
    """patches.txt has no [post_model_sync] section, so the column may not exist."""
    log = _run_patch([], has_column=False)
    assert log.reloads == [("epm", "doctype", "cost_allocation_driver")], log.reloads
    log = _run_patch([], has_column=True)
    assert log.reloads == []


def test_patch_is_a_no_op_without_the_table():
    log = _run_patch([], table_exists=False)
    assert log.sql == [] and log.reloads == []
