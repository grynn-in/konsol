"""The fill_cash_flow_categories_from_chart migration patch (konsol#196), host-run.

Existing sites already hold a published chart whose Balance Sheet leaves
declare cf_category / cf_line_item, but Cash Flow Category (the only table
dbt reads for the cash-flow statement) was keyed by hand. The patch inserts
the missing Published rows from the chart's mapping (group_chart_model.
cash_flow_mapping), leaves accounts that already have a live row alone,
republishes an account's Inactive row instead of colliding with it, and
reloads both doctypes before it queries anything (patches.txt has no
sections, so it runs pre_model_sync). A stub frappe records every call.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "fill_cash_flow_categories_from_chart.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
MODULE = "konsol.patches.fill_cash_flow_categories_from_chart"


def _account(code, **over):
    row = {"name": code, "main_account": code, "is_group": 0, "statement_section": "Balance Sheet",
           "cf_category": "Operating", "cf_line_item": "Cash at bank", "is_cash": 0,
           "status": "Published"}
    row.update(over)
    return row


class _Site:
    """Stub frappe over two in-memory tables. ``calls`` records, in order,
    every frappe call the patch makes; ``inserted`` the docs it inserted."""

    def __init__(self, accounts=(), categories=()):
        self.calls = []
        self.accounts = [dict(a) for a in accounts]
        self.categories = [dict(c) for c in categories]
        self.inserted = []
        self.saved = []

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")

        def reload_doc(module, dt, name, *a, **k):
            site.calls.append(("reload_doc", module, dt, name))

        def get_all(doctype, filters=None, fields=None, pluck=None, **k):
            site.calls.append(("get_all", doctype, filters))
            return site._get_all(doctype, filters or {}, fields, pluck)

        def get_doc(arg, *a, **k):
            if isinstance(arg, dict):
                site.calls.append(("get_doc", "new", arg.get("doctype")))
                return site._new(arg)
            assert arg == "Cash Flow Category" and a, "the patch loads existing docs by (doctype, name)"
            site.calls.append(("get_doc", arg, a[0]))
            return site._existing(a[0])

        frappe.reload_doc = reload_doc
        frappe.get_all = get_all
        frappe.get_doc = get_doc
        frappe.db = types.SimpleNamespace()
        frappe.flags = types.SimpleNamespace(in_patch=True)
        return frappe

    def _new(self, data):
        site = self
        doc = types.SimpleNamespace(**data)

        def insert(ignore_permissions=False, **k):
            site.calls.append(("insert", doc.doctype, doc.main_account, ignore_permissions))
            site.inserted.append(doc)
            site.categories.append({"main_account": doc.main_account, "status": doc.status})
            return doc

        doc.insert = insert
        return doc

    def _existing(self, name):
        site = self
        row = next(r for r in self.categories if r.get("name") == name)
        doc = types.SimpleNamespace(doctype="Cash Flow Category", **row)

        def update(values):
            doc.__dict__.update(values)
            return doc

        def save(ignore_permissions=False, **k):
            site.calls.append(("save", doc.doctype, doc.name, ignore_permissions))
            site.saved.append(doc)
            row.update(main_account=doc.main_account, status=doc.status)
            return doc

        doc.update = update
        doc.save = save
        return doc

    @staticmethod
    def _match(row, filters):
        for key, want in filters.items():
            have = row.get(key)
            if isinstance(want, (list, tuple)):
                op, value = want
                if op == "!=":
                    if have == value:
                        return False
                elif op == "=":
                    if have != value:
                        return False
                else:
                    raise AssertionError("unexpected operator %r" % op)
            elif have != want:
                return False
        return True

    def _get_all(self, doctype, filters, fields, pluck):
        if doctype == "Main Account":
            table = self.accounts
        elif doctype == "Cash Flow Category":
            table = self.categories
        else:
            raise AssertionError("unexpected get_all(%s)" % doctype)
        rows = [r for r in table if self._match(r, filters)]
        if pluck:
            return [r.get(pluck) for r in rows]
        if fields:
            return [{f: r.get(f) for f in fields} for r in rows]
        return [dict(r) for r in rows]


def _run(site):
    """Install the stub, load the patch by path under a private name, run
    execute() with the stub still installed, then restore sys.modules."""
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = site.module()
    try:
        spec = importlib.util.spec_from_file_location("_fill_cash_flow_categories_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        return patch.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _inserts(site):
    return [c for c in site.calls if c[0] == "insert"]


def _saves(site):
    return [c for c in site.calls if c[0] == "save"]


def test_reload_both_doctypes_before_any_query():
    site = _Site(accounts=[_account("1000")])
    _run(site)
    assert site.calls[:2] == [
        ("reload_doc", "epm", "doctype", "cash_flow_category"),
        ("reload_doc", "epm", "doctype", "main_account"),
    ], site.calls[:4]
    first_query = next(i for i, c in enumerate(site.calls) if c[0] in ("get_all", "get_doc"))
    assert first_query >= 2, site.calls


def test_published_mapped_leaf_without_row_is_inserted():
    site = _Site(accounts=[
        _account("1000 ", is_cash="1"),                 # code normalised by text()
        _account("1010", status="Draft"),               # not Published: not read
    ])
    _run(site)
    assert _inserts(site) == [("insert", "Cash Flow Category", "1000", True)], site.calls
    doc = site.inserted[0]
    assert (doc.main_account, doc.cf_category, doc.cf_line_item, doc.is_cash) == \
        ("1000", "Operating", "Cash at bank", 1)
    # konsol#197: nothing reads sign, so the patch must not invent one.
    assert not hasattr(doc, "sign") or doc.sign in (None, "")
    assert doc.status == "Published"


def test_account_with_live_row_is_left_alone():
    site = _Site(
        accounts=[_account("1000"), _account("1100", cf_category="Investing", cf_line_item="Capex")],
        categories=[
            {"name": "CFC-1000", "main_account": "1000", "status": "Published"},
        ],
    )
    _run(site)
    assert _inserts(site) == [("insert", "Cash Flow Category", "1100", True)], site.calls
    assert _saves(site) == [], site.calls


def test_account_whose_only_row_is_inactive_is_republished_not_inserted():
    # A hand-keyed row that was later set Inactive, possibly under another
    # name: inserting CFC-<code> beside it would collide, so the patch
    # republishes that row with the chart's mapping instead.
    site = _Site(
        accounts=[_account("1100", cf_category="Investing", cf_line_item="Capex", is_cash=1)],
        categories=[
            {"name": "Capex mapping", "main_account": "1100", "status": "Inactive",
             "cf_category": "Operating", "cf_line_item": "old", "is_cash": 0},
        ],
    )
    _run(site)
    assert _inserts(site) == [], site.calls
    assert _saves(site) == [("save", "Cash Flow Category", "Capex mapping", True)], site.calls
    doc = site.saved[0]
    assert (doc.main_account, doc.cf_category, doc.cf_line_item, doc.is_cash) == \
        ("1100", "Investing", "Capex", 1)
    # konsol#197: republishing must not re-add the value nothing reads.
    assert not hasattr(doc, "sign") or doc.sign in (None, "")
    assert doc.status == "Published"


def test_profit_and_loss_and_unmapped_accounts_are_skipped():
    site = _Site(accounts=[
        _account("4000", statement_section="Profit and Loss"),
        _account("1", is_group=1),                       # heading
        _account("1200", cf_line_item=""),               # mapping incomplete
    ])
    _run(site)
    assert _inserts(site) == [], site.calls
    assert not [c for c in site.calls if c[0] == "get_doc"], site.calls


def test_listed_once_in_patches_txt():
    with open(PATCHES_TXT) as f:
        lines = [l.strip() for l in f if l.strip()]
    assert lines.count(MODULE) == 1, lines.count(MODULE)
