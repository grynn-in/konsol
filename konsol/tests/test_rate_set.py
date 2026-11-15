"""Rate Set: the set a governed rate belongs to (konsol#348, S4-Q1 option c,
chosen under the standing rule, 15 Nov 2026).

One small reference doctype beside Group Exchange Rate. Exactly one set is
flagged Is Actuals: the actuals translation and the close gate read that set
and no other, and refuse by name while none is flagged. A budget or forecast
names its own set, so one site can hold actual, budget and forecast rates for
the same currency pair and period without them colliding.

Not a fixture (fixtures are reference data re-imported on every migrate;
test_fixtures_reference_only.py): a fresh site gets ACTUAL from after_install,
an existing site from the stamping patch, and a site that renames or re-flags
its sets is never overwritten.

S4-Q7 (a), chosen under the standing rule on 15 Nov 2026 (option (b), a dbt var
konsol regenerates, was rejected as a second copy of the flag that goes stale):
the sets write through to ``epm_staging.rate_sets`` (rate_set, label,
is_actuals), after the commit, on save and on delete, so the warehouse reads
which set is the actuals set the way it reads every reference table and
``reconcile_all`` repairs it. The DDL is pinned here; konsolidat copies it.

The controller, the install helper and the patch run here against a stub frappe.
"""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "rate_set")
GER_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "group_exchange_rate")
PATCH = os.path.join(APP_DIR, "patches", "stamp_rate_set_actual.py")
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")

TABLE = "epm_staging.rate_sets"
#: Identical to konsolidat's clickhouse/init-db.sql; keep them identical.
DDL = "(rate_set String, label String, is_actuals UInt8) ENGINE = MergeTree ORDER BY rate_set"


def _digest_name():
    """The real digest_name. budget_grain imports frappe at module level and
    the patch calls it, so it is loaded by path under a stub frappe, as
    test_fill_historical_rate_to_currency_patch does."""
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = types.ModuleType("frappe")
    try:
        spec = importlib.util.spec_from_file_location(
            "budget_grain_for_rate_set_test", os.path.join(APP_DIR, "epm", "budget_grain.py"))
        grain = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(grain)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return grain.digest_name


class Refused(Exception):
    pass


def _json():
    with open(os.path.join(RS_DIR, "rate_set.json")) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _json()["fields"]}


# -- the doctype ------------------------------------------------------------------

def test_the_shape():
    d, f = _json(), _fields()
    assert d["module"] == "Consolidation" and d["autoname"] == "field:rate_set"
    assert d["track_changes"] == 1 and d["title_field"] == "label"
    assert not d.get("is_submittable") and not d.get("istable")
    assert (f["rate_set"]["fieldtype"], f["rate_set"]["reqd"], f["rate_set"]["unique"]) == ("Data", 1, 1)
    assert f["label"]["fieldtype"] == "Data" and f["label"]["reqd"] == 1
    assert f["is_actuals"]["fieldtype"] == "Check" and not f["is_actuals"].get("default")
    assert f["description"]["fieldtype"] == "Small Text"
    for name in ("rate_set", "label", "is_actuals"):
        assert f[name]["in_list_view"] == 1, name
    for name in ("rate_set", "is_actuals"):
        assert f[name].get("description"), f"{name} needs help text"
    assert "actuals" in f["is_actuals"]["description"].lower() and "one" in f["is_actuals"]["description"]
    for field in d["fields"]:
        if field.get("label"):
            assert field["label"] == field["label"].title() or field["label"] == "Is Actuals", field["label"]


def test_permissions_are_group_exchange_rates():
    """Whoever maintains the rates maintains their sets, no wider."""
    with open(os.path.join(GER_DIR, "group_exchange_rate.json")) as f:
        ger = {p["role"]: p for p in json.load(f)["permissions"]}
    ours = {p["role"]: p for p in _json()["permissions"]}
    assert set(ours) == set(ger)
    for role, p in ours.items():
        theirs = ger[role]
        for right in ("read", "write", "create", "delete", "report", "export"):
            assert p.get(right, 0) == theirs.get(right, 0), (role, right)
        assert not p.get("submit") and not p.get("cancel") and not p.get("amend"), role


def test_not_shipped_as_a_fixture():
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        src = f.read()
    assert '"Rate Set"' not in src.split("fixtures = [", 1)[1].split("]", 1)[0]
    assert not os.path.exists(os.path.join(APP_DIR, "fixtures", "rate_set.json"))


# -- the controller ----------------------------------------------------------------

def _controller(flagged=(), count=0):
    """The real controller against a stub frappe whose Rate Set table holds
    ``flagged`` (names with Is Actuals set) and ``count`` rows in all."""
    record = {"sql": [], "inserted": []}
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})

    def throw(msg, exc=None, *a, **k):
        record.setdefault("thrown", []).append(getattr(exc, "__name__", None))
        raise Refused(msg)

    def sql(query, params=(), *a, **k):
        record["sql"].append(query)
        if "is_actuals = 1" in query:
            return [(n,) for n in flagged if n != params[0]]
        return []

    class Doc(dict):
        def insert(self, ignore_permissions=False):
            record["inserted"].append(dict(self))
            self.name = self["rate_set"]
            return self

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.get_doc = Doc
    frappe.db = types.SimpleNamespace(sql=sql, count=lambda dt, filters=None: count)

    class Document:
        def __init__(self, **fields):
            self.__dict__.update(fields)

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, name, default=None):
            return self.__dict__.get(name, default)

    # KR: the write-through goes through konsol.clickhouse's after-commit
    # sync, recorded here as (doctype, table, field map).
    record["synced"] = []
    clickhouse = types.ModuleType("konsol.clickhouse")
    clickhouse.sync_doctype_after_commit = lambda dt, table, fm: record["synced"].append((dt, table, dict(fm)))

    mods = {n: types.ModuleType(n) for n in ("frappe.model", "frappe.model.document")}
    mods["frappe"] = frappe
    mods["konsol.clickhouse"] = clickhouse
    mods["frappe.model.document"].Document = Document
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("rate_set_under_test", os.path.join(RS_DIR, "rate_set.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module, record


def test_at_most_one_set_is_flagged_actuals_refused_by_name():
    module, record = _controller(flagged=("ACTUAL",))
    doc = module.RateSet(doctype="Rate Set", name="BUDGET_2027", rate_set="BUDGET_2027",
                         label="Budget 2027", is_actuals=1)
    try:
        doc.validate()
    except Refused as e:
        assert "ACTUAL" in str(e) and "Actuals" in str(e), str(e)
    else:
        raise AssertionError("a second Actuals set must be refused")
    # REPEATABLE READ: two flags saved in parallel must not both pass
    assert any("FOR UPDATE" in q and "is_actuals = 1" in q for q in record["sql"]), record["sql"]


def test_the_flagged_set_itself_and_an_unflagged_set_pass():
    module, _ = _controller(flagged=("ACTUAL",))
    module.RateSet(doctype="Rate Set", name="ACTUAL", rate_set="ACTUAL", label="Actual", is_actuals=1).validate()
    module.RateSet(doctype="Rate Set", name="BUDGET_2027", rate_set="BUDGET_2027", label="Budget 2027",
                   is_actuals=0).validate()


def test_a_fresh_site_gets_actual_flagged_and_a_site_with_sets_is_left_alone():
    module, record = _controller(count=0)
    assert module.ensure_actual_set() == "ACTUAL"
    [row] = record["inserted"]
    assert (row["doctype"], row["rate_set"], row["is_actuals"]) == ("Rate Set", "ACTUAL", 1)
    assert row["label"] and row["description"], "a seeded row says what it is"
    module, record = _controller(count=1)
    assert module.ensure_actual_set() is None and record["inserted"] == []


def test_a_fresh_install_creates_the_set():
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        src = f.read()
    after_install = src.split("after_install = [", 1)[1].split("]", 1)[0]
    assert "konsol.consolidation.doctype.rate_set.rate_set.ensure_actual_set" in after_install
    assert after_install.index("create_roles") < after_install.index("ensure_actual_set")


# -- KR (S4-Q7 a): the sets write through to epm_staging.rate_sets -------------------

def _literal(name):
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == name)


def test_the_warehouse_table_ddl_is_pinned_and_ships_whole():
    """konsolidat copies this string into clickhouse/init-db.sql; a new table
    ships complete, never through _ADDED_COLUMNS, and is not retired."""
    assert _literal("_REFERENCE_TABLE_DDL")[TABLE] == DDL
    assert TABLE not in _literal("_ADDED_COLUMNS")
    assert TABLE not in _literal("_RETIRED_TABLES")


def test_the_controller_names_the_table_and_its_columns_in_ddl_order():
    module, _ = _controller()
    assert module.RateSet.CH_TABLE == TABLE
    columns = [c.strip().split()[0] for c in DDL.split("(", 1)[1].split(")", 1)[0].split(",")]
    assert list(module.RateSet.CH_FIELD_MAP) == columns == ["rate_set", "label", "is_actuals"]
    assert module.RateSet.CH_FIELD_MAP == {c: c for c in columns}
    # Not submittable and not governed: every set reaches the warehouse, so
    # the flag can be read there. No CH_SYNC_FILTERS, which would hide rows.
    assert not hasattr(module.RateSet, "CH_SYNC_FILTERS")


def test_a_save_and_a_delete_each_sync_after_the_commit():
    """on_update and after_delete (never on_trash: #120, the row is still
    there and the full-table re-send would put it straight back)."""
    module, record = _controller()
    doc = module.RateSet(doctype="Rate Set", name="BUDGET_2027", rate_set="BUDGET_2027", label="Budget 2027")
    doc.on_update()
    assert record["synced"] == [("Rate Set", TABLE, module.RateSet.CH_FIELD_MAP)]
    doc.after_delete()
    assert len(record["synced"]) == 2 and record["synced"][1][1] == TABLE
    assert not hasattr(module.RateSet, "on_trash")
    with open(os.path.join(RS_DIR, "rate_set.py")) as f:
        src = f.read()
    assert "sync_doctype_after_commit" in src and "sync_table(" not in src and "execute(" not in src, (
        "a document hook syncs after the commit, never in the transaction")


# -- the patch: existing rates are stamped ACTUAL -------------------------------------

def _patch_module(frappe):
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("stamp_rate_set_under_test", PATCH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return module


def test_the_patch_is_listed_once():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        lines = [line.strip() for line in f]
    assert lines.count("konsol.patches.stamp_rate_set_actual") == 1


def _her(name, group="ZZGRP", entity="ZZNL", account="ZZ3000", date="2024-01-01", ccy="EUR", rate_set=None):
    return {"name": name, "consolidation_group": group, "data_area_id": entity, "main_account": account,
            "rate_date": date, "to_currency": ccy, "rate_set": rate_set}


def test_plan_stamps_blank_rows_and_renames_each_tranche_to_its_new_key():
    digest_name = _digest_name()
    frappe = types.ModuleType("frappe")
    m = _patch_module(frappe)
    old = digest_name("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", "EUR"])
    new = digest_name("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", "EUR", "ACTUAL"])
    stamped_and_named = _her(new, rate_set="ACTUAL")
    updates = m.plan_her([_her(old), stamped_and_named, _her("HER-odd", ccy="USD", rate_set="ACTUAL")], "ACTUAL")
    assert updates == [
        (old, "ACTUAL", new),
        ("HER-odd", None, digest_name("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", "USD", "ACTUAL"])),
    ], updates
    assert m.plan_her([stamped_and_named], "ACTUAL") == [], "a second run writes nothing"
    assert m.duplicate_targets([(old, "ACTUAL", new), ("HER-x", "ACTUAL", new)]) == {new: [old, "HER-x"]}


def _site(flagged=("ACTUAL",), sets=1, blank_ger=3, her_rows=()):
    record = {"sql": [], "set_value": [], "renamed": [], "reloaded": [], "printed": []}
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})

    def throw(msg, exc=None, *a, **k):
        raise Refused(msg)

    def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit_page_length=None, **k):
        if doctype == "Rate Set":
            return list(flagged)
        if doctype == "Historical Equity Rate":
            return [dict(r) for r in her_rows]
        return []

    def sql(query, params=(), *a, **k):
        record["sql"].append((query, params))
        return []

    def count(doctype, filters=None):
        if doctype == "Rate Set":
            return sets
        if doctype == "Group Exchange Rate":
            return blank_ger
        return 0

    frappe.throw = throw
    frappe.get_all = get_all
    frappe.reload_doc = lambda *a: record["reloaded"].append(a)
    frappe.rename_doc = lambda dt, old, new, **k: record["renamed"].append((old, new))
    frappe.db = types.SimpleNamespace(
        sql=sql, count=count, table_exists=lambda t: True,
        set_value=lambda dt, name, field, value, **k: record["set_value"].append((dt, name, field, value)),
        exists=lambda dt, name: False)
    m = _patch_module(frappe)
    m.print = lambda *a, **k: record["printed"].append(" ".join(str(x) for x in a))
    # the patch imports the real ensure_actual_set at call time; serve it the
    # real controller module, bound to this site's stub frappe
    rate_set_module, _ = _controller(count=sets)
    rate_set_module.frappe = frappe
    sys.modules["konsol.consolidation.doctype.rate_set.rate_set"] = rate_set_module
    return m, record, frappe


def test_the_patch_stamps_every_blank_rate_with_the_flagged_set_and_logs_counts():
    digest_name = _digest_name()
    old = digest_name("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", "EUR"])
    m, record, _ = _site(blank_ger=3, her_rows=[_her(old)])
    m.execute()
    updates = [(q, p) for q, p in record["sql"] if q.lstrip().upper().startswith("UPDATE")]
    [(ger_update, params)] = updates
    assert "tabGroup Exchange Rate" in ger_update and "rate_set" in ger_update and params == ("ACTUAL",)
    assert "IFNULL(rate_set, '') = ''" in ger_update, "blank is NULL or '' (a Link stores NULL)"
    assert record["set_value"] == [("Historical Equity Rate", old, "rate_set", "ACTUAL")]
    new = digest_name("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", "EUR", "ACTUAL"])
    assert record["renamed"] == [(old, new)]
    assert any("3" in line and "Group Exchange Rate" in line for line in record["printed"]), record["printed"]
    assert any("1" in line and "Historical Equity Rate" in line for line in record["printed"]), record["printed"]
    assert [a[2] for a in record["reloaded"]] == ["rate_set", "group_exchange_rate", "historical_equity_rate"]


def test_the_patch_creates_actual_on_a_site_with_no_set_and_is_idempotent():
    m, record, frappe = _site(flagged=(), sets=0, blank_ger=2)
    created = []
    frappe.get_doc = lambda d: types.SimpleNamespace(insert=lambda ignore_permissions=False: created.append(d))
    m.execute()
    [row] = created
    assert (row["rate_set"], row["is_actuals"]) == ("ACTUAL", 1)
    assert any("UPDATE" in q for q, _ in record["sql"])
    # second run: everything stamped, a flagged set exists; nothing written
    m, record, _ = _site(blank_ger=0, her_rows=[])
    m.execute()
    assert not [q for q, _ in record["sql"] if "UPDATE" in q] and record["renamed"] == []


def test_the_patch_refuses_rather_than_guess_when_sets_exist_but_none_is_flagged():
    m, record, _ = _site(flagged=(), sets=2, blank_ger=1)
    try:
        m.execute()
    except Refused as e:
        assert "Is Actuals" in str(e) and "Rate Set" in str(e), str(e)
    else:
        raise AssertionError("stamping with no flagged set must refuse")
    assert not [q for q, _ in record["sql"] if "UPDATE" in q]


def test_the_patch_refuses_a_rename_onto_an_existing_tranche():
    digest_name = _digest_name()
    old = digest_name("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", "EUR"])
    m, record, frappe = _site(blank_ger=0, her_rows=[_her(old)])
    frappe.db.exists = lambda dt, name: True
    try:
        m.execute()
    except Refused as e:
        assert old in str(e), str(e)
    else:
        raise AssertionError("a rename onto an existing name must refuse")
    assert record["set_value"] == [] and record["renamed"] == []
