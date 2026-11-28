"""konsol#269 phase 2b-2, tasks R1 and R2: a rule's basis and the rates on the
Rule Set.

Q5 = 5B (Deepak Pai, 13 Nov 2026): a step spreads its sender's pool (Share of
Pool) or charges a declared rate per driver unit (Declared Rate). Decided 27 Nov
2026 (Deepak Pai): Q1 A, rates carry dated windows that must cover the whole
period; Q4 A, rates live on the Rule Set by step; Q6 A, a Declared Rate rule is
refused in Sender's Own Accounts mode, because the charge needs an account to
credit the sender on.

Shape tests read the JSON. The pure rate validation is tested on plain dicts.
Controller tests load the rule set by file path against a stub frappe and stub
its three reads, the pattern of test_cost_allocation_doctypes.py. The patch
runs against a recording db stub.
"""
import datetime
import importlib.util
import json
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EPM_DIR = os.path.join(APP_DIR, "epm", "doctype")
RULE = "cost_allocation_rule"
RULE_SET = "cost_allocation_rule_set"
RATE = "cost_allocation_rule_rate"
RS_PY = os.path.join(EPM_DIR, RULE_SET, RULE_SET + ".py")
RS_JS = os.path.join(EPM_DIR, RULE_SET, RULE_SET + ".js")
PATCH_ID = "konsol.patches.set_cost_allocation_rule_basis"
PATCH_PY = os.path.join(APP_DIR, "patches", "set_cost_allocation_rule_basis.py")

SHARE = "Share of Pool"
DECLARED = "Declared Rate"
OWN = "Sender's Own Accounts"
DECLARED_ACCOUNT = "Declared Allocation Account"
BASIS_HELP = ("Share of Pool splits the sender's whole pool; Declared Rate charges a rate "
              "per driver unit and reports the difference as absorption variance.")
RATE_HELP = "Amount per driver unit, in the entity's trial balance currency."
DECLARED_RATE_EVAL = ("eval:(doc.rules || []).some(function (r) "
                      "{ return r.basis == 'Declared Rate'; })")
# rv L1: the Rates section also shows while rows exist, so a rule switched back
# to Share of Pool does not hide rows that rate_errors still refuses.
RATES_SHOWN_EVAL = DECLARED_RATE_EVAL + " || (doc.rates || []).length > 0"
LAYOUT_TYPES = {"Tab Break", "Section Break", "Column Break"}


def _doc(folder):
    with open(os.path.join(EPM_DIR, folder, folder + ".json")) as f:
        return json.load(f)


def _by_name(folder):
    return {f["fieldname"]: f for f in _doc(folder)["fields"]}


# -- shape: the rule's basis ---------------------------------------------------

def test_rule_basis_is_a_required_select_defaulting_to_share_of_pool():
    f = _by_name(RULE)["basis"]
    assert f["fieldtype"] == "Select"
    assert f["label"] == "Basis"
    assert f["options"] == "Share of Pool\nDeclared Rate"
    assert f["reqd"] == 1
    assert f["default"] == SHARE
    assert f["in_list_view"] == 1
    assert f["description"] == BASIS_HELP


def test_rule_basis_sits_after_the_driver():
    names = [f["fieldname"] for f in _doc(RULE)["fields"]]
    assert names == ["step", "sender_member", "driver", "basis", "description"], names


def test_rule_basis_options_equal_model_constants():
    from konsol import cost_allocation_model as m
    assert _by_name(RULE)["basis"]["options"].split("\n") == list(m.BASES)
    assert m.BASES == (m.SHARE_OF_POOL, m.DECLARED_RATE) == (SHARE, DECLARED)
    assert m.RATE_PLACES == 4


# -- shape: the Rates section on the Rule Set ----------------------------------

def test_rule_set_rates_section_closes_the_rules_tab_and_shows_for_a_declared_rate_or_rows():
    """rv L1: shown for a Declared Rate rule OR while the table has rows. With
    the rule-only condition, switching a rule back to Share of Pool hid the
    rows that rate_errors still refuses ("a rate applies only to a Declared
    Rate step"), and the user could not see what to delete."""
    fields = _doc(RULE_SET)["fields"]
    names = [f["fieldname"] for f in fields]
    rules_tab = names.index("rules_tab")
    assert "rates_section" in names
    section = _by_name(RULE_SET)["rates_section"]
    assert section["fieldtype"] == "Section Break"
    assert section["label"] == "Rates"
    assert section["depends_on"] == RATES_SHOWN_EVAL
    assert _by_name(RULE_SET)["rates"]["depends_on"] == RATES_SHOWN_EVAL
    # last section of the Rules tab: after Receivers, nothing after it
    assert names.index("rates_section") > names.index("receivers") > rules_tab
    assert names[-2:] == ["rates_section", "rates"], names[-3:]


def test_rule_set_rates_table_is_mandatory_only_for_a_declared_rate():
    f = _by_name(RULE_SET)["rates"]
    assert f["fieldtype"] == "Table"
    assert f["label"] == "Rates"
    assert f["options"] == "Cost Allocation Rule Rate"
    assert f["mandatory_depends_on"] == DECLARED_RATE_EVAL   # rows alone never make it mandatory
    assert not f.get("reqd")
    assert "step" in f["description"].lower() and "overlap" in f["description"]


def test_rule_set_form_script_refreshes_the_rates_dependency_on_a_basis_change():
    """A child-row change does not re-evaluate the parent's depends_on
    (form.js watches child tables only to dirty and trigger). The form
    script refreshes the rates field, which re-runs the layout's dependency
    check, when a rule's basis changes and when a rule is removed."""
    assert os.path.exists(RS_JS)
    with open(RS_JS) as f:
        js = f.read()
    assert 'frappe.ui.form.on("Cost Allocation Rule"' in js
    assert re.search(r"^\tbasis\(frm\)", js, re.M), "no basis(frm) handler"
    assert re.search(r"^\trules_remove\(frm\)", js, re.M), "no rules_remove(frm) handler"
    # rv L1: the section also depends on the Rates rows; deleting the last
    # stale row must hide it at once, the same way.
    assert 'frappe.ui.form.on("Cost Allocation Rule Set"' in js
    assert re.search(r"^\trates_remove\(frm\)", js, re.M), "no rates_remove(frm) handler"
    assert js.count('frm.refresh_field("rates")') >= 3


# -- shape: the Rule Rate child ------------------------------------------------

def test_rule_rate_child_shape():
    doc = _doc(RATE)
    assert doc["name"] == "Cost Allocation Rule Rate"
    assert doc["doctype"] == "DocType"
    assert doc["istable"] == 1
    assert doc["editable_grid"] == 1
    assert doc["module"] == "EPM"
    assert doc["engine"] == "InnoDB"
    got = [(f["fieldname"], f["fieldtype"], f.get("reqd", 0)) for f in doc["fields"]]
    assert got == [
        ("step", "Int", 1),
        ("rate", "Float", 1),
        ("effective_from", "Date", 1),
        ("effective_to", "Date", 0),
    ]
    fields = _by_name(RATE)
    assert fields["rate"]["description"] == RATE_HELP
    # rv M1: without a precision the Desk rounds a Float to the site's
    # float_precision (ControlFloat.parse, often 3): 12.3456 saved as 12.346
    # and the 4-place check never saw it. Run Weight.rate has the same "4".
    assert fields["rate"]["precision"] == "4"
    assert fields["effective_to"]["description"] == "Blank = still applies."
    for name in ("step", "rate", "effective_from", "effective_to"):
        assert fields[name].get("in_list_view") == 1, name
        assert fields[name]["label"] == name.replace("_", " ").title(), name
    assert os.path.exists(os.path.join(EPM_DIR, RATE, "__init__.py"))
    assert os.path.exists(os.path.join(EPM_DIR, RATE, RATE + ".py"))


def test_rate_child_is_not_synced_to_clickhouse_in_this_task():
    """A run snapshots the rate it used (R3); the Rule Set's table stays in Frappe."""
    for folder in (RATE, RULE, RULE_SET):
        with open(os.path.join(EPM_DIR, folder, folder + ".py")) as f:
            src = f.read()
        assert "CH_TABLE" not in src and "sync_doctype" not in src, folder


# -- pure validation -----------------------------------------------------------

def _rule(step, basis=DECLARED, sender=None):
    return {"step": step, "sender_member": sender or f"S{step}", "driver": "ZZ Head", "basis": basis}


def _rate(step, rate=50.0, frm="2026-01-01", to=None):
    return {"step": step, "rate": rate, "effective_from": frm, "effective_to": to}


def _errors(rules, rates, mode=DECLARED_ACCOUNT):
    from konsol import cost_allocation_model as m
    return m.rate_errors(rules, rates, mode)


def test_rate_errors_accepts_a_declared_rate_step_with_one_rate_in_declared_mode():
    assert _errors([_rule(1)], [_rate(1)]) == []
    # a share step beside it needs no rate; two windows that touch day-to-day are fine
    assert _errors([_rule(1), _rule(2, SHARE)],
                   [_rate(1, to="2026-06-30"), _rate(1, 60, "2026-07-01")]) == []


def test_rate_errors_missing_basis_is_share_of_pool_the_field_default():
    rule = {"step": 1, "sender_member": "S1", "driver": "ZZ Head"}
    assert _errors([rule], [], OWN) == []
    (msg,) = _errors([rule], [_rate(1)], OWN)
    assert "Share of Pool" in msg and "step 1" in msg


def test_rate_errors_refuses_an_unknown_basis_naming_the_step():
    (msg,) = _errors([_rule(1, "Per Head")], [_rate(1)])
    assert msg == "Step 1 (S1): Basis must be one of Share of Pool, Declared Rate, not 'Per Head'."


def test_rate_errors_declared_rate_step_needs_a_rate_row():
    (msg,) = _errors([_rule(1, sender="IT")], [])
    assert msg == "Step 1 (IT) is Declared Rate but has no rate; add at least one Rates row for the step."


def test_rate_errors_refuses_a_rate_for_a_share_of_pool_step_or_a_step_without_a_rule():
    errs = _errors([_rule(1, SHARE, "IT")], [_rate(1), _rate(7)])
    assert errs == [
        "Rates row for step 1 (2026-01-01–open): step 1 (IT) is Share of Pool; "
        "a rate applies only to a Declared Rate step.",
        "Rates row for step 7 (2026-01-01–open): step 7 has no rule.",
    ]


def test_rate_errors_rate_must_be_positive():
    errs = _errors([_rule(1)], [_rate(1, 0, to="2026-12-31"), _rate(1, -3.5, "2027-01-01")])
    assert errs == [
        "Rates row for step 1 (2026-01-01–2026-12-31): Rate must be greater than 0, not 0.",
        "Rates row for step 1 (2027-01-01–open): Rate must be greater than 0, not -3.5.",
    ]


def test_rate_errors_rate_keeps_at_most_four_decimal_places():
    assert _errors([_rule(1)], [_rate(1, 12.3456)]) == []
    assert _errors([_rule(1)], [_rate(1, "12.3456")]) == []
    (msg,) = _errors([_rule(1)], [_rate(1, 12.34567)])
    assert msg == "Rates row for step 1 (2026-01-01–open): Rate 12.34567 has more than 4 decimal places."
    # a float that is a whole number or a short decimal passes whatever repr it carries
    assert _errors([_rule(1)], [_rate(1, 50, to="2026-12-31"), _rate(1, 0.1, "2027-01-01")]) == []


def test_rate_errors_effective_from_is_required_and_effective_to_may_not_precede_it():
    (msg,) = _errors([_rule(1)], [_rate(1, frm=None)])
    assert msg == "Rates row for step 1 (?–open): Effective From is required."
    (msg,) = _errors([_rule(1)], [_rate(1, frm="2026-03-01", to="2026-02-28")])
    assert msg == ("Rates row for step 1 (2026-03-01–2026-02-28): "
                   "Effective To is before Effective From.")
    assert _errors([_rule(1)], [_rate(1, frm="2026-03-01", to="2026-03-01")]) == []


def test_rate_errors_windows_of_one_step_may_not_overlap():
    # an open window swallows a later one; a closed window ending on the next start overlaps
    (msg,) = _errors([_rule(1)], [_rate(1), _rate(1, 60, "2026-07-01")])
    assert msg == "Step 1: overlapping rate windows 2026-01-01–open and 2026-07-01–open."
    (msg,) = _errors([_rule(1)], [_rate(1, to="2026-07-01"), _rate(1, 60, "2026-07-01")])
    assert msg == "Step 1: overlapping rate windows 2026-01-01–2026-07-01 and 2026-07-01–open."
    # windows of different steps are independent
    assert _errors([_rule(1), _rule(2)], [_rate(1), _rate(2)]) == []


def test_rate_errors_accepts_dates_as_date_datetime_or_iso_string():
    d = datetime.date(2026, 7, 1)
    dt = datetime.datetime(2026, 7, 1, 9, 30)
    assert _errors([_rule(1)], [_rate(1, to=datetime.date(2026, 6, 30)), _rate(1, 60, d)]) == []
    (msg,) = _errors([_rule(1)], [_rate(1), _rate(1, 60, dt)])
    assert "overlapping" in msg


def test_rate_errors_refuses_a_declared_rate_rule_in_own_accounts_mode_saying_why():
    (msg,) = _errors([_rule(1, sender="IT")], [_rate(1)], OWN)
    assert msg == (
        "Step 1 (IT) is Declared Rate, but receiver accounts are Sender's Own Accounts; "
        "the charge needs an account to credit the sender on. "
        "Set Receiver Account Mode to Declared Allocation Account.")
    assert _errors([_rule(1, SHARE)], [], OWN) == []


def test_rate_errors_lists_every_problem_together():
    errs = _errors([_rule(1, sender="IT"), _rule(2, sender="HR")],
                   [_rate(2, 0), _rate(2, 50.00001, "2026-01-01"), _rate(3)], OWN)
    joined = "\n".join(errs)
    assert len(errs) == 7, joined
    for needle in ("Step 1 (IT) is Declared Rate, but", "Step 2 (HR) is Declared Rate, but",
                   "Step 1 (IT) is Declared Rate but has no rate", "greater than 0",
                   "more than 4 decimal places", "step 3 has no rule", "overlapping rate windows"):
        assert needle in joined, needle


# -- controller ----------------------------------------------------------------

class _Refused(Exception):
    pass


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def as_dict(self):
        return dict(self.__dict__)


class _ThrowingFrappe:
    def throw(self, message, *args, **kwargs):
        raise _Refused(message)


def _install_document_stub():
    if "frappe.model.document" in sys.modules:
        return

    class Document:
        pass
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = Document
    sys.modules.setdefault("frappe", types.ModuleType("frappe"))
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document


def _rs_module(members=("IT", "HR", "Sales", "Ops")):
    _install_document_stub()
    spec = importlib.util.spec_from_file_location("cost_allocation_rule_set_r2", RS_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.frappe = _ThrowingFrappe()
    module._dimension_state = lambda dim: ("Published", 1)
    module._dimension_members = lambda dim: set(members)
    module._driver_dimensions = lambda names: {n: "dim_zz" for n in names}
    return module


def _rs_doc(module, **over):
    doc = module.CostAllocationRuleSet.__new__(module.CostAllocationRuleSet)
    doc.entity = "ZZE"
    doc.allocation_dimension = "dim_zz"
    doc.method = "Step-Down"
    doc.receiver_account_mode = DECLARED_ACCOUNT
    doc.allocation_account = "ZZ-ALLOC"
    doc.rules = [_Row(step=1, sender_member="IT", driver="ZZ Head", basis=DECLARED),
                 _Row(step=2, sender_member="HR", driver="ZZ Head", basis=SHARE)]
    doc.receivers = [_Row(step=1, receiver_member="HR"), _Row(step=1, receiver_member="Sales"),
                     _Row(step=2, receiver_member="Sales"), _Row(step=2, receiver_member="Ops")]
    doc.rates = [_Row(step=1, rate=50.0, effective_from="2026-01-01", effective_to=None)]
    for k, v in over.items():
        setattr(doc, k, v)
    return doc


def _refusal(doc):
    try:
        doc.validate()
    except _Refused as exc:
        return str(exc)
    return None


def test_rule_set_saves_a_declared_rate_step_with_a_rate_in_declared_account_mode():
    assert _refusal(_rs_doc(_rs_module())) is None


def test_rule_set_refuses_a_declared_rate_step_in_own_accounts_mode_naming_the_step():
    msg = _refusal(_rs_doc(_rs_module(), receiver_account_mode=OWN, allocation_account=""))
    assert msg and "Step 1 (IT) is Declared Rate, but receiver accounts are Sender's Own Accounts" in msg
    assert "account to credit" in msg


def test_rule_set_refuses_a_declared_rate_step_without_a_rate_row():
    msg = _refusal(_rs_doc(_rs_module(), rates=[]))
    assert msg == "Step 1 (IT) is Declared Rate but has no rate; add at least one Rates row for the step."


def test_rule_set_refuses_a_rate_row_on_a_share_of_pool_step():
    doc = _rs_doc(_rs_module())
    doc.rates.append(_Row(step=2, rate=10.0, effective_from="2026-01-01", effective_to=None))
    msg = _refusal(doc)
    assert msg and "step 2 (HR) is Share of Pool" in msg


def test_rule_set_lists_model_and_rate_problems_in_one_throw():
    doc = _rs_doc(_rs_module(), receivers=[_Row(step=1, receiver_member="HR")],
                  rates=[_Row(step=1, rate=0, effective_from="2026-01-01", effective_to=None)])
    msg = _refusal(doc)
    assert msg and "Step 2 has no receiver." in msg and "greater than 0" in msg
    assert msg.count("<br>") == 1, msg


def test_rule_set_allows_a_declared_rate_step_under_reciprocal_at_save():
    """R4 computes it: a rate sender leaves the linear system and acts as an exit."""
    m = _rs_module()
    doc = _rs_doc(m, method="Reciprocal",
                  receivers=[_Row(step=1, receiver_member="HR"), _Row(step=1, receiver_member="Sales"),
                             _Row(step=2, receiver_member="IT"), _Row(step=2, receiver_member="Sales")])
    assert _refusal(doc) is None


def test_rule_set_reciprocal_saves_when_a_share_sender_exits_only_through_a_rate_sender():
    """R4: a Declared Rate sender is an exit for the closed-loop check at save,
    as it is at Compute (model.reciprocal_coefficients). IT (rate) charges only
    HR; HR (share) sends everything to IT. Treating IT as a share sender would
    call both stuck; IT's rate fixes its outflow, so HR's cost does leave."""
    m = _rs_module()
    doc = _rs_doc(m, method="Reciprocal",
                  receivers=[_Row(step=1, receiver_member="HR"), _Row(step=2, receiver_member="IT")])
    assert _refusal(doc) is None


def test_rule_set_reciprocal_saves_two_rate_senders_charging_each_other():
    """Two Declared Rate senders whose only receivers are each other: no share
    sender is in the linear system, so nothing is stuck."""
    m = _rs_module()
    doc = _rs_doc(m, method="Reciprocal",
                  rules=[_Row(step=1, sender_member="IT", driver="ZZ Head", basis=DECLARED),
                         _Row(step=2, sender_member="HR", driver="ZZ Head", basis=DECLARED)],
                  receivers=[_Row(step=1, receiver_member="HR"), _Row(step=2, receiver_member="IT")],
                  rates=[_Row(step=1, rate=50.0, effective_from="2026-01-01", effective_to=None),
                         _Row(step=2, rate=20.0, effective_from="2026-01-01", effective_to=None)])
    assert _refusal(doc) is None


def test_rule_set_reciprocal_still_refuses_a_share_only_loop_beside_a_rate_sender():
    """HR <-> Ops (both Share of Pool) never exit; IT (rate) charges Sales and is
    not in the loop. The refusal names the stuck senders only."""
    m = _rs_module()
    doc = _rs_doc(m, method="Reciprocal",
                  rules=[_Row(step=1, sender_member="IT", driver="ZZ Head", basis=DECLARED),
                         _Row(step=2, sender_member="HR", driver="ZZ Head", basis=SHARE),
                         _Row(step=3, sender_member="Ops", driver="ZZ Head", basis=SHARE)],
                  receivers=[_Row(step=1, receiver_member="Sales"),
                             _Row(step=2, receiver_member="Ops"), _Row(step=3, receiver_member="HR")])
    msg = _refusal(doc)
    assert msg and "never reach a final receiver" in msg
    assert "HR" in msg and "Ops" in msg and "IT" not in msg.split("from:")[1]


def test_rule_set_reciprocal_rows_without_a_basis_pass_the_closed_loop_check_as_share_of_pool():
    """Rows written before 2b-2 carry basis None; under Reciprocal the closed-loop
    check must read them as Share of Pool, not refuse an unknown basis."""
    m = _rs_module()
    doc = _rs_doc(m, method="Reciprocal",
                  rules=[_Row(step=1, sender_member="IT", driver="ZZ Head", basis=None),
                         _Row(step=2, sender_member="HR", driver="ZZ Head")],
                  receivers=[_Row(step=1, receiver_member="HR"), _Row(step=1, receiver_member="Sales"),
                             _Row(step=2, receiver_member="IT"), _Row(step=2, receiver_member="Sales")],
                  rates=[], receiver_account_mode=OWN, allocation_account="")
    assert _refusal(doc) is None


def test_rule_set_rows_without_a_basis_still_validate_as_share_of_pool():
    """Rows built before 2b-2 (and the K4 tests) carry no basis: the field default applies."""
    m = _rs_module()
    doc = _rs_doc(m, rules=[_Row(step=1, sender_member="IT", driver="ZZ Head")],
                  receivers=[_Row(step=1, receiver_member="HR")], rates=[],
                  receiver_account_mode=OWN, allocation_account="")
    assert _refusal(doc) is None
    doc = _rs_doc(m, rules=[_Row(step=1, sender_member="IT", driver="ZZ Head")],
                  receivers=[_Row(step=1, receiver_member="HR")],
                  receiver_account_mode=OWN, allocation_account="")
    del doc.rates
    assert _refusal(doc) is None


# -- patch: existing rules are Share of Pool -----------------------------------

def test_patch_is_registered_in_patches_txt():
    lines = [l.strip() for l in open(os.path.join(APP_DIR, "patches.txt"))]
    assert PATCH_ID in lines
    assert os.path.exists(PATCH_PY)


def _run_patch(has_column=True, table_exists=True):
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
        spec = importlib.util.spec_from_file_location("car_basis_patch_under_test", PATCH_PY)
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


def test_patch_stamps_blank_basis_only_with_one_update():
    log = _run_patch()
    updates = [(q, v) for q, v in log.sql if q.lower().startswith("update")]
    assert len(updates) == 1, log.sql
    q, v = updates[0]
    assert "`tabCost Allocation Rule`" in q
    assert "`basis`" in q
    assert "is null" in q.lower() and "''" in q
    assert SHARE in (q + str(v))


def test_patch_adds_the_column_when_the_model_sync_has_not_run_yet():
    log = _run_patch(has_column=False)
    assert log.reloads == [("epm", "doctype", "cost_allocation_rule")], log.reloads
    assert any(q.lower().startswith("update") for q, _v in log.sql)
    log = _run_patch(has_column=True)
    assert log.reloads == []


def test_patch_is_a_no_op_without_the_table():
    log = _run_patch(table_exists=False)
    assert log.sql == [] and log.reloads == []


# -- R5: the rate window for a run period (pure) ----------------------------------
#
# Q1 A (Deepak Pai, 27 Nov 2026): a window must cover the whole period; a window
# that starts or ends inside the period is a change inside the period and
# refuses even when another window covers; blending by days was rejected. The
# same rule as fixed_windows, for one step's Rates rows.

MARCH = (datetime.date(2026, 3, 1), datetime.date(2026, 3, 31))


def _pick(rates, step=1, start=MARCH[0], end=MARCH[1]):
    from konsol import cost_allocation_model as m
    return m.rate_for_period(rates, step, start, end)


def _refusal_of(fn):
    from konsol import cost_allocation_model as m
    try:
        fn()
    except m.CostAllocationError as exc:
        return exc.errors
    raise AssertionError("expected a CostAllocationError")


def test_rate_for_period_picks_the_one_window_covering_the_whole_period():
    rates = [_rate(1, 40, "2025-01-01", "2025-12-31"),      # expired
             _rate(1, 50, "2026-01-01"),                    # open-ended: covers March
             _rate(2, 99, "2026-01-01")]                    # another step: ignored here
    got = _pick(rates)
    assert got["rate"] == 50
    assert got["effective_from"] == datetime.date(2026, 1, 1)
    assert got["effective_to"] is None
    assert _pick(rates, step=2)["rate"] == 99


def test_rate_for_period_a_window_ending_on_the_last_day_covers():
    got = _pick([_rate(1, 50, "2024-06-01", "2026-03-31")])
    assert (got["rate"], got["effective_to"]) == (50, datetime.date(2026, 3, 31))


def test_rate_for_period_accepts_dates_as_date_datetime_or_iso_string():
    got = _pick([_rate(1, 50, datetime.datetime(2026, 1, 1, 0, 0), "2026-12-31 00:00:00")],
                start=datetime.datetime(2026, 3, 1, 0, 0), end="2026-03-31")
    assert got["effective_from"] == datetime.date(2026, 1, 1)
    assert got["effective_to"] == datetime.date(2026, 12, 31)


def test_rate_for_period_keeps_the_rate_as_given():
    assert _pick([_rate(1, 12.3456)])["rate"] == 12.3456
    assert _pick([_rate(1, "0.5")])["rate"] == "0.5"


def test_rate_for_period_refuses_a_step_with_no_covering_window():
    errs = _refusal_of(lambda: _pick([_rate(1, 50, "2026-04-01")]))
    assert errs == ["Step 1: no declared rate covers the period 2026-03-01 to 2026-03-31."]
    errs = _refusal_of(lambda: _pick([_rate(2)]))
    assert errs == ["Step 1: no declared rate covers the period 2026-03-01 to 2026-03-31."]


def test_rate_for_period_refuses_a_window_that_starts_inside_the_period():
    errs = _refusal_of(lambda: _pick([_rate(1, 50, "2026-01-01", "2026-03-14"), _rate(1, 60, "2026-03-15")]))
    assert len(errs) == 1, errs
    assert errs[0].startswith("Step 1: the declared rate changes inside the period 2026-03-01 to 2026-03-31"), errs
    assert "2026-03-14" in errs[0] and "2026-03-15" in errs[0], errs
    assert "cover the whole period" in errs[0], errs


def test_rate_for_period_refuses_a_window_ending_inside_even_when_another_covers():
    errs = _refusal_of(lambda: _pick([_rate(1, 50, "2026-01-01"), _rate(1, 60, "2025-01-01", "2026-03-10")]))
    assert len(errs) == 1 and "inside the period" in errs[0] and "2026-03-10" in errs[0], errs


def test_rate_for_period_refuses_two_covering_windows():
    errs = _refusal_of(lambda: _pick([_rate(1, 50, "2026-01-01"), _rate(1, 60, "2025-06-01")]))
    assert len(errs) == 1 and "more than one rate window covers" in errs[0], errs
    assert "2025-06-01" in errs[0] and "2026-01-01" in errs[0], errs


def test_rate_for_period_refuses_a_period_that_ends_before_it_starts():
    errs = _refusal_of(lambda: _pick([_rate(1)], start=MARCH[1], end=MARCH[0]))
    assert errs == ["Period start 2026-03-31 is after its end 2026-03-01."]
