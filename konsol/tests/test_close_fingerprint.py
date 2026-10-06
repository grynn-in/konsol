"""konsol#338 (#338-1, Deepak Pai 6 Oct 2026): the warehouse read, the stamp on
a new close run, and the build-completion check that voids a signature only
where the signed numbers changed.

``konsol/close/fingerprint.py`` is loaded by path against a stub frappe and a
stub ``signoff_gate``; ``ch_read`` is stubbed at its ``rows`` function, and
the real ``fingerprint_model`` is used. The SQL itself is proved live
against the local stack's ClickHouse (PR evidence): a stub cannot show that
ClickHouse parses it.
"""
import importlib.util
import os
import sys
import types
from datetime import datetime

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINGERPRINT_PY = os.path.join(APP_DIR, "close", "fingerprint.py")
MODEL_PY = os.path.join(APP_DIR, "close", "fingerprint_model.py")

BUILD_AT = datetime(2026, 10, 6, 9, 30, 0)


class _D(dict):
    def __getattr__(self, name):
        return self.get(name)


def _model():
    spec = importlib.util.spec_from_file_location("fingerprint_model_for_338", MODEL_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Site:
    """The stub world: warehouse rows, signed runs, writes made."""

    def __init__(self):
        self.ch_rows = []
        self.ch_calls = []
        self.ch_fail = None
        self.signed = {}           # (fy, fp) -> run dict
        self.marked = []           # (affected, affected_by, detail)
        self.set_values = []       # (doctype, name, values)
        self.errors = []
        self.commits = 0
        self.rollbacks = 0
        self.values = {("Pipeline Run", "PR-1", "error_log"): "earlier text",
                       ("Build Approval", "BAPR-1", "error_message"): None}


def _load(site):
    frappe = types.ModuleType("frappe")
    frappe._dict = _D

    def get_all(doctype, filters=None, fields=None, order_by=None, limit=None, **kw):
        assert doctype == "Pipeline Run", doctype
        assert filters == {"status": "Completed"}, filters
        return [_D(completed_at=BUILD_AT)]

    def get_value(doctype, name, field, *a, **k):
        return site.values.get((doctype, name, field))

    def set_value(doctype, name, values, *a, **k):
        site.set_values.append((doctype, name, dict(values)))

    def commit():
        site.commits += 1

    def rollback():
        site.rollbacks += 1

    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(get_value=get_value, set_value=set_value,
                                      commit=commit, rollback=rollback)
    frappe.utils = types.SimpleNamespace(now_datetime=lambda: BUILD_AT)
    frappe.log_error = lambda title=None, message=None, **k: site.errors.append((title, message))

    ch_read = types.ModuleType("konsol.close.ch_read")

    def rows(sql, params=None):
        site.ch_calls.append((sql, dict(params or {})))
        if site.ch_fail:
            raise RuntimeError(site.ch_fail)
        return [dict(r) for r in site.ch_rows]

    ch_read.rows = rows

    gate = types.ModuleType("konsol.close.signoff_gate")

    def latest_signed_runs(fields=()):
        return {k: _D(v) for k, v in site.signed.items()}

    def _mark_latest_signed(affected, affected_by, entity=None, detail=None):
        site.marked.append((set(affected), affected_by, detail))
        return [site.signed[k]["name"] for k in sorted(affected) if k in site.signed]

    gate.latest_signed_runs = latest_signed_runs
    gate._mark_latest_signed = _mark_latest_signed

    konsol = types.ModuleType("konsol")
    close = types.ModuleType("konsol.close")
    model = _model()
    close.ch_read, close.signoff_gate, close.fingerprint_model = ch_read, gate, model
    konsol.close = close
    stubs = {"frappe": frappe, "konsol": konsol, "konsol.close": close,
             "konsol.close.ch_read": ch_read, "konsol.close.signoff_gate": gate,
             "konsol.close.fingerprint_model": model}
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("fingerprint_under_test", FINGERPRINT_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return mod, model


def _ch(fp, amt, group="G1", fy=2025, entity="E1", account="1000", adj="entity", nulls=0):
    # ClickHouse's JSON quotes 64-bit integers (countIf is UInt64).
    return {"consolidation_group": group, "fiscal_year": fy, "fiscal_period": fp,
            "data_area_id": entity, "main_account": account, "adjustment_type": adj,
            "amt": amt, "null_rows": str(nulls)}


def _warehouse(site):
    site.ch_rows = [_ch(p, 100.0 * p) for p in range(1, 7)] + [_ch(3, 5.0, group="G2")]


def _model_rows(site):
    return [{"group": r["consolidation_group"], "fiscal_year": r["fiscal_year"],
             "fiscal_period": r["fiscal_period"], "entity": r["data_area_id"],
             "account": r["main_account"], "adjustment_type": r["adjustment_type"],
             "amount": r["amt"]} for r in site.ch_rows]


# --- the read ---------------------------------------------------------------

def test_the_read_binds_the_period_and_aggregates_the_decided_grain():
    site = _Site()
    _warehouse(site)
    mod, _ = _load(site)
    rows = mod.read_rows(2025, 6)
    sql, params = site.ch_calls[0]
    assert params == {"fy": 2025, "fp": 6}
    assert "epm_gold.gold_fully_consolidated_tb" in sql
    for column in ("consolidation_group", "fiscal_year", "fiscal_period", "data_area_id",
                   "main_account", "adjustment_type"):
        assert column in sql.split("GROUP BY")[1], column
    # The CYCLIC_ALIASES / ILLEGAL_AGGREGATION trap (statement_api._TB_SQL).
    assert "AS amount" not in sql and "AS amt" in sql
    assert rows[0] == {"group": "G1", "fiscal_year": 2025, "fiscal_period": 1, "entity": "E1",
                       "account": "1000", "adjustment_type": "entity", "amount": 100.0}


def test_a_key_with_a_null_amount_reads_as_null_so_the_model_refuses_it():
    site = _Site()
    site.ch_rows = [_ch(1, 10.0, nulls=1)]
    mod, _ = _load(site)
    assert mod.read_rows(2025, 1)[0]["amount"] is None
    try:
        mod.fingerprints([(2025, 1)])
    except ValueError as exc:
        assert "NULL" in str(exc)
    else:
        raise AssertionError("a NULL amount was fingerprinted")


def test_fingerprints_read_once_up_to_the_latest_period():
    site = _Site()
    _warehouse(site)
    mod, model = _load(site)
    got = mod.fingerprints([(2025, 2), (2025, 5), (2025, 3)])
    assert len(site.ch_calls) == 1 and site.ch_calls[0][1] == {"fy": 2025, "fp": 5}
    assert got == model.run_fingerprints(_model_rows(site), [(2025, 2), (2025, 3), (2025, 5)])
    assert mod.fingerprints([]) == {} and len(site.ch_calls) == 1


# --- the stamp on a new run -------------------------------------------------

def test_a_new_period_run_is_stamped_with_its_fingerprint_and_the_latest_build_time():
    site = _Site()
    _warehouse(site)
    mod, model = _load(site)
    doc = _D(fiscal_year=2025, fiscal_period=4)
    mod.stamp_new_run(doc)
    assert doc.numbers_fingerprint == model.run_fingerprint(_model_rows(site), 2025, 4)
    assert doc.fingerprint_as_of == BUILD_AT
    assert doc.fingerprint_error is None


def test_a_failed_read_is_recorded_on_the_run_never_a_blank_fingerprint_alone():
    site = _Site()
    site.ch_fail = "Code: 60. DB::Exception: (UNKNOWN_TABLE)"
    mod, _ = _load(site)
    doc = _D(fiscal_year=2025, fiscal_period=4)
    mod.stamp_new_run(doc)
    assert not doc.numbers_fingerprint
    assert "UNKNOWN_TABLE" in doc.fingerprint_error
    assert "cannot be signed" in doc.fingerprint_error


def test_a_year_only_run_is_not_stamped():
    site = _Site()
    mod, _ = _load(site)
    doc = _D(fiscal_year=2025, fiscal_period=0)
    mod.stamp_new_run(doc)
    assert site.ch_calls == [] and not doc.numbers_fingerprint and not doc.fingerprint_error


# --- the build-completion check --------------------------------------------

def _signed(site, model, *periods, stale=()):
    rows = _model_rows(site)
    for fp in periods:
        site.signed[(2025, fp)] = {"name": "RUN-%d" % fp, "fiscal_year": 2025,
                                    "fiscal_period": fp,
                                    "numbers_fingerprint": model.run_fingerprint(rows, 2025, fp)}
    for fp in stale:
        site.signed[(2025, fp)]["numbers_fingerprint"] = "v1:" + "0" * 64


def test_unchanged_numbers_void_nothing():
    site = _Site()
    _warehouse(site)
    mod, model = _load(site)
    _signed(site, model, 2, 4, 6)
    assert mod.check_after_build("PR-1", "BAPR-1") == []
    assert site.marked == [] and site.errors == [] and site.set_values == []


def test_only_the_changed_periods_are_voided_naming_the_build_and_both_fingerprints():
    site = _Site()
    _warehouse(site)
    mod, model = _load(site)
    _signed(site, model, 2, 4, 6)
    old = {k: v["numbers_fingerprint"] for k, v in site.signed.items()}
    site.ch_rows[3]["amt"] += 0.01      # P04 changes: P04 and P06 move, P02 does not
    marked = mod.check_after_build("PR-1", "BAPR-1")
    assert marked == ["RUN-4", "RUN-6"]
    assert [m[0] for m in site.marked] == [{(2025, 4)}, {(2025, 6)}]
    new = model.run_fingerprints(_model_rows(site), [(2025, 4), (2025, 6)])
    for (affected, affected_by, detail), key in zip(site.marked, [(2025, 4), (2025, 6)]):
        assert "Build Approval BAPR-1" in affected_by and "Pipeline Run PR-1" in affected_by
        assert detail["build_approval"] == "BAPR-1" and detail["pipeline_run"] == "PR-1"
        assert detail["old_fingerprint"] == old[key]
        assert detail["new_fingerprint"] == new[key]
    assert site.commits == 1 and site.rollbacks == 0


def test_an_orchestrator_run_is_named_without_a_build_approval():
    site = _Site()
    _warehouse(site)
    mod, model = _load(site)
    _signed(site, model, 3, stale=(3,))
    assert mod.check_after_build("PR-1") == ["RUN-3"]
    affected_by, detail = site.marked[0][1], site.marked[0][2]
    assert "Pipeline Run PR-1" in affected_by and "Build Approval" not in affected_by
    assert detail["build_approval"] is None


def test_a_signed_run_with_no_fingerprint_is_voided_not_passed():
    site = _Site()
    _warehouse(site)
    mod, model = _load(site)
    _signed(site, model, 3)
    site.signed[(2025, 3)]["numbers_fingerprint"] = None
    assert mod.check_after_build("PR-1") == ["RUN-3"]
    assert site.marked[0][2]["old_fingerprint"] is None


def test_no_signed_run_reads_nothing():
    site = _Site()
    _warehouse(site)
    mod, _ = _load(site)
    assert mod.check_after_build("PR-1", "BAPR-1") == []
    assert site.ch_calls == []


def test_a_failed_read_voids_nothing_and_says_so_on_the_build():
    site = _Site()
    _warehouse(site)
    mod, model = _load(site)
    _signed(site, model, 4)
    site.ch_fail = "Code: 60. DB::Exception: (UNKNOWN_TABLE)"
    assert mod.check_after_build("PR-1", "BAPR-1") is None
    assert site.marked == []
    assert site.rollbacks == 1 and site.commits == 1
    written = {(d, n): v for d, n, v in site.set_values}
    log = written[("Pipeline Run", "PR-1")]["error_log"]
    assert log.startswith("earlier text\n")
    for text in (mod.SIGNATURE_CHECK_FAILED, "UNKNOWN_TABLE", "No signature was voided"):
        assert text in log, text
        assert text in written[("Build Approval", "BAPR-1")]["error_message"], text
    assert site.errors and mod.SIGNATURE_CHECK_FAILED in site.errors[0][0]
