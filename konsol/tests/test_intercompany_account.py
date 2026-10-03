"""Intercompany Account: the intercompany flag on the group chart (konsol#159,
decision 3 of 13 Sep 2026). The pure pairing rules run against the module
loaded by path with stubbed frappe/konsol imports; the rest is contract."""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "intercompany_account")


def _load():
    saved = {k: sys.modules.get(k) for k in
             ("frappe", "konsol", "konsol.governed_reference", "konsol.schema_lifecycle")}
    frappe = types.ModuleType("frappe")
    gov = types.ModuleType("konsol.governed_reference")
    gov.GovernedReferenceDocument = type("GovernedReferenceDocument", (), {})
    sl = types.ModuleType("konsol.schema_lifecycle")
    sl.check_epm_admin = lambda: None
    sys.modules.update({"frappe": frappe, "konsol": types.ModuleType("konsol"),
                        "konsol.governed_reference": gov, "konsol.schema_lifecycle": sl})
    try:
        spec = importlib.util.spec_from_file_location("ica_under_test", os.path.join(DT_DIR, "intercompany_account.py"))
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


def test_a_pair_is_unordered_and_a_blank_counterpart_is_the_account_itself():
    assert M.pair_of("1300", "2300") == ("1300", "2300") == M.pair_of("2300", "1300")
    assert M.pair_of("1300", "") == ("1300",) == M.pair_of(" 1300 ", "1300")


def test_each_account_belongs_to_one_pair():
    others = [("ICA-4030", "4030", "5030"), ("ICA-1900", "1900", "")]
    # the same pair from the other side is not a conflict
    assert M.pair_conflicts("5030", "4030", others) == []
    # 5030 on its own, or with another account, would be matched twice
    assert M.pair_conflicts("5030", "", others) == ["ICA-4030"]
    assert M.pair_conflicts("5030", "6000", others) == ["ICA-4030"]
    # 1900 is a same-account pair: pairing it with anything else conflicts
    assert M.pair_conflicts("2900", "1900", others) == ["ICA-1900"]
    assert M.pair_conflicts("7000", "7100", others) == []


class _Flags(dict):
    """frappe._dict: a missing attribute reads as None."""
    __getattr__ = dict.get

    def __setattr__(self, k, v):
        self[k] = v


class _Refused(Exception):
    pass


def _doc(status, before_status, name="ICA-4030", before_counterpart="5030"):
    d = M.IntercompanyAccount()
    d.name, d.main_account, d.counterpart_account, d.status = name, "4030", "5030", status
    d.flags = _Flags()
    d.get_doc_before_save = lambda: (types.SimpleNamespace(status=before_status, main_account="4030",
                                                           counterpart_account=before_counterpart)
                                     if before_status else None)
    return d


def test_every_save_of_a_published_row_needs_the_close_lead():
    """#173 review A1: only the move INTO Published was guarded, so an EPM
    Analyst could edit a live flag's accounts or unpublish it by a plain save.
    A2: a plain save into Published skipped the publish checks."""
    admin, checks = [], []
    saved = M.check_epm_admin
    M.check_epm_admin = lambda: admin.append(1)
    try:
        for status, before, want_admin, want_checks in [
            ("Published", "Published", True, False),   # editing a live flag's description
            ("Draft", "Published", True, False),       # pulling it back to Draft
            ("Inactive", "Published", True, False),   # unpublishing by a plain save
            ("Published", "Draft", True, True),        # publishing by a plain save
            ("Published", None, True, True),           # inserted as Published
            ("Draft", "Draft", False, False),          # an Analyst's draft
            ("Draft", None, False, False),
            ("Inactive", "Draft", False, False),
        ]:
            admin.clear(), checks.clear()
            d = _doc(status, before)
            d._before_publish = lambda: checks.append(1)
            d._guard_publish()
            assert bool(admin) == want_admin, (status, before)
            assert bool(checks) == want_checks, (status, before)
        # re-review K1: editing a live row's accounts is a publish of new
        # accounts, so it passes the publish checks (chart, difference account)
        for before_counterpart in ("6070", ""):
            admin.clear(), checks.clear()
            d = _doc("Published", "Published", before_counterpart=before_counterpart)
            d._before_publish = lambda: checks.append(1)
            d._guard_publish()
            assert admin and checks, before_counterpart
    finally:
        M.check_epm_admin = saved


def test_the_publish_checks_run_once_per_save_for_the_same_accounts():
    """publish() runs _before_publish, then saves, and the save would run it
    again from _guard_publish; the second call must not re-read the chart.
    Re-review K3: the flag records which accounts were checked, so other
    accounts are checked anew."""
    d = _doc("Published", "Draft")
    d.flags.ica_publish_checked = ("4030", "5030")
    assert M.IntercompanyAccount._before_publish(d) is None   # returns before touching frappe
    d.counterpart_account = "6070"
    try:
        M.IntercompanyAccount._before_publish(d)   # reaches the chart check
        assert False, "other accounts were not checked"
    except (ImportError, AttributeError):
        pass


def test_the_one_pair_check_is_a_locking_read():
    """#173 review A4: under REPEATABLE READ a plain read can miss a row
    another transaction committed, so two saves could both pass.
    Re-review K2: one serialising lock first (the build_lock pattern), then
    locking reads by equality on the indexed account columns only, never a
    scan of every row, which deadlocked two unrelated saves."""
    sent = []

    def sql(query, values=None, as_dict=False):
        sent.append((query, values))
        if "`main_account` = %s" in query and values[0] == "5030":
            return [_Flags(name="ICA-5030", main_account="5030", counterpart_account="")]
        return []

    def throw(msg, *a, **k):
        raise _Refused(msg)

    M.frappe.db = types.SimpleNamespace(sql=sql)
    M.frappe.throw = throw
    d = _doc("Draft", None)
    try:
        d._validate_one_pair()
        assert False, "the conflicting pair was not refused"
    except _Refused as e:
        assert "ICA-5030 already pairs" in str(e)
    lock, values = sent[0]
    assert lock == "SELECT `name` FROM `tabDocType` WHERE `name` = %s FOR UPDATE" and values == ("Intercompany Account",)
    reads = sent[1:]
    assert len(reads) == 4, reads   # two accounts x two indexed columns
    for query, values in reads:
        assert query.rstrip().endswith("FOR UPDATE") and "`status` != 'Inactive'" in query
        assert ("`main_account` = %s" in query) != ("`counterpart_account` = %s" in query), query
        assert values[1] == "ICA-4030" and values[0] in ("4030", "5030")


def test_doctype_contract():
    with open(os.path.join(DT_DIR, "intercompany_account.json")) as f:
        meta = json.load(f)
    assert meta["name"] == "Intercompany Account" and meta["module"] == "Consolidation"
    fields = {f["fieldname"]: f for f in meta["fields"]}
    assert fields["main_account"].get("unique") == 1 and fields["main_account"].get("reqd") == 1
    assert not fields["counterpart_account"].get("reqd")
    assert fields["status"]["options"] == "Draft\nPublished\nInactive"
    assert meta["autoname"] == "format:ICA-{main_account}"
    assert set(meta["field_order"]) == set(fields)


def test_published_rows_write_through_like_every_governed_reference():
    with open(os.path.join(DT_DIR, "intercompany_account.py")) as f:
        src = f.read()
    assert "class IntercompanyAccount(GovernedReferenceDocument)" in src
    assert 'CH_TABLE = "epm_staging.intercompany_accounts"' in src
    assert 'CH_SYNC_FILTERS = {"status": _PUBLISHED}' in src
    assert set(M.IntercompanyAccount.CH_FIELD_MAP) == {"main_account", "counterpart_account", "description", "status"}
    # the governed base owns publish/unpublish/after_delete
    for copied in ("def publish(", "def unpublish(", "def after_delete("):
        assert copied not in src


def test_the_staging_table_ddl_matches_the_field_map():
    """The columns synced must be the columns created, in the bootstrap DDL
    konsolidat's init-db.sql mirrors."""
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        src = f.read()
    start = src.index('"epm_staging.intercompany_accounts": (')
    body = src[start:src.index("),", start)]
    for col in M.IntercompanyAccount.CH_FIELD_MAP:
        assert f"{col} String" in body, col


def test_upload_and_consolidation_group_read_the_same_flag():
    """Both read the Published Intercompany Account rows: the uploads through
    intercompany_accounts(), the group's difference-account check through a
    locking read of the same rows (#173 re-review L5)."""
    for rel in ("tb_bulk.py",
                os.path.join("consolidation", "doctype", "trial_balance_submission", "trial_balance_submission.py")):
        with open(os.path.join(APP_DIR, rel)) as f:
            assert "intercompany_accounts" in f.read(), rel
    with open(os.path.join(APP_DIR, "consolidation", "doctype", "consolidation_group", "consolidation_group.py")) as f:
        src = f.read()
    assert "FROM `tabIntercompany Account`" in src and "`status` = 'Published' FOR UPDATE" in src


def test_the_publish_check_locks_before_reading_difference_accounts():
    """#173 re-review L5: the same serialising lock as Consolidation Group's
    difference-account check, then a locking read by equality (indexed)."""
    sent = []

    def sql(query, values=None, as_dict=False):
        sent.append((" ".join(query.split()), values))
        if "`tabConsolidation Group`" in query and values == ("5030",):
            return [_Flags(name="CG-ZZGRP-")]
        if "`tabMain Account`" in query:            # konsol#293: the allow_ic read
            return [{"name": "4030", "allow_ic": 1}, {"name": "5030", "allow_ic": 1}]
        return []

    def throw(msg, *a, **k):
        raise _Refused(msg)

    chart = types.ModuleType("group_chart_stub")
    # konsol#293: _before_publish reads the rows now, not just the codes, so the
    # allow_ic precondition and chart membership come from one read. Both
    # accounts declare allow_ic here, so what this test asserts — the
    # difference-account refusal — is still what fires.
    _accts = {c: {"main_account": c, "is_group": 0, "is_posting": 1, "allow_ic": 1}
              for c in ("4030", "5030")}
    chart.chart_accounts = lambda: _accts
    chart.posting_codes = lambda c: {k for k, a in c.items() if not a["is_group"] and a["is_posting"]}
    chart.chart_codes = lambda: {"4030", "5030"}
    saved = sys.modules.get("konsol.group_chart")
    sys.modules["konsol.group_chart"] = chart
    M.frappe.db = types.SimpleNamespace(sql=sql)
    M.frappe.throw = throw
    try:
        d = _doc("Published", "Draft")
        M.IntercompanyAccount._before_publish(d)
        assert False, "the difference account was not refused"
    except _Refused as e:
        assert "CG-ZZGRP- books intercompany differences" in str(e)
    finally:
        if saved is None:
            sys.modules.pop("konsol.group_chart", None)
        else:
            sys.modules["konsol.group_chart"] = saved
    assert sent[0] == ("SELECT `name` FROM `tabDocType` WHERE `name` = %s FOR UPDATE", ("Intercompany Account",))
    # Only the difference-account reads: konsol#293 added a locking allow_ic read
    # between them and the lock, and this test is about these queries' shape, so
    # it selects them rather than asserting the whole sequence.
    reads = [s for s in sent[1:] if "`ic_difference_account` = %s" in s[0]]
    assert [v for _q, v in reads] == [("4030",), ("5030",)]
    assert all(q.endswith("FOR UPDATE") for q, _v in reads)


# -- konsol#293: the chart must declare allow_ic before a pairing is published ----------

def test_publishing_a_pairing_needs_allow_ic_on_both_accounts():
    """#182 PR5a. The pairing table stays the source of the intercompany set;
    allow_ic is the chart's precondition for being in it. Published without it,
    the chart says the account carries no intercompany rows while consolidation
    eliminates it — the two declarations disagree and nothing notices."""
    problem = M.allow_ic_problem(("4030", "5030"), {"4030": 1, "5030": 0})
    assert problem, "a pairing naming an account without allow_ic must be refused"
    assert "5030" in problem
    assert "4030" not in problem, "only the undeclared account is named"
    assert "allow_ic" in problem


def test_a_pairing_is_published_when_the_chart_declares_both():
    assert M.allow_ic_problem(("4030", "5030"), {"4030": 1, "5030": 1}) == ""


def test_an_account_absent_from_the_chart_map_is_not_declared():
    """Chart membership is checked separately; absent here means the flag was
    never declared, so it is refused rather than assumed."""
    problem = M.allow_ic_problem(("4030",), {})
    assert problem and "4030" in problem


def test_a_single_account_pairing_is_checked_too():
    """A blank counterpart means both sides use one account (pair_of returns a
    1-tuple); it still needs the flag."""
    assert M.allow_ic_problem(("4030",), {"4030": 0})
    assert M.allow_ic_problem(("4030",), {"4030": 1}) == ""


def test_the_allow_ic_precondition_is_read_under_a_lock():
    """Review finding 1. The chart's allow_ic must be read FOR UPDATE on the
    rows being paired, not from the plain chart_accounts() dict: MariaDB here is
    REPEATABLE READ, so two plain reads let this check and Main Account's
    withdrawal guard both pass and leave a Published pairing on an account
    declaring allow_ic = 0."""
    sent = []

    def sql(query, values=None, as_dict=False):
        sent.append((" ".join(query.split()), values))
        if "`tabMain Account`" in query:
            return [{"name": "4030", "allow_ic": 1}, {"name": "5030", "allow_ic": 1}]
        return []

    chart = types.ModuleType("group_chart_stub")
    _accts = {c: {"main_account": c, "is_group": 0, "is_posting": 1, "allow_ic": 1}
              for c in ("4030", "5030")}
    chart.chart_accounts = lambda: _accts
    chart.posting_codes = lambda c: {k for k, a in c.items() if not a["is_group"] and a["is_posting"]}
    chart.chart_codes = lambda: {"4030", "5030"}
    saved = sys.modules.get("konsol.group_chart")
    sys.modules["konsol.group_chart"] = chart
    M.frappe.db = types.SimpleNamespace(sql=sql)
    M.frappe.throw = lambda msg, *a, **k: (_ for _ in ()).throw(_Refused(msg))
    try:
        M.IntercompanyAccount._before_publish(_doc("Published", "Draft"))
    except Exception:
        pass
    finally:
        if saved is None:
            sys.modules.pop("konsol.group_chart", None)
        else:
            sys.modules["konsol.group_chart"] = saved

    reads = [q for q, _ in sent if "`tabMain Account`" in q]
    assert reads, "allow_ic was not read from tabMain Account at all"
    assert all("FOR UPDATE" in q for q in reads), reads
    # and the serialising doctype lock still comes first, as it did before
    assert "FOR UPDATE" in sent[0][0] and "`tabDocType`" in sent[0][0], sent[0]
    assert sent.index(next(s for s in sent if "`tabMain Account`" in s[0])) > 0


def test_the_withdrawal_guard_locks_before_reading_the_pairing_table():
    """Review finding 1, the other side. Main Account's guard must take
    Intercompany Account's serialising tabDocType lock before it reads the
    pairing table, the lock _validate_one_pair and _before_publish take."""
    path = os.path.join(APP_DIR, "epm", "doctype", "main_account", "main_account.py")
    with open(path) as f:
        src = f.read()
    start = src.index("def _refuse_allow_ic_withdrawal")
    body = src[start:src.index("\n    def ", start + 10)]
    assert "FOR UPDATE" in body, "the withdrawal guard reads the pairing table without a lock"
    assert body.index("FOR UPDATE") < body.index("intercompany_accounts(self._row"
                                                 ) if "intercompany_accounts(self._row" in body else True
    assert "`tabDocType`" in body, body


# -- konsol#305-W3-7 (C17): the reverse of C16 -------------------------------

def test_publish_is_refused_while_close_settings_declares_none():
    """C16's declaration, read back here as a raw ``tabSingles`` value (never
    ``get_single_value``, W3-P9), under the same lock C16's save takes, so a
    concurrent Close Settings save and this publish cannot both pass."""
    sent = []
    chart_calls = []

    def sql(query, values=None, as_dict=False):
        sent.append((" ".join(query.split()), values))
        if "`tabSingles`" in query:
            return [("None in this group",)]
        return []

    def throw(msg, *a, **k):
        raise _Refused(msg)

    chart = types.ModuleType("group_chart_stub")
    chart.chart_accounts = lambda: chart_calls.append(1) or {}
    chart.posting_codes = lambda c: chart_calls.append(1) or set()
    chart.chart_codes = lambda: chart_calls.append(1) or set()
    saved = sys.modules.get("konsol.group_chart")
    sys.modules["konsol.group_chart"] = chart
    M.frappe.db = types.SimpleNamespace(sql=sql)
    M.frappe.throw = throw
    try:
        M.IntercompanyAccount._before_publish(_doc("Published", "Draft"))
        assert False, "a declared none did not refuse the publish"
    except _Refused as e:
        assert str(e) == (
            "Close Settings declares no intercompany in this group "
            "(Intercompany: None in this group). Clear that declaration "
            "before publishing this pairing.")
    finally:
        if saved is None:
            sys.modules.pop("konsol.group_chart", None)
        else:
            sys.modules["konsol.group_chart"] = saved
    assert not chart_calls, "the chart was read after a refused publish"
    assert sent[0] == ("SELECT `name` FROM `tabDocType` WHERE `name` = %s FOR UPDATE",
                        ("Intercompany Account",))
    assert len(sent) == 2, sent   # the lock, then the Singles read only
    query, values = sent[1]
    assert query == ("SELECT `value` FROM `tabSingles` WHERE `doctype` = 'Close Settings' "
                      "AND `field` = 'intercompany_declaration' FOR UPDATE")
    assert query.endswith("FOR UPDATE")


def test_a_blank_declaration_does_not_refuse_the_publish():
    """Blank (never declared, or the meta lacks the field and the row reads
    as missing): the publish checks continue exactly as before."""
    for singles_rows in ([], [("",)]):
        sent = []

        def sql(query, values=None, as_dict=False, _rows=singles_rows):
            sent.append((" ".join(query.split()), values))
            if "`tabSingles`" in query:
                return _rows
            if "`tabConsolidation Group`" in query and values == ("5030",):
                return [_Flags(name="CG-ZZGRP-")]
            if "`tabMain Account`" in query:
                return [{"name": "4030", "allow_ic": 1}, {"name": "5030", "allow_ic": 1}]
            return []

        def throw(msg, *a, **k):
            raise _Refused(msg)

        chart = types.ModuleType("group_chart_stub")
        _accts = {c: {"main_account": c, "is_group": 0, "is_posting": 1, "allow_ic": 1}
                  for c in ("4030", "5030")}
        chart.chart_accounts = lambda: _accts
        chart.posting_codes = lambda c: {k for k, a in c.items() if not a["is_group"] and a["is_posting"]}
        chart.chart_codes = lambda: {"4030", "5030"}
        saved = sys.modules.get("konsol.group_chart")
        sys.modules["konsol.group_chart"] = chart
        M.frappe.db = types.SimpleNamespace(sql=sql)
        M.frappe.throw = throw
        try:
            M.IntercompanyAccount._before_publish(_doc("Published", "Draft"))
            assert False, "the difference account was not refused"
        except _Refused as e:
            assert "CG-ZZGRP- books intercompany differences" in str(e)
        finally:
            if saved is None:
                sys.modules.pop("konsol.group_chart", None)
            else:
                sys.modules["konsol.group_chart"] = saved
        singles = [s for s in sent if "`tabSingles`" in s[0]]
        assert len(singles) == 1, singles


def test_the_singles_read_comes_after_the_doctype_lock():
    sent = []

    def sql(query, values=None, as_dict=False):
        sent.append((" ".join(query.split()), values))
        return []

    chart = types.ModuleType("group_chart_stub")
    chart.chart_accounts = lambda: {}
    chart.posting_codes = lambda c: set()
    chart.chart_codes = lambda: set()
    saved = sys.modules.get("konsol.group_chart")
    sys.modules["konsol.group_chart"] = chart
    M.frappe.db = types.SimpleNamespace(sql=sql)
    M.frappe.throw = lambda msg, *a, **k: (_ for _ in ()).throw(_Refused(msg))
    try:
        try:
            M.IntercompanyAccount._before_publish(_doc("Published", "Draft"))
        except Exception:
            pass
    finally:
        if saved is None:
            sys.modules.pop("konsol.group_chart", None)
        else:
            sys.modules["konsol.group_chart"] = saved
    lock_i = sent.index(("SELECT `name` FROM `tabDocType` WHERE `name` = %s FOR UPDATE",
                          ("Intercompany Account",)))
    singles_i = next(i for i, (q, _v) in enumerate(sent) if "`tabSingles`" in q)
    assert singles_i > lock_i
    assert sent[singles_i][0].endswith("FOR UPDATE")


def test_intercompany_account_does_not_import_from_konsol_close():
    """Five test files load this module under stubs that know nothing of
    konsol.close: test_consolidation_group_ic_difference.py,
    test_dimensions_reach_the_warehouse.py, test_group_chart.py,
    test_tb_bulk_model.py and this file. The literal is pinned against
    close_policy_model instead (the REJECT_REASON_FLAG precedent,
    consolidation_journal.py:25-29)."""
    with open(os.path.join(DT_DIR, "intercompany_account.py")) as f:
        src = f.read()
    assert "konsol.close" not in src


def test_the_intercompany_none_literal_matches_close_policy_model():
    spec = importlib.util.spec_from_file_location(
        "close_policy_model_under_test_c17",
        os.path.join(APP_DIR, "close", "close_policy_model.py"))
    cpm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cpm)
    assert M.INTERCOMPANY_NONE == cpm.INTERCOMPANY_NONE == "None in this group"
