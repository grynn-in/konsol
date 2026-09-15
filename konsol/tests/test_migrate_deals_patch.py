"""The migrate_deals_to_business_combinations patch (konsolidat#198), host-run.

Before the deal documents existed, an acquisition lived as figures on the
Ownership Period it started (``acquisition_price``, ``fair_value_adjustment``)
and a disposal as ``is_disposal`` / ``disposal_price`` on the period it
ended. Those fields are now read-only, set by a Business Combination or a
Business Disposal. The patch turns every submitted period carrying such a
figure into a DRAFT deal document linked to it, for a reviewer to complete
and submit; nothing it inserts is approved. It reloads the deal doctypes
(children first) and Ownership Period before it queries anything, because
patches.txt has no sections and every patch runs pre_model_sync. A stub
frappe records every call in order.
"""
import contextlib
import importlib.util
import io
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "migrate_deals_to_business_combinations.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
MODULE = "konsol.patches.migrate_deals_to_business_combinations"

GROUP = "ZZ-GROUP"
ENTITY = "ZZ-SUB"

# Consolidation Group FIRST: the deal controllers' validate selects the
# root's 17 policy columns, which only exist once that doctype is reloaded
# (pre_model_sync). Then children before their parents; Ownership Period
# last (its deal fields are what the patch reads).
EXPECTED_RELOADS = [
    ("reload_doc", "consolidation", "doctype", "consolidation_group"),
    ("reload_doc", "consolidation", "doctype", "business_combination_consideration"),
    ("reload_doc", "consolidation", "doctype", "business_combination_acquired_balance"),
    ("reload_doc", "consolidation", "doctype", "business_combination_cost"),
    ("reload_doc", "consolidation", "doctype", "business_combination"),
    ("reload_doc", "consolidation", "doctype", "business_disposal_proceeds"),
    ("reload_doc", "consolidation", "doctype", "business_disposal"),
    ("reload_doc", "consolidation", "doctype", "ownership_period"),
]


class _Row(dict):
    """A frappe._dict stand-in: keys readable as attributes too."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


class _Refused(Exception):
    """What a controller's validate raises in the stub (frappe.ValidationError)."""


class _Duplicate(Exception):
    """A database error on insert (frappe.DuplicateEntryError is a NameError,
    not a ValidationError): the controller did not refuse, the row failed."""


def _period(name="OP-1", **over):
    row = {
        "name": name, "consolidation_group": GROUP, "data_area_id": ENTITY,
        "effective_date": "2024-04-01", "end_date": None, "ownership_pct": 80.0,
        "consolidation_method": "full", "acquisition_date": None,
        "is_first_acquisition": 0, "acquisition_price": 0.0, "fair_value_adjustment": 0.0,
        "is_disposal": 0, "disposal_date": None, "disposal_price": 0.0, "docstatus": 1,
    }
    row.update(over)
    return _Row(row)


class _Site:
    """Stub frappe over in-memory tables. ``calls`` records, in order, every
    frappe call the patch makes; ``inserted`` the documents it inserted."""

    def __init__(self, periods=(), groups=None, linked=(), refuse=None, crash=None):
        self.calls = []
        self.periods = [_period(**p) if not isinstance(p, _Row) else p for p in periods]
        # consolidation_group -> reporting currency of its root node
        self.groups = {GROUP: "EUR"} if groups is None else groups
        # (doctype, ownership_period) pairs already linked by a deal document
        self.linked = set(linked)
        # doctype -> sentence: the controller's validate refuses the first insert
        self.refuse = dict(refuse or {})
        # doctype -> exception: every insert that validate lets through raises it
        self.crash = dict(crash or {})
        self.inserted = []
        self.cleared = 0

    # -- frappe surface -------------------------------------------------
    def module(self):
        site = self
        frappe = types.ModuleType("frappe")

        def reload_doc(module, dt, name, *a, **k):
            site.calls.append(("reload_doc", module, dt, name))

        def get_all(doctype, filters=None, fields=None, **k):
            site.calls.append(("get_all", doctype, filters))
            assert doctype == "Ownership Period", doctype
            assert filters.get("docstatus") == 1, "only submitted periods carry a real deal"
            return [dict(p) for p in site.periods if p["docstatus"] == 1]

        def get_doc(arg, *a, **k):
            assert isinstance(arg, dict), "the patch only builds new documents"
            site.calls.append(("get_doc", "new", arg.get("doctype")))
            return site._new(arg)

        def clear_last_message():
            site.cleared += 1

        db = types.SimpleNamespace()

        def exists(doctype, filters=None, *a, **k):
            site.calls.append(("db.exists", doctype, filters))
            assert doctype in ("Business Combination", "Business Disposal"), doctype
            return (doctype, filters["ownership_period"]) in site.linked

        def get_value(doctype, filters=None, fieldname=None, *a, **k):
            site.calls.append(("db.get_value", doctype, filters, fieldname))
            assert doctype == "Consolidation Group", doctype
            assert filters.get("data_area_id") == ["is", "not set"], "the root is the node with no entity"
            return site.groups.get(filters["consolidation_group"])

        db.exists = exists
        db.get_value = get_value
        frappe.db = db
        frappe.reload_doc = reload_doc
        frappe.get_all = get_all
        frappe.get_doc = get_doc
        frappe.clear_last_message = clear_last_message
        frappe.flags = types.SimpleNamespace(in_patch=True)
        frappe.ValidationError = _Refused
        frappe.DuplicateEntryError = _Duplicate
        utils = types.ModuleType("frappe.utils")
        utils.strip_html = lambda text: re.sub(r"<[^>]*>", "", text)  # frappe.utils.data.strip_html
        frappe.utils = utils
        return frappe

    def _new(self, data):
        site = self
        doc = types.SimpleNamespace(**data)
        doc.flags = types.SimpleNamespace()

        def insert(*a, **k):
            site.calls.append(("insert", doc.doctype, bool(getattr(doc.flags, "ignore_validate", False))))
            if not getattr(doc.flags, "ignore_validate", False) and doc.doctype in site.refuse:
                raise _Refused(site.refuse[doc.doctype])
            if doc.doctype in site.crash:
                raise site.crash[doc.doctype]
            doc.name = "%s-%s" % (doc.doctype, len(site.inserted) + 1)
            site.inserted.append(doc)
            site.linked.add((doc.doctype, doc.ownership_period))
            return doc

        def submit(*a, **k):
            site.calls.append(("submit", doc.doctype))
            raise AssertionError("the patch must not approve anything")

        doc.insert = insert
        doc.submit = submit
        return doc


def _load(site):
    """Install the stub frappe (and frappe.utils), load the patch by path
    under a private name, restore sys.modules; return the module. The
    patch's names are bound to the stub at load, so it runs against the stub
    after the restore too."""
    frappe = site.module()
    stubs = {"frappe": frappe, "frappe.utils": frappe.utils}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("_migrate_deals_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        return patch
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _run(site):
    """Load the patch against the stub and run execute(); return stdout."""
    patch = _load(site)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        patch.execute()
    return out.getvalue()


def _is_query(call):
    return call[0].startswith("db.") or call[0] in ("get_all", "get_doc")


def _of(site, doctype):
    return [d for d in site.inserted if d.doctype == doctype]


def _nothing_approved(site):
    assert not [c for c in site.calls if c[0] == "submit"], site.calls
    for doc in site.inserted:
        assert getattr(doc, "docstatus", 0) == 0, (doc.doctype, doc.docstatus)
        assert getattr(doc, "status", "Draft") == "Draft", (doc.doctype, doc.status)


def test_reloads_group_then_deal_doctypes_children_first_then_ownership_period_before_any_query():
    site = _Site(periods=[{"acquisition_price": 8300.0}])
    _run(site)
    n = len(EXPECTED_RELOADS)
    assert site.calls[:n] == EXPECTED_RELOADS, site.calls[:n + 1]
    assert site.calls[0] == ("reload_doc", "consolidation", "doctype", "consolidation_group"), (
        "validate reads the root's policy columns: Consolidation Group must be reloaded first")
    first_query = next(i for i, c in enumerate(site.calls) if _is_query(c))
    assert first_query >= n


def test_an_acquisition_becomes_one_draft_combination_with_one_cash_line():
    site = _Site(periods=[{"acquisition_price": 8300.0, "acquisition_date": "2024-04-15"}])
    out = _run(site)
    combos = _of(site, "Business Combination")
    assert len(combos) == 1 and not _of(site, "Business Disposal"), [d.doctype for d in site.inserted]
    bc = combos[0]
    assert bc.consolidation_group == GROUP
    assert bc.acquired_entity == ENTITY
    assert bc.acquisition_date == "2024-04-15"          # the period's own acquisition date
    assert bc.share_acquired_pct == 80.0
    assert bc.consideration_currency == "EUR"            # the root's reporting currency
    assert bc.ownership_period == "OP-1"
    assert not getattr(bc, "acquired_balances", None)
    assert not getattr(bc, "costs", None)
    assert len(bc.consideration) == 1
    line = bc.consideration[0]
    assert (line["component"], line["amount"], line["currency"]) == ("Cash", 8300.0, "EUR")
    assert "Migrated from Ownership Period OP-1" in bc.description
    assert "review and submit" in bc.description
    assert "Fair value adjustment" not in bc.description
    _nothing_approved(site)
    assert "1 Business Combination" in out, out


def test_acquisition_date_falls_back_to_the_effective_date():
    site = _Site(periods=[{"acquisition_price": 100.0, "acquisition_date": None, "effective_date": "2023-01-01"}])
    _run(site)
    assert _of(site, "Business Combination")[0].acquisition_date == "2023-01-01"


def test_fair_value_adjustment_goes_to_the_description_not_to_an_acquired_balance_line():
    site = _Site(periods=[{"acquisition_price": 8300.0, "fair_value_adjustment": 930.0}])
    _run(site)
    bc = _of(site, "Business Combination")[0]
    assert not getattr(bc, "acquired_balances", None), "an FVA without a balance line has no account"
    assert "Fair value adjustment 930.00 to allocate" in bc.description, bc.description
    assert len(bc.consideration) == 1 and bc.consideration[0]["amount"] == 8300.0


def test_a_disposal_becomes_one_draft_disposal_with_one_cash_line_zero_allowed():
    site = _Site(periods=[{
        "name": "OP-9", "effective_date": "2015-01-01", "end_date": "2020-06-30",
        "ownership_pct": 100.0, "is_disposal": 1, "disposal_date": "2020-06-30", "disposal_price": 0.0,
    }])
    out = _run(site)
    disposals = _of(site, "Business Disposal")
    assert len(disposals) == 1 and not _of(site, "Business Combination"), [d.doctype for d in site.inserted]
    bd = disposals[0]
    assert bd.consolidation_group == GROUP
    assert bd.disposed_entity == ENTITY
    assert bd.disposal_date == "2020-06-30"
    assert bd.share_disposed_pct == 100.0
    assert bd.retained_interest_pct == 0
    assert bd.proceeds_currency == "EUR"
    assert bd.ownership_period == "OP-9"
    assert len(bd.proceeds) == 1
    line = bd.proceeds[0]
    assert (line["component"], line["amount"], line["currency"]) == ("Cash", 0.0, "EUR")
    assert "Migrated from Ownership Period OP-9" in bd.description
    _nothing_approved(site)
    assert "1 Business Disposal" in out, out


def test_a_period_bought_and_later_sold_yields_both_documents():
    site = _Site(periods=[{
        "acquisition_price": 500.0, "is_disposal": 1, "disposal_date": "2022-12-31",
        "disposal_price": 700.0, "end_date": "2022-12-31",
    }])
    _run(site)
    assert len(_of(site, "Business Combination")) == 1
    assert len(_of(site, "Business Disposal")) == 1
    assert _of(site, "Business Disposal")[0].proceeds[0]["amount"] == 700.0
    _nothing_approved(site)


def test_already_linked_periods_are_skipped_and_a_rerun_inserts_nothing():
    site = _Site(
        periods=[
            {"name": "OP-A", "acquisition_price": 100.0},
            {"name": "OP-B", "acquisition_price": 200.0},
            {"name": "OP-C", "is_disposal": 1, "disposal_date": "2021-03-31", "end_date": "2021-03-31"},
        ],
        linked=[("Business Combination", "OP-A"), ("Business Disposal", "OP-C")],
    )
    out = _run(site)
    assert [d.ownership_period for d in site.inserted] == ["OP-B"], [d.ownership_period for d in site.inserted]
    assert "2 skipped" in out, out

    site.calls.clear()
    site.inserted.clear()
    _run(site)
    assert not site.inserted, site.inserted
    assert not [c for c in site.calls if c[0] == "insert"], site.calls


def test_a_period_neither_bought_nor_disposed_is_left_alone():
    site = _Site(periods=[{"acquisition_price": 0.0, "is_disposal": 0}])
    _run(site)
    assert not site.inserted
    assert not [c for c in site.calls if c[0] in ("db.exists", "get_doc")], site.calls


def test_a_draft_the_controller_refuses_is_still_inserted_with_the_reason_to_review():
    # The controller's validate may refuse a migrated deal (no policy on the
    # root yet, no Closing rate, no declared period). The migration cannot
    # supply any of that; the Draft is inserted without validate so the
    # figures are not lost, and the reason is on it for the reviewer.
    reason = "Consolidation Policy: Accounting Framework is required once the group has a Business Combination"
    site = _Site(periods=[{"acquisition_price": 8300.0}], refuse={"Business Combination": reason})
    _run(site)
    inserts = [c for c in site.calls if c[0] == "insert"]
    assert inserts == [("insert", "Business Combination", False), ("insert", "Business Combination", True)], inserts
    bc = _of(site, "Business Combination")
    assert len(bc) == 1
    assert reason in bc[0].description, bc[0].description
    assert bc[0].consideration[0]["amount"] == 8300.0
    assert site.cleared == 1, "the refused attempt's message must not leak into the migrate output"
    _nothing_approved(site)


def test_a_database_error_on_insert_lists_the_period_as_not_migrated_and_the_migrate_goes_on():
    # Only a refusal by the controller (a ValidationError) earns the retry
    # without validate. Any other error — a duplicate name, a missing
    # column — is not the controller's judgement; retrying would raise the
    # same error again and abort the whole migrate. The period is listed
    # as not migrated instead, and the next period is still processed.
    dup = _Duplicate("Duplicate entry 'BC-ZZ-GROUP-ZZ-SUB-2024-04-01' for key 'PRIMARY'")
    site = _Site(
        periods=[
            {"name": "OP-DUP", "acquisition_price": 8300.0},
            {"name": "OP-OK", "is_disposal": 1, "disposal_date": "2022-12-31", "end_date": "2022-12-31"},
        ],
        crash={"Business Combination": dup},
    )
    out = _run(site)                                     # nothing raised
    bc_inserts = [c for c in site.calls if c[0] == "insert" and c[1] == "Business Combination"]
    assert bc_inserts == [("insert", "Business Combination", False)], (
        "no retry without validate: the controller did not refuse", bc_inserts)
    assert not _of(site, "Business Combination")
    assert [d.ownership_period for d in site.inserted] == ["OP-OK"], "the migrate goes on"
    assert "OP-DUP" in out and "Duplicate entry" in out, out
    assert "0 Business Combination" in out and "0 kept with" in out, out
    _nothing_approved(site)


def test_an_error_on_the_retry_lists_the_period_as_not_migrated_instead_of_aborting():
    # The controller refused (retry route), then the retry itself failed
    # for another reason: that reason is listed, the migrate goes on.
    reason = "Consolidation Policy: Accounting Framework is required once the group has a Business Combination"
    dup = _Duplicate("Duplicate entry 'BC-ZZ-GROUP-ZZ-SUB-2024-04-01' for key 'PRIMARY'")
    site = _Site(
        periods=[
            {"name": "OP-DUP", "acquisition_price": 8300.0},
            {"name": "OP-OK", "is_disposal": 1, "disposal_date": "2022-12-31", "end_date": "2022-12-31"},
        ],
        refuse={"Business Combination": reason},
        crash={"Business Combination": dup},
    )
    out = _run(site)                                     # nothing raised
    bc_inserts = [c for c in site.calls if c[0] == "insert" and c[1] == "Business Combination"]
    assert bc_inserts == [("insert", "Business Combination", False), ("insert", "Business Combination", True)], bc_inserts
    assert [d.ownership_period for d in site.inserted] == ["OP-OK"]
    assert "OP-DUP" in out and "Duplicate entry" in out, out
    assert "0 Business Combination" in out and "0 kept with" in out, out
    _nothing_approved(site)


def test_a_database_error_is_listed_as_its_text_not_as_a_pymysql_tuple():
    # pymysql raises with args (errno, message); str() of that is the tuple
    # "(1062, "Duplicate entry …")". The person reading the migrate output
    # gets the message alone (PR #202 second review B6).
    dup = _Duplicate(1062, "Duplicate entry 'BC-ZZ-GROUP-ZZ-SUB-2024-04-01' for key 'PRIMARY'")
    site = _Site(periods=[{"name": "OP-DUP", "acquisition_price": 8300.0}], crash={"Business Combination": dup})
    out = _run(site)
    line = next(l for l in out.splitlines() if "OP-DUP" in l)
    assert line.strip() == (
        "not migrated: Ownership Period OP-DUP: Duplicate entry 'BC-ZZ-GROUP-ZZ-SUB-2024-04-01' for key 'PRIMARY'"
    ), line
    assert "1062" not in out and "(" not in line, out


def test_plain_renders_any_error_as_one_sentence_of_text():
    plain = _load(_Site())._plain
    # pymysql-style (errno, message): the message only.
    assert plain(_Duplicate(1062, "Duplicate entry 'X' for key 'PRIMARY'")) == "Duplicate entry 'X' for key 'PRIMARY'"
    # A refusal from frappe.throw: HTML gone, <br> lines joined, whitespace collapsed.
    assert plain(_Refused("<b>Business Combination</b>: no Closing rate<br>\n  approve one for  USD")) == (
        "Business Combination: no Closing rate; approve one for USD")
    # Anything else: its text, or its type when it has none.
    assert plain(ValueError("a plain message")) == "a plain message"
    assert plain(RuntimeError()) == "RuntimeError"
    assert plain(_Duplicate(1062, "")) == "_Duplicate"


def test_a_period_without_a_root_or_an_entity_or_a_disposal_date_is_reported_not_inserted():
    site = _Site(
        periods=[
            {"name": "OP-NOROOT", "consolidation_group": "ZZ-OTHER", "acquisition_price": 100.0},
            {"name": "OP-NOENTITY", "data_area_id": None, "acquisition_price": 100.0},
            {"name": "OP-NODATE", "is_disposal": 1, "disposal_date": None, "end_date": None},
            {"name": "OP-OK", "acquisition_price": 100.0},
        ],
    )
    out = _run(site)
    assert [d.ownership_period for d in site.inserted] == ["OP-OK"], [d.ownership_period for d in site.inserted]
    for name in ("OP-NOROOT", "OP-NOENTITY", "OP-NODATE"):
        assert name in out, out
    _nothing_approved(site)


def test_listed_once_in_patches_txt():
    with open(PATCHES_TXT) as f:
        lines = [l.strip() for l in f if l.strip()]
    assert lines.count(MODULE) == 1, lines.count(MODULE)
