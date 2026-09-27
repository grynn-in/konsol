"""Consolidation Journal's lifecycle hooks, run against a stub frappe (konsol#305 J03).

The journal's lifecycle is Consolidation Adjustment's (proven in
test_consolidation_adjustment_lifecycle.py, #134), minus the warehouse sync,
which J05 adds. These run the hooks, so inverted logic fails here and not only
in a live walk."""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CJ = os.path.join(APP_DIR, "consolidation", "doctype", "consolidation_journal", "consolidation_journal.py")
JOURNAL_MODEL = os.path.join(APP_DIR, "close", "journal_model.py")
WF = [("Draft", 0), ("Pending Approval", 0), ("Approved", 1), ("Reversed", 2)]

#: The default fixture _load()/_doc() give every test unless overridden
#: (konsol#305 J04): a declared group root, two entities in it, and one
#: postable account, so a plain validate() call passes by default and only
#: the tests that mean to break one of these override it.
DEFAULT_ROOT = ("ZZROOT", "USD")             # (root docname, reporting_currency)
DEFAULT_GROUP = "ZZGROUP"
DEFAULT_NODES = {("ZZGROUP", "ZZE1"), ("ZZGROUP", "ZZE2")}
DEFAULT_ACCOUNTS = {
    "4000": {"is_group": 0, "status": "Published"},
    "4100": {"is_group": 1, "status": "Published"},   # a group heading
    "4200": {"is_group": 0, "status": "Draft"},        # not yet published
}


class Refused(Exception):
    pass


def _line(idx, data_area_id="ZZE1", main_account="4000", debit_amount=0, credit_amount=0):
    return types.SimpleNamespace(idx=idx, data_area_id=data_area_id, main_account=main_account,
                                  debit_amount=debit_amount, credit_amount=credit_amount)


#: Two lines that balance, name entities in DEFAULT_NODES and a postable account.
DEFAULT_LINES = [
    _line(1, data_area_id="ZZE1", debit_amount=100),
    _line(2, data_area_id="ZZE1", credit_amount=100),
]


def _get_value_stub(root, nodes, accounts):
    """A minimal frappe.db.get_value fake covering the three shapes J04's
    validate() calls it with: the group root (filters dict, blank
    data_area_id), a line's entity node (filters dict), and a line's Main
    Account (filters is the docname)."""
    def get_value(doctype, filters=None, fieldname=None, as_dict=False):
        if doctype == "Consolidation Group":
            group = filters.get("consolidation_group")
            data_area = filters.get("data_area_id")
            if data_area == ["is", "not set"]:
                if root is None:
                    return None
                return types.SimpleNamespace(name=root[0], reporting_currency=root[1])
            if (group, data_area) in nodes:
                return f"{group}::{data_area}"
            return None
        if doctype == "Main Account":
            account = accounts.get(filters)
            return types.SimpleNamespace(**account) if account is not None else None
        raise AssertionError(f"unexpected get_value({doctype!r}, {filters!r})")
    return get_value


def _period_row(fiscal_year, fiscal_period, period_type="Regular", status="Open"):
    """A fiscal_calendar.fiscal_period_rows() row (konsol#305 J04a)."""
    return {
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "period_code": f"P{fiscal_period}",
        "period_label": f"P{fiscal_period}",
        "period_type": period_type,
        "start_date": None,
        "end_date": None,
        "quarter": "",
        "status": status,
    }


#: The journal's own period (from _doc's defaults) is FY2024 P12; a later
#: Regular period, Open and Closed, for the reversal-check tests.
DEFAULT_PERIOD_ROWS = [
    _period_row(2024, 12, "Regular", "Open"),
    _period_row(2025, 1, "Regular", "Open"),
    _period_row(2025, 2, "Regular", "Closed"),
]

#: before_submit imports konsol.fiscal_calendar lazily (it needs a live site;
#: mirrors close_settings.py's own lazy import), so its stub must outlive one
#: _load() call — the test may call before_submit only after _load() has
#: already restored sys.modules. Registered once, permanently, like
#: test_close_settings.py's own module-scope `_stub`; run-host-tests.py's
#: per-file isolation drops it once this file's tests are done.
_PERIOD_ROWS_NOW = [DEFAULT_PERIOD_ROWS]
if "konsol.fiscal_calendar" not in sys.modules:
    _fiscal_calendar_stub = types.ModuleType("konsol.fiscal_calendar")
    _fiscal_calendar_stub.fiscal_period_rows = lambda: list(_PERIOD_ROWS_NOW[0])
    sys.modules["konsol.fiscal_calendar"] = _fiscal_calendar_stub


def _load(states=None, period_open=True, declared=True,
          root=DEFAULT_ROOT, nodes=DEFAULT_NODES, accounts=DEFAULT_ACCOUNTS,
          period_rows=DEFAULT_PERIOD_ROWS):
    """Import the controller with frappe stubbed. ``states`` is the active
    workflow's [(state, doc_status)], or None for no workflow."""
    _PERIOD_ROWS_NOW[0] = period_rows
    checked = []

    def assert_declared(fiscal_year, fiscal_period):
        if not declared:
            raise Refused(f"FY{fiscal_year} P{fiscal_period} not declared")
    wf = types.SimpleNamespace(states=[types.SimpleNamespace(state=s, doc_status=str(d)) for s, d in states]) if states else None

    def throw(msg, *args, **kwargs):
        raise Refused(msg)

    def assert_open(fiscal_year, fiscal_period, action="run"):
        checked.append((fiscal_year, fiscal_period))
        if not period_open:
            raise Refused("period closed")

    class Document:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def get(self, field, default=None):
            return getattr(self, field, default)

    mods = {name: types.ModuleType(name) for name in (
        "frappe", "frappe.model", "frappe.model.document", "frappe.model.workflow", "frappe.utils",
        "konsol", "konsol.clickhouse", "konsol.period_status")}
    frappe = mods["frappe"]
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.session = types.SimpleNamespace(user="approver@example.com")
    frappe.get_cached_doc = lambda doctype, name: wf
    frappe.db = types.SimpleNamespace(
        after_commit=types.SimpleNamespace(add=lambda fn: None, _functions=[]),
        get_value=_get_value_stub(root, nodes, accounts),
    )
    mods["frappe.model.document"].Document = Document
    mods["frappe.model.workflow"].get_workflow_name = lambda doctype: "CJ Workflow" if wf else None
    mods["frappe.utils"].cint = lambda v: int(v or 0)
    mods["frappe.utils"].now_datetime = lambda: "NOW"
    mods["konsol.clickhouse"].sync_doctype = lambda *a: None
    mods["konsol.period_status"].assert_open = assert_open
    mods["konsol.period_status"].assert_declared = assert_declared

    # journal_model.py is pure (J01) and loaded by path, exactly like
    # test_close_journal_model.py, then wired in as konsol.close.journal_model
    # so the controller's `from konsol.close import journal_model` resolves
    # against the real rules rather than a second stub of them.
    jm_spec = importlib.util.spec_from_file_location("konsol.close.journal_model", JOURNAL_MODEL)
    journal_model = importlib.util.module_from_spec(jm_spec)
    jm_spec.loader.exec_module(journal_model)
    close_pkg = types.ModuleType("konsol.close")
    close_pkg.journal_model = journal_model
    mods["konsol.close"] = close_pkg
    mods["konsol.close.journal_model"] = journal_model

    saved = {name: sys.modules.get(name) for name in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("cj_under_test", CJ)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    return module, checked


def _doc(module, **fields):
    base = dict(doctype="Consolidation Journal", status="Draft", docstatus=0, fiscal_year=2024,
                fiscal_period=12, approved_by=None, approved_at=None,
                consolidation_group=DEFAULT_GROUP, lines=list(DEFAULT_LINES),
                reverse_fiscal_year=0, reverse_fiscal_period=0)
    return module.ConsolidationJournal(**dict(base, **fields))


def _refused(fn):
    try:
        fn()
    except Refused:
        return True
    return False


def test_without_a_workflow_submit_approves_and_stamps_the_approver():
    module, _ = _load()
    d = _doc(module, status="Pending Approval", approved_by="someone-else", approved_at="then")
    d.before_submit()
    assert (d.status, d.approved_by, d.approved_at) == ("Approved", "approver@example.com", "NOW")


def test_under_a_workflow_a_direct_submit_is_refused_and_the_workflows_approve_passes():
    module, _ = _load(WF)
    for status in ("Draft", "Pending Approval"):
        assert _refused(_doc(module, status=status).before_submit), status
    d = _doc(module, status="Approved")   # apply_workflow sets the state, then submits
    d.before_submit()
    assert (d.status, d.approved_by) == ("Approved", "approver@example.com")


def test_reverse_is_refused_in_a_closed_period_and_changes_nothing():
    for states in (None, WF):
        module, checked = _load(states, period_open=False)
        d = _doc(module, status="Reversed" if states else "Approved", docstatus=1)
        before = d.status
        assert _refused(d.before_cancel) and d.status == before and checked == [(2024, 12)]


def test_reverse_in_an_open_period():
    module, _ = _load()
    d = _doc(module, status="Approved", docstatus=1)
    d.before_cancel()
    assert d.status == "Reversed"
    module, _ = _load(WF)
    assert _refused(_doc(module, status="Approved", docstatus=1).before_cancel)   # a direct cancel
    d = _doc(module, status="Reversed", docstatus=1)                               # the workflow's Reverse
    d.before_cancel()
    assert d.status == "Reversed"


def test_a_draft_cannot_be_saved_into_a_submitted_state():
    for states in (None, WF):
        module, _ = _load(states)
        for status in ("Approved", "Reversed"):
            assert _refused(_doc(module, status=status, docstatus=0).validate), (states, status)
        for status in ("Draft", "Pending Approval"):
            _doc(module, status=status, docstatus=0).validate()
        _doc(module, status="Approved", docstatus=1).validate()


def test_an_undeclared_period_is_refused_on_save():
    for states in (None, WF):
        module, _ = _load(states, declared=False)
        assert _refused(_doc(module, status="Draft", docstatus=0).validate), states


def test_every_new_journal_starts_in_the_first_state_with_no_approver():
    for states, first in ((None, "Draft"), (WF, "Draft"), ([("Entwurf", 0), ("Genehmigt", 1)], "Entwurf")):
        module, _ = _load(states)
        for status in ("Draft", "Pending Approval", "Approved", "Reversed", ""):
            d = _doc(module, status=status, approved_by="someone", approved_at="then")
            d.before_insert()
            assert (d.status, d.approved_by, d.approved_at) == (first, None, None), (states, status)


def test_the_period_gates_and_refusals_name_the_journal():
    module, _ = _load()
    actions = []
    module.assert_open = lambda fy, fp, action="run": actions.append(action)
    _doc(module, status="Draft").before_submit()
    _doc(module, status="Approved", docstatus=1).before_cancel()
    assert actions == ["approve a consolidation journal", "reverse a consolidation journal"], actions

    module, _ = _load(WF)
    for fn in (_doc(module, status="Draft").before_submit,
               _doc(module, status="Approved", docstatus=1).before_cancel,
               _doc(module, status="Approved", docstatus=0).validate):
        try:
            fn()
        except Refused as e:
            assert "journal" in str(e) and "adjustment" not in str(e), str(e)
        else:
            raise AssertionError(f"{fn.__name__} was not refused")


# --- J04: lines, totals, the group, each line's entity, accounts, currency --

def test_an_unbalanced_journal_is_refused_naming_both_totals():
    module, _ = _load()
    lines = [_line(1, debit_amount=100), _line(2, credit_amount=99.99)]
    d = _doc(module, lines=lines)
    try:
        d.validate()
    except Refused as e:
        assert "100" in str(e) and "99.99" in str(e), str(e)
    else:
        raise AssertionError("an unbalanced journal was not refused")


def test_a_line_with_both_amounts_is_refused():
    module, _ = _load()
    lines = [_line(1, debit_amount=100, credit_amount=50), _line(2, credit_amount=100)]
    assert _refused(_doc(module, lines=lines).validate)


def test_no_group_root_is_refused():
    module, _ = _load(root=None)
    assert _refused(_doc(module).validate)


def test_a_line_entity_not_in_the_group_is_refused_naming_the_line():
    module, _ = _load()
    lines = [_line(1, data_area_id="ZZE1", debit_amount=100),
              _line(2, data_area_id="ZZNOTIN", credit_amount=100)]
    d = _doc(module, lines=lines)
    try:
        d.validate()
    except Refused as e:
        assert "2" in str(e) and "ZZNOTIN" in str(e), str(e)
    else:
        raise AssertionError("a line whose entity is not in the group was not refused")


def test_a_cross_entity_reclass_saves():
    module, _ = _load()
    lines = [_line(1, data_area_id="ZZE1", debit_amount=100),
              _line(2, data_area_id="ZZE2", credit_amount=100)]
    d = _doc(module, lines=lines)
    d.validate()
    assert (d.total_debit, d.total_credit, d.currency) == (100.0, 100.0, "USD")


def test_a_group_account_is_refused():
    module, _ = _load()
    lines = [_line(1, main_account="4100", debit_amount=100), _line(2, credit_amount=100)]
    assert _refused(_doc(module, lines=lines).validate)


def test_a_draft_account_is_refused():
    module, _ = _load()
    lines = [_line(1, main_account="4200", debit_amount=100), _line(2, credit_amount=100)]
    assert _refused(_doc(module, lines=lines).validate)


def test_a_valid_journal_sets_totals_and_currency():
    module, _ = _load()
    d = _doc(module)
    d.validate()
    assert (d.total_debit, d.total_credit, d.currency) == (100.0, 100.0, "USD")


# --- J04a: the journal names its reversal period; approval checks it ------

def test_a_save_naming_only_the_reversal_year_is_refused():
    module, _ = _load()
    assert _refused(_doc(module, reverse_fiscal_year=2025).validate)


def test_a_submit_naming_a_closed_period_is_refused_and_docstatus_stays_0():
    module, _ = _load()
    d = _doc(module, status="Pending Approval",
              reverse_fiscal_year=2025, reverse_fiscal_period=2)
    assert _refused(d.before_submit)
    assert d.docstatus == 0


def test_a_submit_naming_a_later_open_regular_period_submits():
    module, _ = _load()
    d = _doc(module, status="Pending Approval",
              reverse_fiscal_year=2025, reverse_fiscal_period=1)
    d.before_submit()
    assert d.status == "Approved"
