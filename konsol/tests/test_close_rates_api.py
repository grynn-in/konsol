"""Rates API: konsol/close/rates_api.py ``get_rates`` (konsol#305 E403;
stories 4.1, 4.3; #305-W2-3, W2-10, W2-14; D2-9).

``get_rates(fiscal_year, fiscal_period)`` (GET) returns the period's rate
grid: the required pairs from the warehouse, the drafts and approved rates,
the previous approved rate per key, the move flags under the declared
threshold, the approve mode on each awaiting cell (with the draft's
preparers), the policy gaps, and whether the caller may enter or approve.

Loaded against a stub frappe (pattern: test_close_checks_api.py ``_Site`` /
``_call``, copied, not imported). ``konsol.group_rates`` and
``konsol.fiscal_calendar`` are stub modules: ``translation_gaps`` is how the
ClickHouse read is stubbed, so no ClickHouse client is imported. The real
``close_policy_model.py``, ``rates_model.py`` and ``self_approval.py`` are
loaded by path under their dotted names, so the approve rule and the preparer
rule under test are the product's.
"""
import decimal
import importlib.util
import json
import os
import re
import sys
import types
from datetime import date, datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
API_PY = os.path.join(CLOSE_DIR, "rates_api.py")
TIMEFMT_PY = os.path.join(CLOSE_DIR, "timefmt.py")
OWNERSHIP_CHANGE_PY = os.path.join(CLOSE_DIR, "ownership_change.py")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
CLOSE_DIR_R52F = CLOSE_DIR


#: R52f: the REAL signoff_gate.py, so the rule the preview lists by is the
#: rule record_data_change marks by (never a re-typed copy of it).
SIGNOFF_GATE_PY = os.path.join(CLOSE_DIR_R52F, "signoff_gate.py")
SIGNOFF_MODEL_PY_R52F = os.path.join(CLOSE_DIR_R52F, "signoff_model.py")


def _real_periods_marked_from(rows, first_close, reads=None):
    """``signoff_gate.periods_marked_from`` from the REAL signoff_gate.py,
    loaded by path against a minimal stub frappe: the period row's type
    comes from ``rows()`` (the same calendar the code under test reads), the
    first close is ``first_close`` ((fy, fp) or None = undeclared, read as
    the Close Settings Ints 0/0) and the real pure ``signoff_model``.
    ``reads``, when given, records each gate read as ("gate", what)."""
    log = reads.append if reads is not None else (lambda item: None)
    fy0, fp0 = first_close or (0, 0)

    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})

    def throw(msg, exc=None, title=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    def get_value(doctype, filters, fieldname, as_dict=False, **k):
        assert doctype == "EPM Fiscal Year Period", doctype
        log(("gate", "period_row"))
        key = (int(filters["parent"]), int(filters["fiscal_period"]))
        row = next((r for r in rows() if (int(r["fiscal_year"]), int(r["fiscal_period"])) == key),
                   None)
        if row is None:
            return None
        rec = {"name": "ROW-%d-%d" % key, "period_type": row["period_type"]}
        return {f: rec.get(f) for f in fieldname}

    def get_single_value(doctype, field):
        assert doctype == "Close Settings", doctype
        log(("gate", field))
        return {"first_close_fiscal_year": fy0, "first_close_fiscal_period": fp0}[field]

    frappe.throw = throw
    frappe.db = types.SimpleNamespace(get_value=get_value, get_single_value=get_single_value)

    konsol = types.ModuleType("konsol")
    calendar = types.ModuleType("konsol.fiscal_calendar")

    def fiscal_period_rows():
        log(("gate", "calendar"))
        return [dict(r) for r in rows()]

    calendar.fiscal_period_rows = fiscal_period_rows
    close = types.ModuleType("konsol.close")
    spec = importlib.util.spec_from_file_location("signoff_model_for_r52f", SIGNOFF_MODEL_PY_R52F)
    signoff_model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(signoff_model)
    for name in ("close_policy_model", "ic_api", "period_model", "scope_model",
                 "statement_model"):
        setattr(close, name, types.ModuleType("konsol.close." + name))
    close.signoff_model = signoff_model
    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    konsol.fiscal_calendar, konsol.close, konsol.period_status = calendar, close, period_status
    mods = {"frappe": frappe, "konsol": konsol, "konsol.fiscal_calendar": calendar,
            "konsol.close": close, "konsol.period_status": period_status}
    for name in ("close_policy_model", "ic_api", "period_model", "scope_model",
                 "statement_model", "signoff_model"):
        mods["konsol.close." + name] = getattr(close, name)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("signoff_gate_for_r52f", SIGNOFF_GATE_PY)
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old

    def periods_marked_from(fiscal_year, fiscal_period):
        # Looked up at call time: a gate without the function fails the
        # caller that asks for it, not every stub site that is built.
        return gate.periods_marked_from(fiscal_year, fiscal_period)

    return periods_marked_from

#: The stub site's system time zone (A55, L01e): BST (+01:00) in July 2025.
SITE_TZ = "Europe/London"

RATES_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")
LEAD = "zz-lead@example.com"
ANALYST = "zz-analyst@example.com"
VIEWER = "zz-viewer@example.com"


def _period(fy, fp, status="Open"):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_label": "P%02d" % fp, "period_type": "Regular",
            "start_date": date(fy, fp, 1), "end_date": date(fy, fp, 28),
            "quarter": "Q%d" % ((fp - 1) // 3 + 1), "status": status}


def _ger(name, frm, to, rate_type, quote, docstatus, owner=ANALYST, quoted_per="1",
         erp_quote=None, modified=None, change_reason=None, source="Manual", supersedes=None):
    return {"name": name, "from_currency": frm, "to_currency": to, "rate_type": rate_type,
            "quote": quote, "quoted_per": quoted_per, "erp_quote": erp_quote,
            "docstatus": docstatus, "owner": owner,
            "modified": modified or datetime(2025, 7, 31, 10, 0, 0),
            "change_reason": change_reason, "source": source,
            "fiscal_year": 2025, "fiscal_period": 7, "supersedes": supersedes}


def _earlier(name, frm, to, rate_type, quote, fy, fp, quoted_per="1"):
    return {"name": name, "from_currency": frm, "to_currency": to, "rate_type": rate_type,
            "quote": quote, "quoted_per": quoted_per, "fiscal_year": fy, "fiscal_period": fp,
            "docstatus": 1}


class _Site:
    def __init__(self):
        self.user = LEAD
        self.roles = {"EPM Admin"}
        self.periods = [_period(2025, 6, "Closed"), _period(2025, 7, "Open")]
        self.needs = ({("EUR", "GBP"), ("USD", "GBP")}, [], [])
        self.needs_error = None
        self.threshold = 0.5
        self.policy = "Blocked"
        self.workflow_state_field = None
        self.docs = [
            _ger("GER-1", "EUR", "GBP", "Closing", 0.85, 1),
            _ger("GER-2", "EUR", "GBP", "Average", 0.84, 0, owner=ANALYST),
        ]
        self.earlier = [
            _earlier("GER-0", "EUR", "GBP", "Closing", 0.80, 2025, 6),
            _earlier("GER-00", "EUR", "GBP", "Average", 0.81, 2025, 6),
        ]
        self.versions = []  # {"ref_doctype", "docname", "owner", "data"}
        self.her = []  # Historical Equity Rate drafts (E405)
        self.ops = []  # Ownership Period rows: drafts (E405) and submitted (E406 scope query)
        self.entities = []  # Active leaf entities (E406): {"name"}
        self.tbs = []  # Trial Balance Submission rows (E406): {"data_area_id", "fiscal_year", "fiscal_period", "docstatus"}
        self.can_record = True  # frappe.has_permission("Ownership Period", "create") (E406)
        self.allowed = None  # allowed_entity_codes(): None = unrestricted (E405, W2-10)
        self.quoted_per_options = "1\n10\n100\n1000\n10000"  # E409b: Group Exchange Rate meta
        self.move_answer = None
        self.move_calls = []
        self.needs_calls = []
        self.only_for_calls = []
        self.reads = []  # one entry per MariaDB read
        self.named = {}  # name -> _FakeDoc, for get_doc(doctype, name)
        self.get_doc_calls = []  # what get_doc received
        self.get_doc_kwargs = []  # FR1: the keyword arguments of each get_doc(doctype, name)
        self.deleted = []  # FR1: each frappe.delete_doc call, as (args, kwargs)
        self.locking_values = []  # FR3: the values of each locking read of a key's rates
        self.new_docs = []  # every _FakeDoc built from a dict
        self.signed = []  # signoff_gate.latest_signed_runs() keys (O55)
        #: R52f: the declared first close (fy, fp), None = undeclared, read by
        #: the REAL signoff_gate.periods_marked_from; its reads go to gate_reads
        #: so the MariaDB read log above keeps its exact shape.
        self.first_close = None
        self.gate_reads = []
        #: O64: {(fy, fp): (signed_off_by, signed_off_at)} of a signed run;
        #: unlisted keys are signed by LEAD at O64_SIGNED_AT.
        self.signers = {}
        self.users = {LEAD: O64_LEAD_NAME}  # User full names (O64)


#: O64: who signed the stub's signed runs, and when.
O64_LEAD_NAME = "Zz Lead"
O64_SIGNED_AT = datetime(2025, 10, 4, 17, 30, 0)


def _signed_runs(site, fields=()):
    """The stub ``signoff_gate.latest_signed_runs(fields)``: one run per
    signed key, with the requested columns, as the real one returns them."""
    out = {}
    for fy, fp in site.signed:
        by, at = site.signers.get((fy, fp), (LEAD, O64_SIGNED_AT))
        row = {"name": "ZZ-RUN-%d-%d" % (fy, fp), "fiscal_year": fy, "fiscal_period": fp,
               "signed_off_by": by, "signed_off_at": at}
        out[(fy, fp)] = {f: row[f] for f in ("name", "fiscal_year", "fiscal_period")
                         + tuple(fields)}
    return out


def _o64_signatures(site, keys=None):
    """The signatures ``ownership_change.context`` should hand the model for
    this site: written out independently of the code under test. ``keys``
    (R52f): only those signed keys, the ones the approval will mark."""
    out = {}
    for fy, fp in sorted(site.signed):
        if keys is not None and (fy, fp) not in keys:
            continue
        by, at = site.signers.get((fy, fp), (LEAD, O64_SIGNED_AT))
        out[(fy, fp)] = {"run": "ZZ-RUN-%d-%d" % (fy, fp), "signed_on": at.date().isoformat(),
                         "signed_by_name": site.users.get(by) or None}
    return out


def _her(name, data_area_id="ZZA", group="CG1", account="4000", rate_date=None,
         historical_rate=1.2, owner=ANALYST, creation=None, docstatus=0):
    return {"name": name, "consolidation_group": group, "data_area_id": data_area_id,
            "main_account": account, "rate_date": rate_date or date(2024, 1, 1),
            "historical_rate": historical_rate, "owner": owner,
            "creation": creation or datetime(2025, 7, 1, 9, 0, 0), "docstatus": docstatus}


def _op(name, data_area_id="ZZA", group="CG1", effective_date=None, end_date=None,
        ownership_pct=60.0, consolidation_method="Equity", owner=ANALYST, creation=None,
        docstatus=0):
    return {"name": name, "consolidation_group": group, "data_area_id": data_area_id,
            "effective_date": effective_date or date(2025, 1, 1), "end_date": end_date,
            "ownership_pct": ownership_pct, "consolidation_method": consolidation_method,
            "owner": owner, "creation": creation or datetime(2025, 7, 2, 9, 0, 0),
            "docstatus": docstatus}


def _entity(name, status="Active", is_group=0):
    return {"name": name, "status": status, "is_group": is_group}


def _tb(data_area_id, fy=2025, fp=7, docstatus=1):
    return {"data_area_id": data_area_id, "fiscal_year": fy, "fiscal_period": fp,
            "docstatus": docstatus}


def _match_value(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        if op == "is":
            assert arg in ("set", "not set"), cond
            return (value not in (None, "")) == (arg == "set")
        if op == "<=":
            return value is not None and value <= arg
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


def _match(row, filters):
    return all(_match_value(row.get(key), cond) for key, cond in (filters or {}).items())


def _frappe(site):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    def only_for(roles, message=False):
        roles = [roles] if isinstance(roles, str) else list(roles)
        site.only_for_calls.append(tuple(roles))
        if not site.roles.intersection(roles):
            raise frappe.PermissionError("Not permitted")

    def whitelist(*a, **k):
        return lambda fn: fn

    def get_all(doctype, filters=None, fields=None, order_by=None, limit=None,
                limit_page_length=None, pluck=None, **k):
        site.reads.append(("get_all", doctype))
        if doctype == "Group Exchange Rate":
            rows = [r for r in site.docs if _match(r, filters)]
        elif doctype == "Version":
            rows = [r for r in site.versions if _match(r, filters)]
        elif doctype == "Historical Equity Rate":
            rows = [r for r in site.her if _match(r, filters)]
        elif doctype == "Ownership Period":
            rows = [r for r in site.ops if _match(r, filters)]
        elif doctype == "Entity":
            rows = [r for r in site.entities if _match(r, filters)]
        elif doctype == "Trial Balance Submission":
            rows = [r for r in site.tbs if _match(r, filters)]
        elif doctype == "User":  # O64: the signers' full names
            rows = [{"name": n, "full_name": f} for n, f in sorted(site.users.items())
                    if _match({"name": n}, filters)]
        else:
            raise AssertionError("unexpected get_all on %s" % doctype)
        if pluck:
            return [r.get(pluck) for r in rows]
        return [{f: r.get(f) for f in fields} for r in rows]

    def sql(query, values=None, as_dict=False, **k):
        site.reads.append(("sql", query))
        assert "tabGroup Exchange Rate" in query, query
        if "FOR UPDATE" in query:
            # FR3: save_rate's locking read of the key's draft and approved
            # rows, answered from site.docs on the five grain values.
            assert as_dict, "the key's rates are read as_dict"
            site.locking_values.append(tuple(values))
            to, frm, fy, fp, rate_type = values
            grain = {"to_currency": to, "from_currency": frm, "fiscal_year": fy,
                     "fiscal_period": fp, "rate_type": rate_type}
            return [{"name": r["name"], "docstatus": r["docstatus"],
                     "supersedes": r.get("supersedes")}
                    for r in site.docs if _match(r, grain) and r["docstatus"] in (0, 1)]
        assert "docstatus = 1" in query, query
        assert as_dict, "the earlier approved rates are read as_dict"
        fy, fy2, fp = values
        assert fy == fy2
        return [dict(r) for r in site.earlier
                if r["docstatus"] == 1 and (r["fiscal_year"] < fy
                                            or (r["fiscal_year"] == fy and r["fiscal_period"] < fp))]

    def get_single_value(doctype, field, **k):
        site.reads.append(("single", doctype, field))
        assert (doctype, field) == ("Close Settings", "self_approval"), (doctype, field)
        return site.policy

    def get_value(doctype, filters, fieldname=None, **k):
        site.reads.append(("get_value", doctype))
        assert doctype == "Workflow", doctype
        return site.workflow_state_field

    def get_doc(arg, name=None, **k):
        site.get_doc_calls.append(dict(arg) if isinstance(arg, dict) else (arg, name))
        if isinstance(arg, dict):
            doc = _FakeDoc(dict(arg), new=True)
            site.new_docs.append(doc)
            return doc
        site.reads.append(("get_doc", arg))
        site.get_doc_kwargs.append(dict(k))
        assert arg in ("Group Exchange Rate", "Ownership Period"), arg
        if name not in site.named:
            raise frappe.ValidationError("%s %s not found" % (arg, name))
        return site.named[name]

    def has_permission(doctype, ptype="read", *a, **k):
        assert (doctype, ptype) == ("Ownership Period", "create"), (doctype, ptype)
        return site.can_record

    def get_meta(doctype):
        site.reads.append(("get_meta", doctype))
        assert doctype == "Group Exchange Rate", doctype
        fields = {"quoted_per": types.SimpleNamespace(options=site.quoted_per_options)}
        return types.SimpleNamespace(get_field=lambda f: fields[f])

    def exists(doctype, name=None, *a, **k):
        site.reads.append(("exists", doctype))
        assert doctype == "Group Exchange Rate", doctype
        assert isinstance(name, str), name  # FR1: the name is a string before it is looked up
        return name if name in site.named else None

    def delete_doc(*a, **k):
        site.deleted.append((a, k))

    frappe.get_doc = get_doc
    frappe.delete_doc = delete_doc
    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe.get_meta = get_meta
    frappe.has_permission = has_permission
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.flags = {}
    frappe.db = types.SimpleNamespace(sql=sql, get_single_value=get_single_value,
                                      get_value=get_value, exists=exists)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.utils = types.SimpleNamespace(get_system_timezone=lambda: SITE_TZ)
    return frappe


class _FakeDoc:
    """A recording Group Exchange Rate: insert()/save() are counted, and run
    what the controller would set (source Manual, a label) without deciding
    anything for the endpoint."""

    def __init__(self, data, new=False):
        self.__dict__["_data"] = dict(data)
        self.__dict__["flags"] = types.SimpleNamespace()
        self.__dict__["calls"] = []
        self.__dict__["_new"] = new

    def __getattr__(self, key):
        try:
            return self.__dict__["_data"][key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        self.__dict__["_data"][key] = value

    def get(self, key, default=None):
        return self._data.get(key, default)

    def insert(self, *a, **k):
        self.calls.append(("insert", a, k))
        self._data.setdefault(
            "name", "OP-NEW" if self._data.get("doctype") == "Ownership Period" else "GER-NEW")
        self._data.setdefault("docstatus", 0)
        if not self._data.get("source"):
            self._data["source"] = "Manual"
        self._data["quote_label"] = "%s %s per %s %s" % (
            self._data.get("quote"), self._data.get("to_currency"),
            self._data.get("quoted_per"), self._data.get("from_currency"))
        return self

    def save(self, *a, **k):
        self.calls.append(("save", a, k))
        self._data["quote_label"] = "%s %s per %s %s" % (
            self._data.get("quote"), self._data.get("to_currency"),
            self._data.get("quoted_per"), self._data.get("from_currency"))
        return self

    def submit(self, *a, **k):
        raise AssertionError("save_rate must never submit")


def _group_rates(site):
    gr = types.ModuleType("konsol.group_rates")
    gr.RATE_TYPES = ("Closing", "Average")
    gr.PREFILL_ROLES = ("EPM Analyst", "EPM Admin", "System Manager")
    gr.true_rate = lambda q, p: float(decimal.Decimal(str(q or 0)) / int(p or 1))
    gr.ch_error_names = lambda e: set()

    def translation_gaps(fy, fp):
        # (pairs, groups with no reporting currency, entities with no functional currency)
        site.needs_calls.append((fy, fp))
        if site.needs_error is not None:
            raise site.needs_error
        return site.needs

    def move_threshold():
        site.reads.append(("single", "Close Settings", "rate_move_threshold"))
        return site.threshold

    def move_problem(rate, previous=None, erp_rate=None, unit="", threshold=None):
        site.move_calls.append({"rate": rate, "previous": previous, "erp_rate": erp_rate,
                                "unit": unit, "threshold": threshold})
        return site.move_answer

    gr.translation_gaps = translation_gaps
    gr.move_threshold = move_threshold
    gr.move_problem = move_problem
    return gr


def _fiscal_calendar(site):
    fc = types.ModuleType("konsol.fiscal_calendar")

    def fiscal_period_rows():
        site.reads.append(("sql", "fiscal_period_rows"))
        return [dict(r) for r in site.periods]

    fc.fiscal_period_rows = fiscal_period_rows
    return fc


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _call(site, fy=2025, fp=7):
    return _invoke(site, lambda api: api.get_rates(fy, fp))


def _load_rates_api(site):
    """The loaded ``rates_api`` module itself (for unit-testing ``_iso``
    directly), not a JSON-safe endpoint result."""
    return _invoke(site, lambda api: api, require_json_safe=False)


def _invoke(site, run, require_json_safe=True):
    frappe = _frappe(site)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    group_rates = _group_rates(site)
    fiscal_calendar = _fiscal_calendar(site)
    entity_permissions = types.ModuleType("konsol.entity_permissions")
    entity_permissions.allowed_entity_codes = lambda user=None: site.allowed
    konsol.close = close
    konsol.group_rates = group_rates
    konsol.fiscal_calendar = fiscal_calendar
    konsol.entity_permissions = entity_permissions
    names = ["frappe", "konsol", "konsol.close", "konsol.group_rates", "konsol.fiscal_calendar",
             "konsol.entity_permissions", "konsol.close.close_policy_model",
             "konsol.close.rates_model", "konsol.close.self_approval", "konsol.close.scope_model",
             "konsol.close.timefmt", "konsol.close.signoff_gate",
             "konsol.close.ownership_change", "close_rates_api_under_test"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"frappe": frappe, "konsol": konsol, "konsol.close": close,
                        "konsol.group_rates": group_rates,
                        "konsol.fiscal_calendar": fiscal_calendar,
                        "konsol.entity_permissions": entity_permissions})
    try:
        close.close_policy_model = _load_path(
            "konsol.close.close_policy_model", os.path.join(CLOSE_DIR, "close_policy_model.py"))
        close.rates_model = _load_path(
            "konsol.close.rates_model", os.path.join(CLOSE_DIR, "rates_model.py"))
        close.self_approval = _load_path(
            "konsol.close.self_approval", os.path.join(CLOSE_DIR, "self_approval.py"))
        close.scope_model = _load_path(
            "konsol.close.scope_model", os.path.join(CLOSE_DIR, "scope_model.py"))
        close.timefmt = _load_path("konsol.close.timefmt", TIMEFMT_PY)
        # O55: the REAL ownership_change.py (and through it the real
        # ownership_change_model.py), reading this stub site; only
        # signoff_gate's signed runs are stubbed.
        signoff_gate = types.ModuleType("konsol.close.signoff_gate")
        signoff_gate.latest_signed_runs = lambda fields=(): _signed_runs(site, fields)
        # R52f: the REAL rule, on this site's calendar and first close.
        signoff_gate.periods_marked_from = _real_periods_marked_from(
            lambda: site.periods, site.first_close, site.gate_reads)
        sys.modules["konsol.close.signoff_gate"] = signoff_gate
        close.signoff_gate = signoff_gate
        close.ownership_change = _load_path(
            "konsol.close.ownership_change", OWNERSHIP_CHANGE_PY)
        api = _load_path("close_rates_api_under_test", API_PY)
        site.frappe = frappe
        result = run(api)
        if require_json_safe:
            json.dumps(result)  # JSON-safe
        return result
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _call_raises(site, fy=2025, fp=7):
    with pytest.raises(Exception) as info:
        _call(site, fy, fp)
    return info.value


def _call_pending(site):
    return _invoke(site, lambda api: api.get_pending())


def _call_pending_raises(site):
    with pytest.raises(Exception) as info:
        _call_pending(site)
    return info.value


def _call_ownership(site, fy=2025, fp=7):
    return _invoke(site, lambda api: api.get_ownership(fy, fp))


def _call_ownership_raises(site, fy=2025, fp=7):
    with pytest.raises(Exception) as info:
        _call_ownership(site, fy, fp)
    return info.value


def _row(result, frm, to="GBP"):
    rows = [r for r in result["rows"] if (r["from_currency"], r["to_currency"]) == (frm, to)]
    assert len(rows) == 1, result["rows"]
    return rows[0]


def _awaiting(result):
    cells = []
    for row in result["rows"] + result["unrequired"]:
        for key in ("closing", "average"):
            if row[key]["status"] == "awaiting_approval":
                cells.append(row[key])
    return cells


# --- the grid -------------------------------------------------------------------

def test_the_close_lead_sees_two_rows_and_approves_another_users_draft_directly():
    site = _Site()
    result = _call(site)
    assert site.only_for_calls == [RATES_ROLES]
    assert len(result["rows"]) == 2
    eur = _row(result, "EUR")
    assert eur["required"] is True
    assert eur["closing"]["status"] == "approved"
    assert eur["average"]["status"] == "awaiting_approval"
    assert eur["average"]["owner"] == ANALYST
    assert eur["average"]["approve"]["mode"] == "direct"
    assert eur["average"]["edited_by"] == []
    usd = _row(result, "USD")
    assert usd["closing"]["status"] == "missing" and usd["average"]["status"] == "missing"
    assert result["summary"] == {"missing": 2, "awaiting_approval": 1, "approved": 1}
    assert result["can_approve"] is True and result["can_enter"] is True
    assert result["period"] == {"fiscal_year": 2025, "fiscal_period": 7, "period_code": "P07",
                                "status": "Open"}
    assert result["pairs_error"] is None and result["blockers"] == []
    assert result["threshold_pct"] == 50.0
    assert result["self_approval"] == "Blocked"
    assert result["policy_gaps"] == []
    assert site.needs_calls == [(2025, 7)]


def test_the_previous_approved_rate_and_the_true_rate_reach_the_cell():
    site = _Site()
    site.docs = [_ger("GER-1", "EUR", "GBP", "Closing", 88, 1, quoted_per="100",
                      erp_quote=87)]
    site.earlier = [_earlier("GER-A", "EUR", "GBP", "Closing", 0.80, 2025, 6),
                    _earlier("GER-B", "EUR", "GBP", "Closing", 0.70, 2025, 3),
                    _earlier("GER-C", "EUR", "GBP", "Closing", 0.60, 2024, 12)]
    result = _call(site)
    cell = _row(result, "EUR")["closing"]
    assert cell["rate"] == pytest.approx(0.88)
    assert cell["previous"]["name"] == "GER-A"
    assert cell["previous"]["label"] == "FY2025 P6 (GER-A)"
    assert cell["delta"] == pytest.approx(0.88 / 0.80 - 1, abs=1e-12)
    closing_calls = [c for c in site.move_calls if c["rate"] == pytest.approx(0.88)]
    assert closing_calls[0]["erp_rate"] == pytest.approx(0.87)
    assert closing_calls[0]["unit"] == "GBP per EUR"
    assert closing_calls[0]["threshold"] == 0.5


def test_failure_path_self_approval_under_blocked_is_refused():
    site = _Site()
    site.docs = [_ger("GER-2", "EUR", "GBP", "Average", 0.84, 0, owner=LEAD)]
    result = _call(site)
    cell = _row(result, "EUR")["average"]
    assert cell["approve"]["mode"] == "refused"
    assert "blocks self-approval" in cell["approve"]["message"]


def test_failure_path_approve_by_an_analyst_is_refused():
    site = _Site()
    site.user = ANALYST
    site.roles = {"EPM Analyst"}
    result = _call(site)
    cell = _row(result, "EUR")["average"]
    assert cell["approve"]["mode"] == "not_approver"
    assert result["can_approve"] is False
    assert result["can_enter"] is True


def test_failure_path_undeclared_threshold_is_a_gap_never_a_default():
    site = _Site()
    site.threshold = None
    result = _call(site)
    assert result["threshold_pct"] is None
    assert "rate_move_undeclared" in [g["code"] for g in result["policy_gaps"]]
    assert site.move_calls, "move_problem was never called"
    assert all(c["threshold"] is None for c in site.move_calls), site.move_calls


def test_failure_path_undeclared_self_approval_is_a_gap():
    site = _Site()
    site.policy = None
    result = _call(site)
    assert result["self_approval"] is None
    assert "self_approval_undeclared" in [g["code"] for g in result["policy_gaps"]]


def test_failure_path_clickhouse_down_lists_the_docs_with_required_none():
    site = _Site()
    site.needs_error = RuntimeError("connection refused")
    site.docs.append(_ger("GER-9", "JPY", "GBP", "Closing", 0.005, 0))
    result = _call(site)
    assert result["pairs_error"].startswith("RuntimeError")
    pairs = {(r["from_currency"], r["to_currency"]): r["required"] for r in result["rows"]}
    assert pairs == {("EUR", "GBP"): None, ("JPY", "GBP"): None}
    assert result["unrequired"] == []
    assert result["blockers"] == []


def test_a_group_with_no_reporting_currency_is_a_blocker():
    site = _Site()
    site.needs = ({("EUR", "GBP")}, ["ZZGRP"], [])
    result = _call(site)
    assert result["blockers"] == ["Consolidation Group ZZGRP has no reporting currency"]


def test_an_entity_with_no_functional_currency_is_a_blocker_after_the_groups():
    """konsol#311 (F13): the Rates screen names what group_rates.rate_gate names,
    in the same words and order: the groups, then the entities."""
    site = _Site()
    site.needs = ({("EUR", "GBP")}, ["ZZGRP"], ["ZZ_NOFC"])
    result = _call(site)
    assert result["blockers"] == ["Consolidation Group ZZGRP has no reporting currency",
                                  "Entity ZZ_NOFC has no functional currency"], result["blockers"]
    assert site.needs_calls == [(2025, 7)]


def test_an_entity_with_no_functional_currency_alone_is_a_blocker():
    site = _Site()
    site.needs = ({("EUR", "GBP")}, [], ["ZZ_NOFC", "ZZ_NOFC2"])
    result = _call(site)
    assert result["blockers"] == ["Entity ZZ_NOFC has no functional currency",
                                  "Entity ZZ_NOFC2 has no functional currency"], result["blockers"]


def test_a_rate_for_an_unrequired_pair_is_listed_apart():
    site = _Site()
    site.docs.append(_ger("GER-9", "JPY", "GBP", "Closing", 0.005, 0))
    result = _call(site)
    assert [(r["from_currency"], r["required"]) for r in result["unrequired"]] == [("JPY", False)]
    assert result["unrequired"][0]["closing"]["approve"]["mode"] == "direct"


def test_failure_path_an_undeclared_period_is_refused():
    site = _Site()
    err = _call_raises(site, 2031, 9)
    assert "not declared" in str(err), err
    assert "FY2031 P09" in str(err), err
    assert site.needs_calls == []


def test_a_closed_period_cannot_be_entered():
    site = _Site()
    result = _call(site, 2025, 6)
    assert result["period"]["status"] == "Closed"
    assert result["can_enter"] is False
    assert result["can_approve"] is True


def test_string_arguments_are_cast_to_int():
    site = _Site()
    result = _call(site, "2025", "7")
    assert result["period"]["fiscal_year"] == 2025 and result["period"]["fiscal_period"] == 7


# --- roles ------------------------------------------------------------------------

def test_failure_path_the_entity_accountant_is_refused():
    site = _Site()
    site.roles = {"Entity Accountant"}
    err = _call_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.only_for_calls == [RATES_ROLES]
    assert site.reads == [] and site.needs_calls == []


def test_a_viewer_reads_but_can_neither_enter_nor_approve():
    site = _Site()
    site.user = VIEWER
    site.roles = {"EPM User"}
    result = _call(site)
    assert result["can_enter"] is False
    assert result["can_approve"] is False
    cells = _awaiting(result)
    assert cells and all(c["approve"]["mode"] == "not_approver" for c in cells)


# --- preparers (#305-W2-14) ---------------------------------------------------------

def test_failure_path_a_lead_who_edited_the_analysts_draft_is_refused_under_blocked():
    site = _Site()
    site.versions = [{"ref_doctype": "Group Exchange Rate", "docname": "GER-2", "owner": LEAD,
                      "data": json.dumps({"changed": [["quote", 0.83, 0.84]]})}]
    result = _call(site)
    cell = _row(result, "EUR")["average"]
    assert cell["owner"] == ANALYST
    assert cell["approve"]["mode"] == "refused"
    assert cell["edited_by"] == [LEAD]


def test_the_same_edit_under_allowed_with_reason_needs_a_reason():
    site = _Site()
    site.policy = "Allowed with reason"
    site.versions = [{"ref_doctype": "Group Exchange Rate", "docname": "GER-2", "owner": LEAD,
                      "data": json.dumps({"changed": [["quote", 0.83, 0.84]]})}]
    result = _call(site)
    cell = _row(result, "EUR")["average"]
    assert cell["approve"]["mode"] == "reason"
    assert "approval_api.approve" in cell["approve"]["message"]


def test_a_version_of_another_document_does_not_make_a_preparer():
    site = _Site()
    site.versions = [{"ref_doctype": "Group Exchange Rate", "docname": "GER-OTHER", "owner": LEAD,
                      "data": json.dumps({"changed": [["quote", 0.83, 0.84]]})}]
    result = _call(site)
    cell = _row(result, "EUR")["average"]
    assert cell["approve"]["mode"] == "direct"
    assert cell["edited_by"] == []


def test_an_approved_or_missing_cell_carries_no_approve_mode():
    site = _Site()
    result = _call(site)
    eur = _row(result, "EUR")
    assert eur["closing"]["approve"] is None and eur["closing"]["edited_by"] is None
    usd = _row(result, "USD")
    assert usd["closing"]["approve"] is None


# --- query count ----------------------------------------------------------------------

def _pairs_site(n):
    currencies = ["EUR", "USD", "JPY", "CHF", "SEK", "NOK"][:n]
    site = _Site()
    site.needs = ({(c, "GBP") for c in currencies}, [], [])
    site.docs = []
    site.earlier = []
    for i, c in enumerate(currencies):
        site.docs.append(_ger("GER-C%d" % i, c, "GBP", "Closing", 1.1, 0))
        site.docs.append(_ger("GER-A%d" % i, c, "GBP", "Average", 1.0, 1))
        site.earlier.append(_earlier("GER-E%d" % i, c, "GBP", "Closing", 1.0, 2025, 6))
    return site


def test_the_query_count_is_constant_in_the_number_of_pairs():
    one, six = _pairs_site(1), _pairs_site(6)
    r1, r6 = _call(one), _call(six)
    assert len(r1["rows"]) == 1 and len(r6["rows"]) == 6
    assert len(one.reads) == 8, one.reads  # 7 + 1 get_meta for quoted_per_options (E409b)
    assert len(six.reads) == 8, six.reads
    assert one.needs_calls == [(2025, 7)] and six.needs_calls == [(2025, 7)]
    versions = [r for r in six.reads if r == ("get_all", "Version")]
    assert len(versions) == 1


# --- quoted_per_options (E409b): the one source of truth is the doctype meta ---------

def test_get_rates_sends_quoted_per_options_from_the_group_exchange_rate_meta():
    site = _Site()
    result = _call(site)
    assert result["quoted_per_options"] == ["1", "10", "100", "1000", "10000"]


def test_a_changed_meta_changes_the_quoted_per_options_payload():
    site = _Site()
    site.quoted_per_options = "5\n\n50\n"
    result = _call(site)
    assert result["quoted_per_options"] == ["5", "50"]


# --- save_rate (E404): the Analyst saves a draft; nothing else can be set -----------

SAVE_PARAMS = ["fiscal_year", "fiscal_period", "from_currency", "to_currency", "rate_type",
               "quote", "quoted_per", "change_reason", "name", "supersedes"]
NEW_KEYS = {"doctype", "to_currency", "from_currency", "rate_type", "fiscal_year",
            "fiscal_period", "quote", "quoted_per", "change_reason"}
FORGED = ("docstatus", "source", "erp_quote", "source_note", "owner", "amended_from")


def _analyst_site():
    site = _Site()
    site.user = ANALYST
    site.roles = {"EPM Analyst"}
    return site


def _save(site, **kw):
    args = {"fiscal_year": 2025, "fiscal_period": 7, "from_currency": "USD",
            "to_currency": "GBP", "rate_type": "Closing", "quote": 0.79, "quoted_per": "1"}
    args.update(kw)
    return _invoke(site, lambda api: api.save_rate(**args))


def _save_raises(site, **kw):
    with pytest.raises(Exception) as info:
        _save(site, **kw)
    return info.value


def _no_ignore_flags(site, doc):
    assert not [k for k in vars(doc.flags) if k.startswith("ignore")], vars(doc.flags)
    assert not [k for k in site.frappe.flags if str(k).startswith("ignore")], site.frappe.flags


def test_save_rate_creates_a_new_draft_through_insert_with_only_the_nine_keys():
    site = _analyst_site()
    result = _save(site, change_reason="  RBI reference, 30 Sep  ")
    assert site.only_for_calls == [("EPM Analyst", "EPM Admin", "System Manager")]
    dicts = [c for c in site.get_doc_calls if isinstance(c, dict)]
    assert len(dicts) == 1
    assert set(dicts[0]) == NEW_KEYS, dicts[0]
    assert dicts[0]["doctype"] == "Group Exchange Rate"
    assert dicts[0]["change_reason"] == "RBI reference, 30 Sep"
    assert (dicts[0]["fiscal_year"], dicts[0]["fiscal_period"]) == (2025, 7)
    doc = site.new_docs[0]
    assert [c[0] for c in doc.calls] == ["insert"]
    _no_ignore_flags(site, doc)
    assert result == {"name": "GER-NEW", "docstatus": 0, "quote_label": doc.quote_label,
                      "source": "Manual"}


#: FR3 (review-d2fu N8): the new-draft path reads the key's draft and
#: approved rows with a locking read, so a second correction of one rate waits
#: on the first and then sees it (MariaDB REPEATABLE READ).
FR3_LOCKING_READ = (
    "SELECT name, docstatus, supersedes FROM `tabGroup Exchange Rate` "
    "WHERE to_currency = %s AND from_currency = %s AND fiscal_year = %s "
    "AND fiscal_period = %s AND rate_type = %s AND docstatus IN (0, 1) "
    "ORDER BY modified DESC FOR UPDATE")


def test_save_rate_reads_the_period_and_the_grain_once_each():
    site = _analyst_site()
    _save(site)
    assert site.reads == [("sql", "fiscal_period_rows"), ("sql", FR3_LOCKING_READ)], \
        site.reads


def test_forge_save_rate_takes_no_kwargs_and_names_only_the_ten_parameters():
    import inspect

    site = _analyst_site()
    sig = _invoke(site, lambda api: {"sig": [(p.name, p.kind) for p in
                                              inspect.signature(api.save_rate).parameters.values()]})
    names = [n for n, _ in sig["sig"]]
    assert names == SAVE_PARAMS, names
    kinds = {k for _, k in sig["sig"]}
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.VAR_POSITIONAL not in kinds
    for forged in FORGED:
        assert forged not in names, forged


def test_forge_a_forged_status_field_cannot_be_passed_at_all():
    site = _analyst_site()
    for forged in FORGED:
        err = _save_raises(site, **{forged: "ZZ forged"})
        assert isinstance(err, TypeError), (forged, err)
    assert site.new_docs == []


def test_failure_path_an_approved_key_with_no_name_is_refused_naming_the_correction():
    # F11 (#312-3): a change of an approved rate is a correction in the app,
    # no longer "a cancel and an amendment (Desk)".
    site = _analyst_site()
    site.docs = [_ger("GER-1", "EUR", "GBP", "Closing", 0.85, 1)]
    err = _save_raises(site, from_currency="EUR")
    assert str(err) == ("GER-1 is already the approved Closing rate EUR → GBP for FY2025 P07: "
                        "correct it with a Reason for Change instead."), err
    assert site.new_docs == []


def test_failure_path_a_draft_key_with_no_name_is_refused_naming_the_draft():
    site = _analyst_site()
    err = _save_raises(site, from_currency="EUR", rate_type="Average")
    assert "GER-2 is already a draft for this rate" in str(err), err
    assert site.new_docs == []


def test_a_cancelled_rate_does_not_block_a_new_draft():
    site = _analyst_site()
    site.docs = [_ger("GER-1", "EUR", "GBP", "Closing", 0.85, 2)]
    result = _save(site, from_currency="EUR")
    assert result["name"] == "GER-NEW"


def _named(site, name="GER-2", **kw):
    data = {"doctype": "Group Exchange Rate", "name": name, "from_currency": "EUR",
            "to_currency": "GBP", "rate_type": "Average", "fiscal_year": 2025,
            "fiscal_period": 7, "quote": 0.84, "quoted_per": "1", "change_reason": "old",
            "docstatus": 0, "owner": ANALYST, "source": "ERP pre-fill", "erp_quote": 0.83,
            "source_note": None, "amended_from": None, "quote_label": "x"}
    data.update(kw)
    doc = _FakeDoc(data)
    site.named[name] = doc
    return doc


def test_update_by_name_changes_only_quote_quoted_per_and_change_reason():
    site = _analyst_site()
    doc = _named(site)
    before = dict(doc._data)
    result = _save(site, name="GER-2", from_currency="EUR", rate_type="Average",
                   quote=8.5, quoted_per="10", change_reason="  RBI  ")
    assert [c[0] for c in doc.calls] == ["save"]
    changed = {k for k in set(before) | set(doc._data) if before.get(k) != doc._data.get(k)}
    assert changed == {"quote", "quoted_per", "change_reason", "quote_label"}, changed
    assert (doc.quote, doc.quoted_per, doc.change_reason) == (8.5, "10", "RBI")
    assert site.new_docs == []
    _no_ignore_flags(site, doc)
    assert result["name"] == "GER-2" and result["docstatus"] == 0
    assert site.reads == [("sql", "fiscal_period_rows"), ("get_doc", "Group Exchange Rate")]


def test_update_by_name_with_a_blank_reason_clears_it():
    site = _analyst_site()
    doc = _named(site)
    _save(site, name="GER-2", from_currency="EUR", rate_type="Average", change_reason="   ")
    assert not doc.change_reason


def test_failure_path_a_named_approved_rate_is_refused():
    site = _analyst_site()
    doc = _named(site, docstatus=1)
    err = _save_raises(site, name="GER-2", from_currency="EUR", rate_type="Average")
    assert str(err) == ("GER-2 is approved, not a draft: correct it with a Reason for Change "
                        "instead."), err
    assert doc.calls == []


def test_failure_path_a_named_cancelled_rate_is_refused():
    site = _analyst_site()
    doc = _named(site, docstatus=2)
    err = _save_raises(site, name="GER-2", from_currency="EUR", rate_type="Average")
    assert str(err) == ("GER-2 is cancelled, not a draft: correct the approved rate with a "
                        "Reason for Change instead."), err
    assert doc.calls == []


def test_failure_path_a_named_doc_with_a_different_grain_is_refused():
    site = _analyst_site()
    doc = _named(site)
    for kw in ({"from_currency": "USD", "rate_type": "Average"},
               {"from_currency": "EUR", "rate_type": "Closing"},
               {"from_currency": "EUR", "rate_type": "Average", "fiscal_period": 6}):
        if kw.get("fiscal_period") == 6:
            site.periods = [_period(2025, 6, "Open"), _period(2025, 7, "Open")]
        err = _save_raises(site, name="GER-2", **kw)
        assert "GER-2" in str(err), err
        assert "EUR" in str(err) and "Average" in str(err), err
    assert doc.calls == []


def test_failure_path_a_closed_period_is_refused_before_any_get_doc():
    site = _analyst_site()
    err = _save_raises(site, fiscal_period=6)
    assert "is Closed" in str(err) and "reopen" in str(err), err
    assert "FY2025 P06" in str(err), err
    assert site.get_doc_calls == []
    assert site.reads == [("sql", "fiscal_period_rows")], site.reads


def test_failure_path_an_undeclared_period_is_refused():
    site = _analyst_site()
    err = _save_raises(site, fiscal_year=2031, fiscal_period=9)
    assert "not declared" in str(err), err
    assert site.get_doc_calls == []


def test_failure_path_rate_type_spot_is_refused_before_any_read():
    site = _analyst_site()
    err = _save_raises(site, rate_type="Spot")
    assert "Spot" in str(err) and "Closing" in str(err), err
    assert site.reads == [] and site.get_doc_calls == []


def test_failure_path_the_entity_accountant_and_the_viewer_are_refused():
    for role in ("Entity Accountant", "EPM User"):
        site = _analyst_site()
        site.roles = {role}
        err = _save_raises(site)
        assert type(err).__name__ == "PermissionError", (role, err)
        assert site.reads == [] and site.get_doc_calls == []


def test_the_close_lead_may_save_the_analysts_draft_and_the_owner_is_untouched():
    site = _Site()
    doc = _named(site)
    _save(site, name="GER-2", from_currency="EUR", rate_type="Average", quote=0.845)
    assert doc.owner == ANALYST
    assert [c[0] for c in doc.calls] == ["save"]


# --- get_pending (E405): HER and OP drafts awaiting approval -----------------------

def test_get_pending_lists_her_direct_and_refuses_the_leads_own_op_under_blocked():
    site = _Site()
    site.her = [_her("HER-1", owner=ANALYST)]
    site.ops = [_op("OP-1", owner=LEAD)]
    site.user = LEAD
    site.roles = {"EPM Admin"}
    site.policy = "Blocked"
    result = _call_pending(site)
    json.dumps(result)
    her_item = next(i for i in result["items"] if i["doctype"] == "Historical Equity Rate")
    op_item = next(i for i in result["items"] if i["doctype"] == "Ownership Period")
    assert her_item["approve"]["mode"] == "direct"
    # failure path: self-approval under Blocked is refused
    assert op_item["approve"]["mode"] == "refused"
    assert result["counts"]["Historical Equity Rate"] == 1
    assert result["counts"]["Ownership Period"] == 1
    assert isinstance(her_item["created"], str) and isinstance(op_item["created"], str)


def test__iso_attaches_the_system_zone_to_a_naive_datetime():
    """L01e: ``rates_api._iso`` is the same ``timefmt.zoned_iso`` wrapper
    every sibling endpoint's ``_iso`` uses (tb_read_api.py, checks_api.py,
    trail_api.py, freshness_api.py, signoff_api.py) — not a naive
    ``isoformat()``."""
    api = _load_rates_api(_Site())
    # BST (+01:00) in July.
    assert api._iso(datetime(2025, 7, 1, 9, 0, 0)) == "2025-07-01T09:00:00+01:00"
    # GMT in January: +00:00, not the +01:00 of the summer.
    assert api._iso(datetime(2025, 1, 10, 8, 0, 0)) == "2025-01-10T08:00:00+00:00"


def test__iso_leaves_a_plain_date_alone_and_blank_stays_none():
    api = _load_rates_api(_Site())
    assert api._iso(date(2025, 10, 3)) == "2025-10-03"
    assert api._iso(None) is None and api._iso("") is None


# --- A55: every datetime get_pending sends carries the site's time zone -----------

_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_OFFSET = re.compile(r"(Z|[+-]\d{2}:\d{2})$")


def _strings(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, str):
        yield value


def test_get_pending_payload_carries_no_naive_datetime():
    # Found live 2 Oct via L01d: get_pending's created showed raw, e.g.
    # '2026-09-16T18:13:05.213749' (no zone).
    site = _Site()
    site.her = [_her("HER-1", rate_date=date(2024, 1, 1),
                      creation=datetime(2025, 7, 1, 9, 0, 0))]
    site.ops = [_op("OP-1", effective_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
                     creation=datetime(2025, 7, 2, 9, 0, 0))]
    result = _call_pending(site)
    stamps = [s for s in _strings(result) if _DATETIME.match(s)]
    naive = [s for s in stamps if not _OFFSET.search(s)]
    assert not naive, "datetimes sent without a time zone: %r" % naive
    assert sorted(stamps) == ["2025-07-01T09:00:00+01:00", "2025-07-02T09:00:00+01:00"], stamps


def test_failure_path_seen_by_the_analyst_every_item_is_not_approver():
    site = _Site()
    site.her = [_her("HER-1", owner=ANALYST)]
    site.ops = [_op("OP-1", owner=LEAD)]
    site.user = ANALYST
    site.roles = {"EPM Analyst"}
    result = _call_pending(site)
    assert result["items"] and all(i["approve"]["mode"] == "not_approver" for i in result["items"])
    assert result["can_approve"] is False


def test_under_allowed_with_reason_the_leads_own_op_needs_a_reason():
    site = _Site()
    site.ops = [_op("OP-1", owner=LEAD)]
    site.user = LEAD
    site.roles = {"EPM Admin"}
    site.policy = "Allowed with reason"
    result = _call_pending(site)
    op_item = next(i for i in result["items"] if i["doctype"] == "Ownership Period")
    assert op_item["approve"]["mode"] == "reason"
    assert "approval_api.approve" in op_item["approve"]["message"]


def test_no_drafts_gives_empty_items_and_zero_counts_never_missing_keys():
    site = _Site()
    result = _call_pending(site)
    assert result["items"] == []
    assert result["counts"]["Historical Equity Rate"] == 0
    assert result["counts"]["Ownership Period"] == 0


def test_failure_path_the_entity_accountant_is_refused_from_pending():
    site = _Site()
    site.her = [_her("HER-1")]
    site.roles = {"Entity Accountant"}
    err = _call_pending_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.reads == []


def test_pending_query_count_is_constant_in_the_number_of_drafts():
    one = _Site()
    one.her = [_her("HER-1")]
    one.ops = [_op("OP-1")]
    ten = _Site()
    ten.her = [_her("HER-%d" % i) for i in range(5)]
    ten.ops = [_op("OP-%d" % i) for i in range(5)]
    r1, r10 = _call_pending(one), _call_pending(ten)
    assert len(r1["items"]) == 2 and len(r10["items"]) == 10
    assert len(one.reads) == 7, one.reads
    assert len(ten.reads) == 7, ten.reads


# --- get_pending: Viewer and entity scope (#305-W2-10, W2-14) ----------------------

def test_a_viewer_reads_pending_but_every_item_is_not_approver():
    site = _Site()
    site.her = [_her("HER-1")]
    site.ops = [_op("OP-1")]
    site.user = VIEWER
    site.roles = {"EPM User"}
    result = _call_pending(site)
    assert result["items"] and all(i["approve"]["mode"] == "not_approver" for i in result["items"])
    assert result["can_approve"] is False


def test_failure_path_a_leak_items_are_cut_to_the_callers_allowed_entities():
    site = _Site()
    site.her = [_her("HER-A", data_area_id="ZZA"), _her("HER-X", data_area_id="ZZX")]
    site.allowed = {"ZZA"}
    result = _call_pending(site)
    assert len(result["items"]) == 1
    assert result["counts"]["hidden"] == 1
    dumped = json.dumps(result)
    assert "ZZX" not in dumped, dumped


def test_the_w2_14_case_an_op_edited_by_the_lead_is_refused_under_blocked():
    site = _Site()
    site.ops = [_op("OP-1", owner=ANALYST)]
    site.versions = [{"ref_doctype": "Ownership Period", "docname": "OP-1", "owner": LEAD,
                      "data": json.dumps({"changed": [["ownership_pct", 50, 60]]})}]
    site.user = LEAD
    site.roles = {"EPM Admin"}
    site.policy = "Blocked"
    result = _call_pending(site)
    op_item = result["items"][0]
    assert op_item["approve"]["mode"] == "refused"
    assert op_item["edited_by"] == [LEAD]


# --- get_ownership (E406): ownership gaps for the period, from scope_model ---------

def test_failure_path_a_tb_with_no_ownership_is_blocking_and_a_covered_entity_is_not():
    site = _Site()
    site.entities = [_entity("ZZA"), _entity("ZZB"), _entity("ZZC")]
    site.ops = [_op("OP-A", data_area_id="ZZA", effective_date=date(2025, 1, 1), docstatus=1)]
    site.tbs = [_tb("ZZA"), _tb("ZZB")]
    result = _call_ownership(site)
    assert result["blocking"] == [{
        "entity": "ZZB",
        "message": "ZZB has a submitted trial balance for FY2025 P07 but no approved ownership "
                   "period covering 2025-07-01: it is not consolidated. Record its ownership, or "
                   "cancel the trial balance (#305-W2-2).",
        "desk": "/app/ownership-period/new?data_area_id=ZZB",
    }]
    # ZZA is covered and has a TB: not listed anywhere.
    # ZZC is an Active leaf with no OP and no TB: out_of_scope only, never blocking.
    assert result["out_of_scope"] == ["ZZC"]
    assert result["in_scope_count"] == 1
    assert result["start_date"] == "2025-07-01"
    assert result["period"] == {"fiscal_year": 2025, "fiscal_period": 7}
    assert result["can_record"] is True
    assert result["hidden"] == 0


def test_an_ownership_period_whose_end_date_is_before_start_does_not_cover():
    site = _Site()
    site.entities = [_entity("ZZA")]
    site.ops = [_op("OP-A", data_area_id="ZZA", effective_date=date(2025, 1, 1),
                    end_date=date(2025, 6, 30), docstatus=1)]
    site.tbs = [_tb("ZZA")]
    result = _call_ownership(site)
    assert [b["entity"] for b in result["blocking"]] == ["ZZA"]
    assert result["out_of_scope"] == []
    assert result["in_scope_count"] == 0


def test_failure_path_an_undeclared_period_is_refused_from_get_ownership():
    site = _Site()
    err = _call_ownership_raises(site, 2031, 9)
    assert "not declared" in str(err), err
    assert "FY2031 P09" in str(err), err


def test_failure_path_the_entity_accountant_is_refused_from_get_ownership():
    site = _Site()
    site.roles = {"Entity Accountant"}
    err = _call_ownership_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.only_for_calls == [RATES_ROLES]
    assert site.reads == []


def test_ownership_query_count_is_constant_in_the_number_of_leaves():
    # O63: a caller who may record also reads the submitted nodes for the
    # change form (one more read; no node has an entity here, so no Entity
    # name read). A Viewer's count stays 4 (test_o63_a_viewer_gets_change_none).
    small = _Site()
    small.entities = [_entity("ZZA"), _entity("ZZB"), _entity("ZZC")]
    big = _Site()
    big.entities = [_entity("ZZ%03d" % i) for i in range(30)]
    _call_ownership(small)
    _call_ownership(big)
    assert len(small.reads) == 5, small.reads
    assert len(big.reads) == 5, big.reads


def test_an_epm_user_reads_ownership_with_can_record_false():
    site = _Site()
    site.entities = [_entity("ZZA")]
    site.user = VIEWER
    site.roles = {"EPM User"}
    site.can_record = False
    result = _call_ownership(site)
    assert site.only_for_calls == [RATES_ROLES]
    assert result["can_record"] is False


def test_failure_path_a_leak_blocking_is_cut_to_the_callers_allowed_entities():
    site = _Site()
    site.entities = [_entity("ZZA"), _entity("ZZB")]
    site.tbs = [_tb("ZZA"), _tb("ZZB")]
    site.allowed = {"ZZA"}
    result = _call_ownership(site)
    assert [b["entity"] for b in result["blocking"]] == ["ZZA"]
    assert result["hidden"] == 1
    assert result["blocking_hidden"] == 1
    dumped = json.dumps(result)
    assert "ZZB" not in dumped, dumped


def test_blocking_hidden_counts_only_hidden_blocking_entities_not_out_of_scope():
    # ZZA, ZZB: submitted TB, no ownership -> blocking. ZZC: no TB, no ownership
    # -> out_of_scope. allowed = {ZZA}: ZZB is a hidden blocker, ZZC is a hidden
    # out-of-scope entity. blocking_hidden must count only ZZB, while the
    # existing combined `hidden` field keeps counting both (N1).
    site = _Site()
    site.entities = [_entity("ZZA"), _entity("ZZB"), _entity("ZZC")]
    site.tbs = [_tb("ZZA"), _tb("ZZB")]
    site.allowed = {"ZZA"}
    result = _call_ownership(site)
    assert [b["entity"] for b in result["blocking"]] == ["ZZA"]
    assert result["out_of_scope"] == []
    assert result["blocking_hidden"] == 1
    assert result["hidden"] == 2
    dumped = json.dumps(result)
    assert "ZZB" not in dumped and "ZZC" not in dumped, dumped


def test_in_scope_count_is_cut_to_the_callers_allowed_entities_not_total_scope():
    # Three entities are all covered by ownership (all in scope). allowed
    # narrows to two of them: in_scope_count must be 2 (len(scope & allowed)),
    # never 3 (len(scope)) — the cut a mutation to plain len(scope) would miss
    # (N2).
    site = _Site()
    site.entities = [_entity("ZZA"), _entity("ZZB"), _entity("ZZC")]
    site.ops = [
        _op("OP-A", data_area_id="ZZA", effective_date=date(2025, 1, 1), docstatus=1),
        _op("OP-B", data_area_id="ZZB", effective_date=date(2025, 1, 1), docstatus=1),
        _op("OP-C", data_area_id="ZZC", effective_date=date(2025, 1, 1), docstatus=1),
    ]
    site.allowed = {"ZZA", "ZZB"}
    result = _call_ownership(site)
    assert result["in_scope_count"] == 2


def test_a_non_regular_period_is_refused_from_get_ownership():
    site = _Site()
    site.periods.append({
        "fiscal_year": 2025, "fiscal_period": 13, "period_code": "P13",
        "period_label": "Closing", "period_type": "Closing",
        "start_date": date(2025, 12, 31), "end_date": date(2025, 12, 31),
        "quarter": "", "status": "Open",
    })
    err = _call_ownership_raises(site, 2025, 13)
    assert type(err).__name__ == "ValidationError", err
    assert "Regular" in str(err), err
    assert "FY2025 P13" in str(err), err


# --- O55: preview_ownership_change (GET): refusals and effect, no write -----
# Decisions: #305-4.2-1 (structural effect only), #305-Q1-1 (a change ends its
# predecessor on approval), wireframe-4.2.md as drawn. The context and effect
# come from the REAL ownership_change.py / ownership_change_model.py.

PREVIEW_PARAMS = ["fiscal_year", "fiscal_period", "consolidation_group", "entity",
                  "ownership_pct", "consolidation_method"]
O55_GROUP = "ECL_GROUP"
O55_LEAF = "ZZ5B1"
O55_GOLDEN = os.path.join(FIXTURES, "close_ownership_preview_payload.json")
O55_GOLDEN_REFUSED = os.path.join(FIXTURES, "close_ownership_preview_refused.json")
O55_MODEL = None


def _o55_model():
    global O55_MODEL
    if O55_MODEL is None:
        spec = importlib.util.spec_from_file_location(
            "ownership_change_model_for_o55_test",
            os.path.join(CLOSE_DIR, "ownership_change_model.py"))
        O55_MODEL = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(O55_MODEL)
    return O55_MODEL


def _o55_calendar():
    """FY2025 as live declares it (measured 7 Oct): P00 Opening on 1 Jan,
    P01-P12 Regular months, P13 Closing on 31 Dec."""
    rows = [{"fiscal_year": 2025, "fiscal_period": 0, "period_code": "P00",
             "period_label": "Opening", "period_type": "Opening",
             "start_date": date(2025, 1, 1), "end_date": date(2025, 1, 1),
             "quarter": "", "status": "Open"}]
    for m in range(1, 13):
        end = (date(2025 + m // 12, m % 12 + 1, 1) - (date(2025, 1, 2) - date(2025, 1, 1)))
        rows.append({"fiscal_year": 2025, "fiscal_period": m, "period_code": "P%02d" % m,
                     "period_label": "P%02d" % m, "period_type": "Regular",
                     "start_date": date(2025, m, 1), "end_date": end,
                     "quarter": "Q%d" % ((m - 1) // 3 + 1), "status": "Open"})
    rows.append({"fiscal_year": 2025, "fiscal_period": 13, "period_code": "P13",
                 "period_label": "Closing", "period_type": "Closing",
                 "start_date": date(2025, 12, 31), "end_date": date(2025, 12, 31),
                 "quarter": "", "status": "Open"})
    return rows


def _o55_site(roles=("EPM Analyst",), user=ANALYST):
    site = _Site()
    site.user = user
    site.roles = set(roles)
    site.periods = _o55_calendar()
    site.ops = [_op("OP-ZZ5B1-1", data_area_id=O55_LEAF, group=O55_GROUP,
                    effective_date=date(2025, 1, 1), end_date=None, ownership_pct=100.0,
                    consolidation_method="full", owner=LEAD, docstatus=1)]
    # P09 is before the change (not re-signed); P11 is after it. The Closing
    # P13 is after it too, but an approval never marks a Closing period, so
    # it is not listed (R52f, review S5).
    site.signed = [(2025, 9), (2025, 11), (2025, 13)]
    site.first_close = (2025, 1)
    return site


def _preview(site, fy=2025, fp=10, group=O55_GROUP, entity=O55_LEAF, pct="80", method="full"):
    return _invoke(site, lambda api: api.preview_ownership_change(fy, fp, group, entity, pct,
                                                                  method))


def _preview_raises(site, **k):
    with pytest.raises(Exception) as info:
        _preview(site, **k)
    return info.value


def _golden(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _no_write(site):
    assert site.new_docs == [], site.new_docs
    assert [c for c in site.get_doc_calls] == [], site.get_doc_calls
    assert not [r for r in site.reads if r[0] == "sql" and r[1] != "fiscal_period_rows"], site.reads


def test_o55_preview_happy_path_matches_the_golden_payload():
    site = _o55_site()
    result = _preview(site)
    assert site.only_for_calls == [RATES_ROLES], site.only_for_calls
    assert result == _golden(O55_GOLDEN), json.dumps(result, indent=1)
    assert set(result) == {"problems", "effect", "current", "pending"}
    assert result["problems"] == []
    assert result["effect"]["resign"] == ["FY2025 P11"]
    assert result["effect"]["current_ends"] == "2025-09-30"
    assert result["current"]["name"] == "OP-ZZ5B1-1"
    _no_write(site)


def test_o55_the_effect_is_the_real_models_for_the_periods_start():
    site = _o55_site()
    result = _preview(site)
    model = _o55_model()
    current = result["current"]
    expected = model.effect({"entity": O55_LEAF, "effective_date": "2025-10-01",
                             "ownership_pct": "80", "consolidation_method": "full"},
                            current, _o55_calendar(), _o64_signatures(site, {(2025, 11)}))
    assert result["effect"] == expected
    # O64: who signed each re-sign period, and the predecessor's name.
    assert result["effect"]["current_name"] == "OP-ZZ5B1-1"
    assert result["effect"]["resign_detail"] == [
        {"period": "FY2025 P11", "signed_on": "2025-10-04", "signed_by_name": O64_LEAD_NAME}]
    assert "not previewed" in result["effect"]["not_shown"]


def test_o55_the_golden_payload_carries_no_amount():
    golden = _golden(O55_GOLDEN)
    assert golden["effect"]["not_shown"] == _o55_model().NOT_SHOWN
    for banned in ("goodwill_amount", "nci_amount", "amount", "result"):
        assert banned not in golden["effect"], banned


def test_o55_a_viewer_may_preview():
    site = _o55_site(roles=("EPM User",), user=VIEWER)
    result = _preview(site)
    assert result["problems"] == []
    assert result["effect"]["first_period"] == "FY2025 P10"
    _no_write(site)


def test_o55_refused_change_matches_the_golden_refused_payload_and_has_no_effect():
    site = _o55_site()
    site.ops.append(_op("OP-ZZ5B1-2", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=70.0,
                        consolidation_method="full", docstatus=0))
    result = _preview(site, pct="120")
    assert result == _golden(O55_GOLDEN_REFUSED), json.dumps(result, indent=1)
    assert result["problems"] == [
        "Ownership % must be a number from 0 to 100.",
        "A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-2): edit that draft.",
    ]
    assert result["effect"] is None
    assert result["current"]["name"] == "OP-ZZ5B1-1"
    _no_write(site)


def test_o55_failure_path_nan_pct_is_a_problem_never_an_effect():
    site = _o55_site()
    result = _preview(site, pct="nan")
    assert result["problems"] == ["Ownership % must be a number from 0 to 100."]
    assert result["effect"] is None


def test_o55_failure_path_no_current_ownership_is_the_desk_sentence():
    site = _o55_site()
    site.ops = []
    result = _preview(site)
    assert result["current"] is None
    assert result["effect"] is None
    assert result["problems"][0] == (
        "ZZ5B1 has no ownership for FY2025 P10: record its first ownership in Desk "
        "(an acquisition is a Business Combination).")


def test_o55_failure_path_a_hidden_entity_throws_before_any_read():
    site = _o55_site(roles=("EPM Analyst",))
    site.allowed = {"ZZOTHER"}
    err = _preview_raises(site)
    assert type(err).__name__ == "ValidationError", err
    assert "ZZ5B1" in str(err), err
    assert site.reads == [], site.reads
    _no_write(site)


def test_o55_failure_path_a_blank_entity_is_refused_for_a_scoped_caller():
    site = _o55_site()
    site.allowed = {"ZZ5B1"}
    err = _preview_raises(site, entity="")
    assert type(err).__name__ == "ValidationError", err
    assert site.reads == [], site.reads


def test_o55_a_visible_entity_previews_for_a_scoped_caller():
    site = _o55_site()
    site.allowed = {"ZZ5B1"}
    assert _preview(site)["problems"] == []


def test_o55_failure_path_a_closing_period_is_the_period_problem():
    site = _o55_site()
    result = _preview(site, fp=13)
    assert result["effect"] is None
    assert result["problems"][0] == (
        "FY2025 P13 is the Closing period, not a Regular one: an ownership change starts "
        "on the first day of a Regular period; pick a Regular period.")
    # The model's own first-day sentence follows: 31 Dec is inside P12.
    assert any(p.startswith("Ownership changes take effect on the first day of a period")
               for p in result["problems"]), result["problems"]
    _no_write(site)


def test_o55_failure_path_an_opening_period_is_never_silently_p01():
    # P00 starts on P01's first day, so the model alone sees a valid first
    # day: the endpoint must refuse the non-Regular period itself.
    site = _o55_site()
    result = _preview(site, fp=0)
    assert result["effect"] is None
    assert result["problems"][0] == (
        "FY2025 P00 is the Opening period, not a Regular one: an ownership change starts "
        "on the first day of a Regular period; pick a Regular period.")
    _no_write(site)


def test_o55_failure_path_an_undeclared_period_throws_naming_it():
    site = _o55_site()
    err = _preview_raises(site, fp=20)
    assert type(err).__name__ == "ValidationError", err
    assert "FY2025 P20" in str(err), err


def test_o55_failure_path_corrupt_overlap_throws_a_frappe_error_not_a_value_error():
    site = _o55_site()
    site.ops.append(_op("OP-ZZ5B1-X", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 6, 1), ownership_pct=60.0,
                        consolidation_method="full", docstatus=1))
    err = _preview_raises(site)
    assert type(err).__name__ == "ValidationError", err
    assert "corrupt" in str(err), err


def test_o55_failure_path_the_entity_accountant_is_refused():
    site = _o55_site(roles=("Entity Accountant",), user="zz-ea@example.com")
    err = _preview_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.reads == []


def test_o55_preview_names_only_its_parameters_and_takes_no_kwargs():
    # O66 adds ``name`` (the draft being edited), the only optional one.
    import inspect

    site = _o55_site()
    sig = _invoke(site, lambda api: {"sig": [
        (p.name, p.kind, p.default is None)
        for p in inspect.signature(api.preview_ownership_change).parameters.values()]})
    assert [n for n, _, _ in sig["sig"]] == PREVIEW_PARAMS + ["name"]
    kinds = {k for _, k, _ in sig["sig"]}
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.VAR_POSITIONAL not in kinds
    assert [n for n, _, none in sig["sig"] if none] == ["name"]


def test_o55_preview_is_a_get_with_a_literal_role_tuple():
    import ast

    with open(API_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "preview_ownership_change")
    deco = fn.decorator_list[0]
    assert ast.unparse(deco) == "frappe.whitelist(methods=['GET'])", ast.unparse(deco)
    first = fn.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        first = fn.body[1]
    call = first.value
    assert ast.unparse(call.func) == "frappe.only_for"
    assert isinstance(call.args[0], ast.Tuple), ast.unparse(call)
    assert tuple(e.value for e in call.args[0].elts) == RATES_ROLES


# --- O56: save_ownership_change (POST): the Analyst's draft ------------------
# Decisions: #305-4.2-1, #305-Q1-1, wireframe-4.2.md as drawn; R2 (the
# Analyst drafts, the Close Lead approves). Every refusal of the preview is
# thrown before any write; the controller (O53) decides the rest, so the
# insert and the save carry no ignore flag.

O56_SAVE_PARAMS = PREVIEW_PARAMS + ["name"]
O56_SAVE_ROLES = ("EPM Analyst", "EPM Admin", "System Manager")


def _o56_save(site, fy=2025, fp=10, group=O55_GROUP, entity=O55_LEAF, pct="80", method="full",
          name=None):
    return _invoke(site, lambda api: api.save_ownership_change(
        fy, fp, group, entity, pct, method, name=name))


def _o56_save_raises(site, **k):
    with pytest.raises(Exception) as info:
        _o56_save(site, **k)
    return info.value


def _no_save(site):
    """Nothing inserted and nothing saved: every refusal comes before the write."""
    assert site.new_docs == [], [d._data for d in site.new_docs]
    for doc in site.named.values():
        assert doc.calls == [], doc.calls


def _o56_draft(name="OP-ZZ5B1-D", entity=O55_LEAF, group=O55_GROUP,
               effective_date=date(2025, 10, 1), pct=70.0, docstatus=0, owner=ANALYST):
    row = _op(name, data_area_id=entity, group=group, effective_date=effective_date,
              ownership_pct=pct, consolidation_method="full", owner=owner,
              docstatus=docstatus)
    doc = _FakeDoc(dict(row, doctype="Ownership Period", supersedes="OP-ZZ5B1-1",
                        superseded_end_date=None))
    return row, doc


def test_o56_happy_path_inserts_one_draft_superseding_the_current_period():
    site = _o55_site()
    result = _o56_save(site)
    assert site.only_for_calls == [O56_SAVE_ROLES], site.only_for_calls
    assert result == {"name": "OP-NEW", "docstatus": 0}
    assert len(site.new_docs) == 1
    doc = site.new_docs[0]
    assert [c[0] for c in doc.calls] == ["insert"]
    assert doc.calls[0][1:] == ((), {}), "insert() with no ignore flag: the controller decides"
    assert doc._data == {
        "doctype": "Ownership Period", "consolidation_group": O55_GROUP,
        "data_area_id": O55_LEAF, "effective_date": "2025-10-01", "end_date": None,
        "ownership_pct": 80.0, "consolidation_method": "full",
        "supersedes": "OP-ZZ5B1-1", "superseded_end_date": None,
        "name": "OP-NEW", "docstatus": 0, "source": "Manual",
        "quote_label": doc._data["quote_label"]}


def test_o56_the_draft_ends_where_the_current_period_ends_as_the_effect_says():
    # The preview's effect gives after.to = the current period's end; the
    # saved draft must be that change, not an open-ended one.
    site = _o55_site()
    site.ops[0]["end_date"] = date(2025, 12, 31)
    _o56_save(site)
    doc = site.new_docs[0]
    assert doc._data["end_date"] == "2025-12-31"
    assert doc._data["superseded_end_date"] == "2025-12-31"


def test_o56_forged_keys_never_reach_the_doc_the_signature_is_pinned():
    import inspect

    site = _o55_site()
    sig = _invoke(site, lambda api: {"sig": [
        (p.name, p.kind, p.default is None)
        for p in inspect.signature(api.save_ownership_change).parameters.values()]})
    assert [n for n, _, _ in sig["sig"]] == O56_SAVE_PARAMS
    kinds = {k for _, k, _ in sig["sig"]}
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.VAR_POSITIONAL not in kinds
    for forged in ("docstatus", "supersedes", "superseded_end_date", "end_date",
                   "effective_date", "owner", "amended_from", "acquisition_price",
                   "is_disposal"):
        assert forged not in O56_SAVE_PARAMS, forged
    # The only optional parameter is ``name`` (default None).
    assert [n for n, _, none in sig["sig"] if none] == ["name"]


def test_o56_is_a_post_with_a_literal_role_tuple_and_never_commits():
    import ast

    with open(API_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "save_ownership_change")
    deco = fn.decorator_list[0]
    assert ast.unparse(deco) == "frappe.whitelist(methods=['POST'])", ast.unparse(deco)
    first = fn.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        first = fn.body[1]
    call = first.value
    assert ast.unparse(call.func) == "frappe.only_for"
    assert isinstance(call.args[0], ast.Tuple), ast.unparse(call)
    assert tuple(e.value for e in call.args[0].elts) == O56_SAVE_ROLES
    # Code only (the docstring may say what the endpoint does not do).
    used = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg:
            used.add(node.arg)
    for banned in ("commit", "ignore_permissions", "ignore_validate", "ignore_mandatory",
                   "ignore_links", "submit"):
        assert banned not in used, banned


def test_o56_failure_path_a_viewer_is_refused_by_only_for_before_any_read():
    site = _o55_site(roles=("EPM User",), user=VIEWER)
    err = _o56_save_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.reads == [], site.reads
    _no_save(site)


def test_o56_failure_path_the_entity_accountant_is_refused():
    site = _o55_site(roles=("Entity Accountant",), user="zz-ea@example.com")
    err = _o56_save_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.reads == []
    _no_save(site)


def test_o56_failure_path_a_hidden_entity_throws_before_any_read():
    site = _o55_site()
    site.allowed = {"ZZOTHER"}
    err = _o56_save_raises(site)
    assert type(err).__name__ == "ValidationError", err
    assert "ZZ5B1" in str(err), err
    assert site.reads == [], site.reads
    _no_save(site)


def test_o56_failure_path_a_hidden_entity_with_a_name_throws_before_reading_the_draft():
    site = _o55_site()
    site.allowed = {"ZZOTHER"}
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    err = _o56_save_raises(site, name=row["name"])
    assert type(err).__name__ == "ValidationError", err
    assert site.reads == [], site.reads
    assert site.get_doc_calls == [], site.get_doc_calls
    _no_save(site)


def _o56_refusal_cases():
    """(label, site mutator, save kwargs, the sentence the model writes)."""
    def no_current(site):
        site.ops = []

    def closed(site):
        for r in site.periods:
            if r["fiscal_period"] == 10:
                r["status"] = "Closed"

    def started_same_day(site):
        site.ops[0]["effective_date"] = date(2025, 10, 1)

    def later(site):
        site.ops[0]["end_date"] = date(2025, 10, 31)
        site.ops.append(_op("OP-ZZ5B1-L", data_area_id=O55_LEAF, group=O55_GROUP,
                            effective_date=date(2025, 11, 1), ownership_pct=90.0,
                            consolidation_method="full", docstatus=1))

    def pending(site):
        site.ops.append(_op("OP-ZZ5B1-P", data_area_id=O55_LEAF, group=O55_GROUP,
                            effective_date=date(2025, 11, 1), ownership_pct=70.0,
                            consolidation_method="full", docstatus=0))

    def nothing(site):
        pass

    return [
        ("no current", no_current, {},
         "ZZ5B1 has no ownership for FY2025 P10: record its first ownership in Desk "
         "(an acquisition is a Business Combination)."),
        ("non-Regular period", nothing, {"fp": 13},
         "FY2025 P13 is the Closing period, not a Regular one: an ownership change starts "
         "on the first day of a Regular period; pick a Regular period."),
        ("first day", nothing, {"fp": 13},
         "Ownership changes take effect on the first day of a period: the warehouse reads "
         "ownership on each period's first day. 2025-12-31 is inside FY2025 P12;"),
        ("Opening period", nothing, {"fp": 0},
         "FY2025 P00 is the Opening period, not a Regular one"),
        ("closed period", closed, {},
         "FY2025 P10 is Closed: an ownership change must start in an Open period."),
        ("pct 120", nothing, {"pct": "120"}, "Ownership % must be a number from 0 to 100."),
        ("pct nan", nothing, {"pct": "nan"}, "Ownership % must be a number from 0 to 100."),
        ("pct inf", nothing, {"pct": "inf"}, "Ownership % must be a number from 0 to 100."),
        ("method", nothing, {"method": "bogus"},
         "Method must be one of full, proportional, equity, none."),
        ("nothing changes", nothing, {"pct": "100"},
         "Nothing changes: ZZ5B1 is already 100 % full from 2025-01-01."),
        ("starts same day", started_same_day, {},
         "The change must start after the current period's start (2025-10-01)."),
        ("later exists", later, {},
         "ZZ5B1 already has an ownership period from 2025-11-01: change or cancel that one "
         "first."),
        ("pending exists", pending, {},
         "A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-P): edit that draft."),
    ]


def test_o56_failure_path_every_refusal_throws_its_sentence_with_no_insert():
    for label, mutate, kwargs, sentence in _o56_refusal_cases():
        site = _o55_site()
        mutate(site)
        err = _o56_save_raises(site, **kwargs)
        assert type(err).__name__ == "ValidationError", (label, err)
        assert sentence in str(err), (label, str(err))
        _no_save(site)


def test_o56_failure_path_every_problem_is_in_the_one_refusal():
    site = _o55_site()
    site.ops.append(_op("OP-ZZ5B1-P", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=70.0,
                        consolidation_method="full", docstatus=0))
    err = _o56_save_raises(site, pct="120")
    assert "Ownership % must be a number from 0 to 100." in str(err), err
    assert "already awaiting approval (OP-ZZ5B1-P)" in str(err), err
    _no_save(site)


def test_o56_failure_path_corrupt_overlap_throws_with_no_insert():
    site = _o55_site()
    site.ops.append(_op("OP-ZZ5B1-X", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 6, 1), ownership_pct=60.0,
                        consolidation_method="full", docstatus=1))
    err = _o56_save_raises(site)
    assert type(err).__name__ == "ValidationError", err
    assert "corrupt" in str(err), err
    _no_save(site)


def test_o56_edit_saves_the_named_draft_leaving_it_out_of_the_pending_check():
    # ``ownership_change.context`` reports every draft on the node as pending;
    # the draft being edited is excluded by its exact name.
    site = _o55_site()
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    result = _o56_save(site, pct="80", method="equity", name=row["name"])
    assert result == {"name": "OP-ZZ5B1-D", "docstatus": 0}
    assert site.new_docs == []
    assert [c[0] for c in doc.calls] == ["save"]
    assert doc.calls[0][1:] == ((), {}), "save() with no ignore flag: the controller decides"
    assert doc._data["ownership_pct"] == 80.0
    assert doc._data["consolidation_method"] == "equity"
    assert doc._data["supersedes"] == "OP-ZZ5B1-1"
    assert doc._data["superseded_end_date"] is None
    assert doc._data["effective_date"] == date(2025, 10, 1), "the period is unchanged"


def test_o56_failure_path_edit_still_refuses_another_pending_draft_on_the_node():
    site = _o55_site()
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    site.ops.append(_op("OP-ZZ5B1-E", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=75.0,
                        consolidation_method="full", docstatus=0))
    err = _o56_save_raises(site, name=row["name"])
    assert ("A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-E): edit that draft."
            in str(err)), err
    assert "OP-ZZ5B1-D" not in str(err), "the draft being edited is left out"
    _no_save(site)


def test_o56_failure_path_the_exclude_is_the_exact_name_never_a_pattern():
    # A draft whose name merely starts with the edited one stays pending.
    site = _o55_site()
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    site.ops.append(_op("OP-ZZ5B1-D2", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=75.0,
                        consolidation_method="full", docstatus=0))
    err = _o56_save_raises(site, name=row["name"])
    assert "awaiting approval (OP-ZZ5B1-D2)" in str(err), err
    _no_save(site)


def test_o56_failure_path_editing_an_approved_period_is_refused():
    site = _o55_site()
    site.named["OP-ZZ5B1-1"] = _FakeDoc(dict(site.ops[0], doctype="Ownership Period"))
    err = _o56_save_raises(site, name="OP-ZZ5B1-1")
    assert type(err).__name__ == "ValidationError", err
    assert str(err).startswith("OP-ZZ5B1-1 is approved: record a new change instead."), err
    _no_save(site)


def test_o56_failure_path_editing_a_cancelled_period_is_refused():
    site = _o55_site()
    row, doc = _o56_draft(docstatus=2)
    site.ops.append(row)
    site.named[row["name"]] = doc
    err = _o56_save_raises(site, name=row["name"])
    assert str(err).startswith("OP-ZZ5B1-D is cancelled: record a new change instead."), err
    _no_save(site)


def test_o56_failure_path_editing_another_entitys_draft_is_refused():
    site = _o55_site()
    row, doc = _o56_draft(name="OP-ZZOTHER-D", entity="ZZOTHER")
    site.ops.append(row)
    site.named[row["name"]] = doc
    err = _o56_save_raises(site, name=row["name"])
    assert type(err).__name__ == "ValidationError", err
    assert "OP-ZZOTHER-D is the change for ZZOTHER in ECL_GROUP from 2025-10-01" in str(err), err
    _no_save(site)


def test_o56_failure_path_editing_a_draft_into_another_period_is_refused():
    # The draft's name carries its effective date (autoname), so a period
    # change is a new draft, never a silent move.
    site = _o55_site()
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    err = _o56_save_raises(site, fp=11, name=row["name"])
    assert type(err).__name__ == "ValidationError", err
    assert "OP-ZZ5B1-D is the change for ZZ5B1 in ECL_GROUP from 2025-10-01" in str(err), err
    assert "2025-11-01" in str(err), err
    _no_save(site)


def test_o64_failure_path_a_listed_run_without_a_signer_name_refuses_the_preview():
    """A re-sign period whose signer has no full name is refused naming the
    run: never shown with a user id or a blank (O64)."""
    site = _o55_site()
    site.users = {LEAD: ""}
    err = _preview_raises(site)
    assert type(err).__name__ == "ValidationError", err
    assert "ZZ-RUN-2025-11" in str(err), err
    assert LEAD not in str(err), err
    _no_write(site)


# --- O66: preview_ownership_change takes the draft being edited --------------
# wireframe-4.2.md §1 ("The Analyst can edit it until it is approved"). The
# preview passes ``name`` as the same exact-name exclude as O56's save, and
# refuses (as data, like every other preview problem) a name that is not a
# draft of the same node and first day.


def _o66_preview(site, fy=2025, fp=10, group=O55_GROUP, entity=O55_LEAF, pct="80",
                 method="full", name=None):
    return _invoke(site, lambda api: api.preview_ownership_change(
        fy, fp, group, entity, pct, method, name=name))


def _o66_site_with_draft(**k):
    site = _o55_site(**k)
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    return site, row


def _o66_no_write(site):
    assert site.new_docs == [], [d._data for d in site.new_docs]
    for doc in site.named.values():
        assert doc.calls == [], doc.calls


def test_o66_failure_path_without_name_the_draft_still_refuses_as_pending():
    # The base line: without ``name`` the node's draft is pending.
    site, row = _o66_site_with_draft()
    result = _o66_preview(site)
    assert result["problems"] == [
        "A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-D): edit that draft."]
    assert result["effect"] is None


def test_o66_preview_of_the_draft_being_edited_is_not_refused_as_pending():
    site, row = _o66_site_with_draft()
    result = _o66_preview(site, name=row["name"])
    assert site.only_for_calls == [RATES_ROLES], site.only_for_calls
    assert result["problems"] == [], result["problems"]
    assert set(result) == {"problems", "effect", "current", "pending"}
    # The effect is the one the plain preview gives for the same change.
    assert result["effect"] == _preview(_o55_site())["effect"]
    assert result["current"]["name"] == "OP-ZZ5B1-1"
    _o66_no_write(site)


def test_o66_a_viewer_may_preview_an_edit():
    site, row = _o66_site_with_draft(roles=("EPM User",), user=VIEWER)
    result = _o66_preview(site, name=row["name"])
    assert result["problems"] == []
    assert result["effect"]["first_period"] == "FY2025 P10"
    _o66_no_write(site)


def test_o66_failure_path_another_draft_on_the_node_still_refuses():
    site, row = _o66_site_with_draft()
    site.ops.append(_op("OP-ZZ5B1-E", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=75.0,
                        consolidation_method="full", docstatus=0))
    result = _o66_preview(site, name=row["name"])
    assert result["problems"] == [
        "A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-E): edit that draft."]
    assert result["effect"] is None
    _o66_no_write(site)


def test_o66_failure_path_the_exclude_is_the_exact_name_never_a_pattern():
    site, row = _o66_site_with_draft()
    site.ops.append(_op("OP-ZZ5B1-D2", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=75.0,
                        consolidation_method="full", docstatus=0))
    result = _o66_preview(site, name=row["name"])
    assert result["problems"] == [
        "A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-D2): edit that draft."]


def test_o66_failure_path_the_name_of_an_approved_period_is_refused():
    site = _o55_site()
    site.named["OP-ZZ5B1-1"] = _FakeDoc(dict(site.ops[0], doctype="Ownership Period"))
    result = _o66_preview(site, name="OP-ZZ5B1-1")
    assert result["problems"][0] == "OP-ZZ5B1-1 is approved: record a new change instead."
    assert result["effect"] is None
    _o66_no_write(site)


def test_o66_failure_path_the_name_of_a_cancelled_draft_is_refused():
    site = _o55_site()
    row, doc = _o56_draft(docstatus=2)
    site.ops.append(row)
    site.named[row["name"]] = doc
    result = _o66_preview(site, name=row["name"])
    assert result["problems"][0] == "OP-ZZ5B1-D is cancelled: record a new change instead."
    assert result["effect"] is None


def test_o66_failure_path_another_nodes_draft_is_refused():
    site = _o55_site()
    row, doc = _o56_draft(name="OP-ZZOTHER-D", entity="ZZOTHER")
    site.ops.append(row)
    site.named[row["name"]] = doc
    result = _o66_preview(site, name=row["name"])
    assert result["problems"][0].startswith(
        "OP-ZZOTHER-D is the change for ZZOTHER in ECL_GROUP from 2025-10-01"), result
    assert result["effect"] is None
    _o66_no_write(site)


def test_o66_failure_path_the_draft_previewed_into_another_period_is_refused():
    # The save refuses moving a draft to another period (its name carries the
    # date); the preview says so before the save is tried.
    site, row = _o66_site_with_draft()
    result = _o66_preview(site, fp=11, name=row["name"])
    assert result["problems"][0].startswith(
        "OP-ZZ5B1-D is the change for ZZ5B1 in ECL_GROUP from 2025-10-01"), result
    assert "2025-11-01" in result["problems"][0]
    assert result["effect"] is None


def test_o66_failure_path_a_hidden_entitys_draft_is_refused_without_naming_it():
    site = _o55_site()
    site.allowed = {O55_LEAF}
    row, doc = _o56_draft(name="OP-ZZHIDDEN-D", entity="ZZHIDDEN")
    site.ops.append(row)
    site.named[row["name"]] = doc
    result = _o66_preview(site, name=row["name"])
    assert result["effect"] is None
    assert result["problems"][0] == (
        "OP-ZZHIDDEN-D is not a draft you can edit here: pick a draft of ZZ5B1 in ECL_GROUP "
        "from 2025-10-01.")
    assert not any("ZZHIDDEN " in p or "for ZZHIDDEN" in p for p in result["problems"])


def test_o66_failure_path_a_hidden_entity_with_a_name_throws_before_reading_the_draft():
    site, row = _o66_site_with_draft()
    site.allowed = {"ZZOTHER"}
    with pytest.raises(Exception) as info:
        _o66_preview(site, name=row["name"])
    assert type(info.value).__name__ == "ValidationError", info.value
    assert site.reads == [], site.reads
    assert site.get_doc_calls == [], site.get_doc_calls


def test_o66_failure_path_an_unknown_name_throws():
    site = _o55_site()
    with pytest.raises(Exception) as info:
        _o66_preview(site, name="OP-ZZ-MISSING")
    assert type(info.value).__name__ == "ValidationError", info.value
    assert "OP-ZZ-MISSING" in str(info.value)


def test_o66_failure_path_the_entity_accountant_is_refused_with_a_name():
    site, row = _o66_site_with_draft(roles=("Entity Accountant",), user="zz-ea@example.com")
    with pytest.raises(Exception) as info:
        _o66_preview(site, name=row["name"])
    assert type(info.value).__name__ == "PermissionError", info.value
    assert site.reads == []


# --- O57: get_pending's OP drafts carry their structural effect (story 4.2,
# 4.3; C-O4; #305-4.2-1, #305-Q1-1). The effect is computed by the REAL
# ownership_change.effect_for (loaded in _invoke) and checked against the REAL
# ownership_change_model.effect, loaded by path. ---------------------------------

O57_GOLDEN = os.path.join(FIXTURES, "close_rates_pending_payload.json")
O57_DESK_LEAF = "ZZ5B2"


def _o57_site():
    """The O55 site (ZZ5B1 100 % full from 2025-01-01, approved; P09, P11
    and P13 signed; first close FY2025 P01) plus three drafts: the Analyst's change of ZZ5B1 to 80 %
    full from FY2025 P10 (``supersedes`` the approved period), a Desk
    "Record ownership" draft for ZZ5B2 (no ``supersedes``), and one Historical
    Equity Rate. The Close Lead reads it under Blocked."""
    site = _o55_site(roles=("EPM Admin",), user=LEAD)
    site.policy = "Blocked"
    change = _op("OP-ZZ5B1-2025-10-01", data_area_id=O55_LEAF, group=O55_GROUP,
                 effective_date=date(2025, 10, 1), end_date=None, ownership_pct=80.0,
                 consolidation_method="full", owner=ANALYST,
                 creation=datetime(2025, 10, 2, 9, 0, 0), docstatus=0)
    change["supersedes"] = "OP-ZZ5B1-1"
    change["superseded_end_date"] = None
    desk = _op("OP-ZZ5B2-2025-10-01", data_area_id=O57_DESK_LEAF, group=O55_GROUP,
               effective_date=date(2025, 10, 1), end_date=None, ownership_pct=60.0,
               consolidation_method="equity", owner=ANALYST,
               creation=datetime(2025, 10, 3, 9, 0, 0), docstatus=0)
    site.ops.extend([change, desk])
    site.her = [_her("HER-ZZ5B1-1", data_area_id=O55_LEAF, group=O55_GROUP,
                     creation=datetime(2025, 10, 1, 9, 0, 0))]
    return site


def _o57_item(result, name):
    [item] = [i for i in result["items"] if i["name"] == name]
    return item


def test_o57_pending_matches_the_golden_payload():
    site = _o57_site()
    result = _call_pending(site)
    assert result == _golden(O57_GOLDEN), json.dumps(result, indent=1)


def test_o57_a_change_draft_carries_the_real_models_effect():
    site = _o57_site()
    result = _call_pending(site)
    item = _o57_item(result, "OP-ZZ5B1-2025-10-01")
    expected = _o55_model().effect(
        {"entity": O55_LEAF, "effective_date": "2025-10-01", "ownership_pct": 80.0,
         "consolidation_method": "full"},
        {"name": "OP-ZZ5B1-1", "effective_date": "2025-01-01", "end_date": None,
         "ownership_pct": 100.0, "consolidation_method": "full"},
        _o55_calendar(), _o64_signatures(site, {(2025, 11)}))
    assert item["ownership_effect"] == expected
    assert item["ownership_effect"]["current_name"] == "OP-ZZ5B1-1"
    assert [d["period"] for d in item["ownership_effect"]["resign_detail"]] == item["ownership_effect"]["resign"]
    assert item["ownership_effect"]["before"]["pct"] == 100.0 and item["ownership_effect"]["after"]["pct"] == 80.0
    assert item["ownership_effect"]["current_ends"] == "2025-09-30"
    assert item["ownership_effect"]["resign"] == ["FY2025 P11"]
    # The detail text is unchanged (O57 goal).
    assert item["detail"] == "80% · full"
    assert item["approve"]["mode"] == "direct"


def test_o57_failure_path_a_desk_draft_without_supersedes_has_effect_none():
    """A Desk "Record ownership" draft names no predecessor: its effect is
    None (the screen says "Drafted in Desk: effect not previewed"), never a
    guessed before/after."""
    site = _o57_site()
    result = _call_pending(site)
    item = _o57_item(result, "OP-ZZ5B2-2025-10-01")
    assert "ownership_effect" in item and item["ownership_effect"] is None
    assert item["detail"] == "60% · equity"


def test_o57_her_items_carry_no_effect():
    result = _call_pending(_o57_site())
    item = _o57_item(result, "HER-ZZ5B1-1")
    assert "ownership_effect" not in item


R52I_BROKEN = "OP-ZZ5B3-2025-10-01"
R52I_BROKEN_PRED = "OP-ZZ5B3-1"


def _r52i_site():
    """R52i (review S2): the O57 site plus a second change draft, for ZZ5B3,
    whose ``supersedes`` names a period cancelled after the draft was saved."""
    site = _o57_site()
    site.ops.append(_op(R52I_BROKEN_PRED, data_area_id="ZZ5B3", group=O55_GROUP,
                        effective_date=date(2025, 1, 1), end_date=None, ownership_pct=100.0,
                        consolidation_method="full", owner=LEAD, docstatus=2))
    broken = _op(R52I_BROKEN, data_area_id="ZZ5B3", group=O55_GROUP,
                 effective_date=date(2025, 10, 1), end_date=None, ownership_pct=70.0,
                 consolidation_method="full", owner=ANALYST,
                 creation=datetime(2025, 10, 4, 9, 0, 0), docstatus=0)
    broken["supersedes"] = R52I_BROKEN_PRED
    broken["superseded_end_date"] = None
    site.ops.append(broken)
    return site


def test_r52i_failure_path_a_cancelled_supersedes_is_an_error_on_that_item_only():
    """Corrupt data (the named predecessor is not an approved period) is an
    error on that one item, naming the draft and the predecessor, never a
    guessed effect and never a refusal of the whole screen (review S2)."""
    result = _call_pending(_r52i_site())
    broken = _o57_item(result, R52I_BROKEN)
    assert broken["ownership_effect"] is None
    assert broken["ownership_effect_error"].startswith(
        "The pending ownership change %s cannot be shown: " % R52I_BROKEN), broken["ownership_effect_error"]
    assert R52I_BROKEN_PRED in broken["ownership_effect_error"], broken["ownership_effect_error"]
    assert ".." not in broken["ownership_effect_error"], broken["ownership_effect_error"]
    assert broken["ownership_effect_error"].endswith(". Correct or delete the draft in Desk."), \
        broken["ownership_effect_error"]
    assert broken["detail"] == "70% · full"
    # The good draft is built as normal: the same item as without the broken one.
    good = _o57_item(result, "OP-ZZ5B1-2025-10-01")
    alone = _o57_item(_call_pending(_o57_site()), "OP-ZZ5B1-2025-10-01")
    assert good == alone
    assert good["ownership_effect"]["current_name"] == "OP-ZZ5B1-1"
    assert good["ownership_effect_error"] is None
    # Every draft is listed and counted.
    assert sorted(i["name"] for i in result["items"]) == sorted(
        ["OP-ZZ5B1-2025-10-01", "OP-ZZ5B2-2025-10-01", R52I_BROKEN, "HER-ZZ5B1-1"])
    assert result["counts"]["Ownership Period"] == 3


def test_r52i_every_op_item_carries_effect_error_and_her_items_none():
    result = _call_pending(_o57_site())
    for item in result["items"]:
        if item["doctype"] == "Ownership Period":
            assert "ownership_effect_error" in item and item["ownership_effect_error"] is None, item
        else:
            assert "ownership_effect_error" not in item, item


def test_o57_reads_grow_only_for_a_change_draft_and_nothing_is_written():
    """Each visible OP draft with ``supersedes`` costs ``effect_for``'s reads
    (its predecessor and the calendar; the signed runs are stubbed here); a
    Desk draft costs none."""
    desk_only = _o57_site()
    desk_only.ops = [r for r in desk_only.ops if not r.get("supersedes")]
    with_change = _o57_site()
    _call_pending(desk_only)
    _call_pending(with_change)
    assert len(desk_only.reads) == 7, desk_only.reads
    # O64: + one User read for the signers' full names. O69: + one calendar
    # read per call for the Close Lead's ``edit`` (EPM Admin may save).
    assert len(with_change.reads) == 11, with_change.reads
    extra = list(with_change.reads)
    for read in desk_only.reads:
        extra.remove(read)
    assert sorted(extra) == sorted([("get_all", "Ownership Period"),
                                    ("sql", "fiscal_period_rows"),
                                    ("sql", "fiscal_period_rows"),
                                    ("get_all", "User")]), extra
    assert with_change.new_docs == [] and with_change.get_doc_calls == []


def test_o57_failure_path_a_hidden_draft_is_never_read_for_its_effect():
    site = _o57_site()
    site.allowed = {O57_DESK_LEAF}
    result = _call_pending(site)
    assert [i["name"] for i in result["items"]] == ["OP-ZZ5B2-2025-10-01"]
    assert result["counts"]["hidden"] == 2
    # Only the one draft list read: no predecessor, no calendar.
    assert site.reads.count(("get_all", "Ownership Period")) == 1, site.reads
    assert ("sql", "fiscal_period_rows") not in site.reads, site.reads
    assert O55_LEAF not in json.dumps(result)


# --- O63: get_ownership carries the change form's choices (story 4.2;
# #305-4.2-1; C-O2, C-O3; wireframe-4.2.md section 1). Entities come with
# their node's own group (one item per node, never a guessed one); periods are
# the Open Regular ones, read from the one calendar read. -------------------

O63_GOLDEN = os.path.join(FIXTURES, "close_ownership_payload.json")
O63_SUB = "ZZ_SUBGROUP"


def _o63_period(fy, fp, status, period_type="Regular", start=None):
    start = start or date(fy, fp, 1)
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_label": "P%02d" % fp, "period_type": period_type,
            "start_date": start, "end_date": start, "quarter": "", "status": status}


def _o63_site(roles=("EPM Analyst",), user=ANALYST):
    """FY2026 P01 is listed first in the stub calendar, so calendar order is
    the endpoint's, not the stub's. FY2025 P09 is Closed, P13 is an Open
    Closing period: neither is a choice. ZZ5B1 sits on two nodes (ECL_GROUP
    and ZZ_SUBGROUP; ECL_GROUP twice, an older closed period and the current
    one). ZZ5B2 has only a draft (C-O3: a first ownership stays in Desk).
    ZZ5B3 has a submitted period that starts later. The group node's own
    period has no entity."""
    site = _Site()
    site.user = user
    site.roles = set(roles)
    site.periods = [_o63_period(2026, 1, "Open"), _o63_period(2025, 9, "Closed"),
                    _o63_period(2025, 10, "Open"),
                    _o63_period(2025, 13, "Open", "Closing", date(2025, 12, 31))]
    site.entities = [dict(_entity("ZZ5B1"), entity_name="ZZ Five B One"),
                     dict(_entity("ZZ5B2"), entity_name="ZZ Five B Two"),
                     dict(_entity("ZZ5B3"), entity_name="ZZ Five B Three")]
    site.ops = [
        _op("OP-ZZ5B1-0", data_area_id=O55_LEAF, group=O55_GROUP,
            effective_date=date(2024, 1, 1), end_date=date(2024, 12, 31), docstatus=1),
        _op("OP-ZZ5B1-1", data_area_id=O55_LEAF, group=O55_GROUP,
            effective_date=date(2025, 1, 1), docstatus=1),
        _op("OP-ZZ5B1-SUB", data_area_id=O55_LEAF, group=O63_SUB,
            effective_date=date(2025, 1, 1), docstatus=1),
        _op("OP-ZZ5B2-D", data_area_id="ZZ5B2", group=O55_GROUP,
            effective_date=date(2025, 1, 1), docstatus=0),
        _op("OP-ZZ5B3-1", data_area_id="ZZ5B3", group=O55_GROUP,
            effective_date=date(2025, 12, 1), docstatus=1),
        _op("OP-GROUP-1", data_area_id=None, group=O55_GROUP,
            effective_date=date(2025, 1, 1), docstatus=1),
    ]
    site.tbs = [_tb("ZZ5B1", fp=10)]
    return site


def _o63_call(site):
    return _call_ownership(site, 2025, 10)


def test_o63_ownership_matches_the_golden_payload():
    site = _o63_site()
    result = _o63_call(site)
    assert result == _golden(O63_GOLDEN), json.dumps(result, indent=1)


def test_o63_change_entities_carry_their_nodes_group_one_item_per_node():
    result = _o63_call(_o63_site())
    assert [{k: e[k] for k in ("entity", "entity_name", "consolidation_group")}
            for e in result["change"]["entities"]] == [
        {"entity": "ZZ5B1", "entity_name": "ZZ Five B One", "consolidation_group": O55_GROUP},
        {"entity": "ZZ5B1", "entity_name": "ZZ Five B One", "consolidation_group": O63_SUB},
        {"entity": "ZZ5B3", "entity_name": "ZZ Five B Three",
         "consolidation_group": O55_GROUP},
    ]


def test_o63_change_periods_are_only_the_open_regular_ones_in_calendar_order():
    result = _o63_call(_o63_site())
    assert result["change"]["periods"] == [
        {"fiscal_year": 2025, "fiscal_period": 10, "label": "FY2025 P10",
         "start_date": "2025-10-01"},
        {"fiscal_year": 2026, "fiscal_period": 1, "label": "FY2026 P01",
         "start_date": "2026-01-01"},
    ]


def test_o63_the_calendar_is_read_once():
    site = _o63_site()
    _o63_call(site)
    assert site.reads.count(("sql", "fiscal_period_rows")) == 1, site.reads


def test_o63_failure_path_a_viewer_gets_change_none_and_no_extra_read():
    site = _o63_site(roles=("EPM User",), user=VIEWER)
    site.can_record = False
    result = _o63_call(site)
    assert result["can_record"] is False
    assert "change" in result and result["change"] is None
    assert len(site.reads) == 4, site.reads


def test_o63_failure_path_a_scoped_caller_never_sees_an_entity_outside_scope():
    site = _o63_site()
    site.allowed = {"ZZ5B3"}
    result = _o63_call(site)
    assert [{k: e[k] for k in ("entity", "entity_name", "consolidation_group")}
            for e in result["change"]["entities"]] == [
        {"entity": "ZZ5B3", "entity_name": "ZZ Five B Three",
         "consolidation_group": O55_GROUP}]
    # The existing hidden rule counts out-of-scope ZZ5B2 only; the change list
    # adds nothing to it.
    assert result["hidden"] == 1
    assert "ZZ5B1" not in json.dumps(result)


def test_o63_failure_path_an_entity_on_two_nodes_gives_two_items():
    result = _o63_call(_o63_site())
    groups = [e["consolidation_group"] for e in result["change"]["entities"]
              if e["entity"] == "ZZ5B1"]
    assert groups == [O55_GROUP, O63_SUB]


def test_o63_reads_are_constant_in_the_number_of_nodes():
    small = _o63_site()
    big = _o63_site()
    big.entities = big.entities + [dict(_entity("ZZN%02d" % i), entity_name="N%d" % i)
                                   for i in range(20)]
    big.ops = big.ops + [_op("OP-ZZN%02d" % i, data_area_id="ZZN%02d" % i,
                             group=O55_GROUP, effective_date=date(2025, 1, 1), docstatus=1)
                         for i in range(20)]
    _o63_call(small)
    _o63_call(big)
    assert len(small.reads) == 6, small.reads
    assert len(big.reads) == len(small.reads), big.reads


# --- R52j (review U4; wireframe-4.2.md section 1, the "Currently" line
# under Group): each change-form entity item carries ``current``, its node's
# latest SUBMITTED Ownership Period by effective_date, in
# ``ownership_change._as_current``'s shape, read in the same nodes read. ----


def _r52j_current(name, effective_date, end_date=None, pct=60.0, method="Equity"):
    """Written out independently of the code under test."""
    return {"name": name, "effective_date": effective_date, "end_date": end_date,
            "ownership_pct": pct, "consolidation_method": method}


def _r52j_item(result, entity, group):
    items = [e for e in result["change"]["entities"]
             if (e["entity"], e["consolidation_group"]) == (entity, group)]
    assert len(items) == 1, result["change"]["entities"]
    return items[0]


def test_r52j_failure_path_every_entity_item_carries_current():
    # Red at 843cdf7: the items carry no ``current`` (KeyError: current).
    result = _o63_call(_o63_site())
    for item in result["change"]["entities"]:
        assert "current" in item, item
        item["current"]


def test_r52j_a_node_with_an_ended_and_an_open_period_gives_the_open_one():
    site = _o63_site()
    # Listed newest first, so the answer is not the stub's row order.
    site.ops = list(reversed(site.ops))
    result = _o63_call(site)
    assert _r52j_item(result, O55_LEAF, O55_GROUP)["current"] == _r52j_current(
        "OP-ZZ5B1-1", "2025-01-01")


def test_r52j_failure_path_a_later_draft_on_the_node_is_not_current():
    site = _o63_site()
    site.ops.append(_op("OP-ZZ5B1-D", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 10, 1), ownership_pct=80.0, docstatus=0))
    result = _o63_call(site)
    assert _r52j_item(result, O55_LEAF, O55_GROUP)["current"]["name"] == "OP-ZZ5B1-1"


def test_r52j_a_node_with_one_period_gives_that_period():
    result = _o63_call(_o63_site())
    assert _r52j_item(result, O55_LEAF, O63_SUB)["current"] == _r52j_current(
        "OP-ZZ5B1-SUB", "2025-01-01")
    assert _r52j_item(result, "ZZ5B3", O55_GROUP)["current"] == _r52j_current(
        "OP-ZZ5B3-1", "2025-12-01")


def test_r52j_an_ended_latest_period_carries_its_end_date_as_iso():
    site = _o63_site()
    site.ops = [r for r in site.ops if r["name"] != "OP-ZZ5B1-1"]
    result = _o63_call(site)
    assert _r52j_item(result, O55_LEAF, O55_GROUP)["current"] == _r52j_current(
        "OP-ZZ5B1-0", "2024-01-01", "2024-12-31")


def test_r52j_failure_path_a_hidden_entitys_item_and_period_stay_cut():
    site = _o63_site()
    site.allowed = {"ZZ5B3"}
    result = _o63_call(site)
    assert [(e["entity"], e["current"]["name"]) for e in result["change"]["entities"]] == [
        ("ZZ5B3", "OP-ZZ5B3-1")]
    text = json.dumps(result)
    assert "ZZ5B1" not in text and "OP-ZZ5B1" not in text


def test_r52j_no_new_read():
    site = _o63_site()
    _o63_call(site)
    assert len(site.reads) == 6, site.reads
    assert site.reads.count(("get_all", "Ownership Period")) == 2, site.reads


# --- O68: save_ownership_change(name=<a hidden entity's draft>) never names that entity ---
# The scope check in ``_ownership_preview`` covers the ``entity`` argument
# only; the named draft's own entity is checked by one helper the preview
# (O66) and the save share, so both give one sentence.

O68_HIDDEN_SENTENCE = ("OP-ZZHIDDEN-D is not a draft you can edit here: pick a draft of ZZ5B1 "
                       "in ECL_GROUP from 2025-10-01.")


def _o68_site_with_hidden(docstatus=0):
    site = _o55_site()
    site.allowed = {O55_LEAF}
    row, doc = _o56_draft(name="OP-ZZHIDDEN-D", entity="ZZHIDDEN", docstatus=docstatus)
    site.ops.append(row)
    site.named[row["name"]] = doc
    return site, row


def _o68_assert_hides(err):
    text = str(err)
    assert "ZZHIDDEN " not in text and "for ZZHIDDEN" not in text, text


def test_o68_failure_path_saving_a_hidden_entitys_draft_never_names_it():
    site, row = _o68_site_with_hidden()
    err = _o56_save_raises(site, name=row["name"])
    assert type(err).__name__ == "ValidationError", err
    assert str(err) == O68_HIDDEN_SENTENCE, err
    _o68_assert_hides(err)
    _no_save(site)


def test_o68_failure_path_a_hidden_approved_period_is_refused_with_the_same_sentence():
    # Scope comes before status: "is approved" would confirm the hidden
    # name exists as an approved period.
    site, row = _o68_site_with_hidden(docstatus=1)
    err = _o56_save_raises(site, name=row["name"])
    assert str(err) == O68_HIDDEN_SENTENCE, err
    _no_save(site)


def test_o68_save_and_preview_give_one_sentence():
    site, row = _o68_site_with_hidden()
    err = _o56_save_raises(site, name=row["name"])
    site2, _ = _o68_site_with_hidden()
    result = _o66_preview(site2, name=row["name"])
    assert result["problems"][0] == str(err)


def test_o68_a_visible_other_nodes_draft_still_gets_the_edit_refusal():
    site = _o55_site()
    site.allowed = {O55_LEAF, "ZZOTHER"}
    row, doc = _o56_draft(name="OP-ZZOTHER-D", entity="ZZOTHER")
    site.ops.append(row)
    site.named[row["name"]] = doc
    err = _o56_save_raises(site, name=row["name"])
    assert type(err).__name__ == "ValidationError", err
    assert "OP-ZZOTHER-D is the change for ZZOTHER in ECL_GROUP from 2025-10-01" in str(err), err
    _no_save(site)


def test_o68_an_unscoped_caller_still_gets_the_edit_refusal():
    # allowed None (no Entity permission at all) sees every entity.
    site = _o55_site()
    row, doc = _o56_draft(name="OP-ZZHIDDEN-D", entity="ZZHIDDEN")
    site.ops.append(row)
    site.named[row["name"]] = doc
    err = _o56_save_raises(site, name=row["name"])
    assert "OP-ZZHIDDEN-D is the change for ZZHIDDEN in ECL_GROUP" in str(err), err
    _no_save(site)


# --- O69: the pending OP item says how to edit it; the preview names the awaiting drafts ---
# Story 4.2; wireframe-4.2.md §1 ("The Analyst can edit it until it is
# approved", confirmed by Deepak Pai 7 Oct). O67's Edit is fed by the server:
# ``edit`` is never derived by the client from a title, a sentence or
# ``get_ownership.can_record`` (a different test from the save's roles).

O69_CHANGE = "OP-ZZ5B1-2025-10-01"
O69_EDIT = {"consolidation_group": O55_GROUP, "entity": O55_LEAF, "fiscal_year": 2025,
            "fiscal_period": 10, "ownership_pct": 80.0, "consolidation_method": "full"}


def _o69_site(roles=("EPM Analyst",), user=ANALYST):
    site = _o57_site()
    site.roles = set(roles)
    site.user = user
    return site


def _o69_calendar_reads(site):
    return site.reads.count(("sql", "fiscal_period_rows"))


def test_o69_an_analysts_change_draft_carries_its_edit_and_a_desk_draft_none():
    result = _call_pending(_o69_site())
    assert _o57_item(result, O69_CHANGE)["edit"] == O69_EDIT
    desk = _o57_item(result, "OP-ZZ5B2-2025-10-01")
    assert "edit" in desk and desk["edit"] is None
    # HER items are not ownership drafts: no key.
    assert "edit" not in _o57_item(result, "HER-ZZ5B1-1")
    # The title and detail are unchanged; the client never parses them.
    item = _o57_item(result, O69_CHANGE)
    assert item["title"] == "ZZ5B1 in ECL_GROUP from 2025-10-01"
    assert item["detail"] == "80% · full"


def test_o69_every_role_save_admits_gets_the_edit():
    for role in ("EPM Analyst", "EPM Admin", "System Manager"):
        result = _call_pending(_o69_site(roles=(role,)))
        assert _o57_item(result, O69_CHANGE)["edit"] == O69_EDIT, role


def test_o69_the_edit_roles_are_the_saves_literal_role_tuple():
    import ast

    with open(API_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "save_ownership_change")
    first = fn.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        first = fn.body[1]
    literal = tuple(e.value for e in first.value.args[0].elts)
    api = _load_rates_api(_Site())
    assert tuple(api.OWNERSHIP_SAVE_ROLES) == literal == O56_SAVE_ROLES


def test_o69_the_edit_fed_back_to_the_save_edits_that_draft_without_a_refusal():
    """The server's ``edit`` plus the item's ``name`` is a save the save
    accepts: the client never re-derives the node, period, pct or method."""
    site = _o69_site()
    item = _o57_item(_call_pending(site), O69_CHANGE)
    edit = item["edit"]
    [row] = [r for r in site.ops if r["name"] == O69_CHANGE]
    doc = _FakeDoc(dict(row, doctype="Ownership Period"))
    site.named[O69_CHANGE] = doc
    result = _invoke(site, lambda api: api.save_ownership_change(
        edit["fiscal_year"], edit["fiscal_period"], edit["consolidation_group"],
        edit["entity"], edit["ownership_pct"], edit["consolidation_method"],
        name=item["name"]))
    assert result == {"name": O69_CHANGE, "docstatus": 0}
    assert [c[0] for c in doc.calls] == ["save"]
    assert site.new_docs == []


def test_o69_failure_path_a_viewer_gets_edit_none_on_every_item_and_no_calendar_read():
    viewer = _o69_site(roles=("EPM User",), user=VIEWER)
    lead = _o69_site()
    result = _call_pending(viewer)
    ops = [i for i in result["items"] if i["doctype"] == "Ownership Period"]
    assert len(ops) == 2
    assert all("edit" in i and i["edit"] is None for i in ops), ops
    _call_pending(lead)
    # The Analyst's one extra read is the calendar, read once.
    assert _o69_calendar_reads(lead) - _o69_calendar_reads(viewer) == 1


def test_o69_failure_path_no_regular_period_starting_on_the_date_gives_edit_none():
    """Two Regular periods declared on the draft's first day: the period is
    ambiguous, so ``edit`` is None, never a guessed one. (A date that starts
    no Regular period at all is already refused by O57's effect.)"""
    site = _o69_site()
    site.periods = site.periods + [{
        "fiscal_year": 2026, "fiscal_period": 1, "period_code": "P01", "period_label": "P01",
        "period_type": "Regular", "start_date": date(2025, 10, 1),
        "end_date": date(2025, 10, 31), "quarter": "Q1", "status": "Open"}]
    result = _call_pending(site)
    assert _o57_item(result, O69_CHANGE)["edit"] is None


def test_o69_failure_path_the_calendar_is_read_once_for_n_change_drafts():
    one = _o69_site()
    many = _o69_site()
    for i in range(4):
        extra = dict(next(r for r in many.ops if r["name"] == O69_CHANGE),
                     name="OP-ZZ5B1-X%d" % i, creation=datetime(2025, 10, 4 + i, 9, 0, 0))
        many.ops.append(extra)
    viewer_one = _o69_site(roles=("EPM User",), user=VIEWER)
    viewer_many = _o69_site(roles=("EPM User",), user=VIEWER)
    viewer_many.ops = [dict(r) for r in many.ops]
    for s in (one, many, viewer_one, viewer_many):
        _call_pending(s)
    # effect_for (O57) reads the calendar per change draft; O69 adds one read
    # per call, whatever the number of drafts.
    assert _o69_calendar_reads(one) - _o69_calendar_reads(viewer_one) == 1
    assert _o69_calendar_reads(many) - _o69_calendar_reads(viewer_many) == 1


def test_o69_failure_path_no_change_draft_means_no_calendar_read():
    site = _o69_site()
    site.ops = [r for r in site.ops if not r.get("supersedes")]
    _call_pending(site)
    assert _o69_calendar_reads(site) == 0, site.reads


def test_o69_failure_path_a_scoped_analyst_sees_only_visible_drafts_with_edit():
    site = _o69_site()
    site.allowed = {O55_LEAF}
    result = _call_pending(site)
    ops = [i for i in result["items"] if i["doctype"] == "Ownership Period"]
    assert [i["name"] for i in ops] == [O69_CHANGE]
    assert ops[0]["edit"] == O69_EDIT
    assert O57_DESK_LEAF not in json.dumps(result)


def test_o69_preview_names_the_other_awaiting_draft_as_data_beside_the_sentence():
    site, row = _o66_site_with_draft()
    result = _o66_preview(site)
    assert result["pending"] == [row["name"]]
    assert result["problems"] == [
        "A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-D): edit that draft."]


def test_o69_preview_of_that_draft_by_name_has_pending_empty():
    site, row = _o66_site_with_draft()
    result = _o66_preview(site, name=row["name"])
    assert result["pending"] == []
    assert result["problems"] == []


def test_o69_preview_with_no_draft_has_pending_empty_never_missing():
    result = _preview(_o55_site())
    assert "pending" in result and result["pending"] == []


def test_o69_failure_path_editing_one_draft_still_names_the_others_sorted():
    site, row = _o66_site_with_draft()
    for name in ("OP-ZZ5B1-F", "OP-ZZ5B1-E"):
        site.ops.append(_op(name, data_area_id=O55_LEAF, group=O55_GROUP,
                            effective_date=date(2025, 11, 1), ownership_pct=75.0,
                            consolidation_method="full", docstatus=0))
    result = _o66_preview(site, name=row["name"])
    assert result["pending"] == ["OP-ZZ5B1-E", "OP-ZZ5B1-F"]
    assert result["effect"] is None


def test_o69_failure_path_another_nodes_draft_is_never_in_pending():
    site = _o55_site()
    site.ops.append(_op("OP-ZZ5B2-D", data_area_id="ZZ5B2", group=O55_GROUP,
                        effective_date=date(2025, 10, 1), docstatus=0))
    result = _preview(site)
    assert result["pending"] == [] and result["problems"] == []


def test_o69_the_preview_goldens_carry_pending():
    assert _golden(O55_GOLDEN)["pending"] == []
    assert _golden(O55_GOLDEN_REFUSED)["pending"] == ["OP-ZZ5B1-2"]


def test_o69_the_pending_golden_carries_edit_on_every_op_item():
    golden = _golden(O57_GOLDEN)
    ops = {i["name"]: i for i in golden["items"] if i["doctype"] == "Ownership Period"}
    assert set(ops) == {O69_CHANGE, "OP-ZZ5B2-2025-10-01"}
    # The golden is the Close Lead's call: EPM Admin may save.
    assert ops[O69_CHANGE]["edit"] == O69_EDIT
    assert ops["OP-ZZ5B2-2025-10-01"]["edit"] is None


# --- R52q (review S18): the pending list's OP items carry the ownership keys ----


def test_r52q_pending_op_items_carry_ownership_effect_and_no_effect():
    result = _call_pending(_o57_site())
    ops = {i["name"]: i for i in result["items"] if i["doctype"] == "Ownership Period"}
    assert sorted(ops) == ["OP-ZZ5B1-2025-10-01", "OP-ZZ5B2-2025-10-01"]
    assert ops["OP-ZZ5B1-2025-10-01"]["ownership_effect"]["current_name"] == "OP-ZZ5B1-1"
    assert ops["OP-ZZ5B2-2025-10-01"]["ownership_effect"] is None
    for item in ops.values():
        assert item["ownership_effect_error"] is None, item
        assert "effect" not in item and "effect_error" not in item, sorted(item)
    for item in result["items"]:
        if item["doctype"] != "Ownership Period":
            assert "ownership_effect" not in item and "ownership_effect_error" not in item, item


def test_r52q_failure_path_a_broken_draft_names_its_error_under_the_ownership_key():
    result = _call_pending(_r52i_site())
    [broken] = [i for i in result["items"] if i["name"] == R52I_BROKEN]
    assert broken["ownership_effect"] is None
    assert broken["ownership_effect_error"].startswith(
        "The pending ownership change %s cannot be shown: " % R52I_BROKEN), broken
    assert "effect" not in broken and "effect_error" not in broken, sorted(broken)


# --- R53e (#305-R52-4, U10e; Deepak Pai 7 Oct): get_ownership sends one
# ``can_change``, true exactly when ``save_ownership_change`` would admit the
# caller: a role in the save's literal tuple (OWNERSHIP_SAVE_ROLES), and at
# least one Open Regular period to start a change in (the save refuses any
# other period). Never Ownership Period create permission (``can_record``),
# which a DocPerm customisation can move. ``change`` is sent exactly when
# ``can_change`` is true, so the form never mounts without its choices. ----


def test_r53e_every_role_the_save_admits_gets_can_change_true():
    for role in ("EPM Analyst", "EPM Admin", "System Manager"):
        result = _o63_call(_o63_site(roles=(role,)))
        assert result["can_change"] is True, role
        assert result["change"] is not None, role


def test_r53e_failure_path_a_viewer_gets_can_change_false_and_no_change():
    site = _o63_site(roles=("EPM User",), user=VIEWER)
    site.can_record = False
    result = _o63_call(site)
    assert result["can_change"] is False
    assert result["change"] is None


def test_r53e_failure_path_a_viewer_granted_op_create_still_gets_can_change_false():
    """A role customisation granting Ownership Period create to the Viewer
    moves ``can_record`` (the Desk link) but never ``can_change``: the save
    would still refuse them."""
    site = _o63_site(roles=("EPM User",), user=VIEWER)
    site.can_record = True
    result = _o63_call(site)
    assert result["can_record"] is True
    assert result["can_change"] is False
    assert result["change"] is None


def test_r53e_failure_path_an_analyst_without_op_create_still_gets_can_change_true():
    """The other direction of the same customisation: the save admits the
    Analyst by role, so the form is offered with its choices."""
    site = _o63_site()
    site.can_record = False
    result = _o63_call(site)
    assert result["can_record"] is False
    assert result["can_change"] is True
    assert [p["label"] for p in result["change"]["periods"]] == ["FY2025 P10", "FY2026 P01"]


def test_r53e_failure_path_no_open_regular_period_gives_can_change_false():
    """The save refuses a change starting in any period that is not an Open
    Regular one, so with none open there is nothing the save would accept."""
    site = _o63_site()
    site.periods = [_o63_period(2025, 9, "Closed"), _o63_period(2025, 10, "Closed"),
                    _o63_period(2025, 13, "Open", "Closing", date(2025, 12, 31))]
    result = _o63_call(site)
    assert result["can_change"] is False
    assert result["change"] is None


def test_r53e_the_golden_carries_can_change():
    golden = _golden(O63_GOLDEN)
    assert golden["can_change"] is True
    assert golden["change"] is not None


def test_r53e_the_servers_decision_fed_back_to_the_save():
    """Where ``can_change`` is true the save admits the caller with a period
    and node from ``change``; where it is false the save refuses by role."""
    for roles, user in ((("EPM Analyst",), ANALYST), (("EPM Admin",), LEAD),
                        (("System Manager",), LEAD), (("EPM User",), VIEWER)):
        site = _o63_site(roles=roles, user=user)
        site.can_record = True
        result = _o63_call(site)
        if result["can_change"]:
            period = result["change"]["periods"][0]
            node = result["change"]["entities"][0]
            fresh = _o63_site(roles=roles, user=user)
            saved = _invoke(fresh, lambda api: api.save_ownership_change(
                period["fiscal_year"], period["fiscal_period"], node["consolidation_group"],
                node["entity"], "80", "full"))
            assert saved["docstatus"] == 0, roles
            assert len(fresh.new_docs) == 1, roles
        else:
            fresh = _o63_site(roles=roles, user=user)
            with pytest.raises(Exception) as info:
                _invoke(fresh, lambda api: api.save_ownership_change(
                    2025, 10, O55_GROUP, O55_LEAF, "80", "full"))
            assert type(info.value).__name__ == "PermissionError", (roles, info.value)
            assert fresh.new_docs == [], roles
    # Both branches ran.
    assert _o63_call(_o63_site(roles=("EPM User",), user=VIEWER))["can_change"] is False


# --- F11 (konsol#312: #312-3, #312-3b, Deepak Pai 9 and 12 Oct 2026): the
# close app drafts a correction of an approved rate. ``save_rate`` takes
# ``supersedes`` (the approved rate of the same key) and saves a draft beside
# it; the Group Exchange Rate controller (F10) cancels the old rate when the
# correction is approved. ``get_rates`` says per cell whether the caller may
# correct it (``correct``) and carries a pending correction with its
# before -> after and approve mode. R2: the Analyst drafts, the Close Lead
# approves; the save's own roles (Analyst, Close Lead, System Manager) draft
# a correction too. -----------------------------------------------------------

F11_GOLDEN = os.path.join(FIXTURES, "close_rates_correction_payload.json")
F11_REASON = "ECB fixing corrected 31 Jul"


def _f11_correction_site():
    """Approved Closing EUR → GBP GER-1 (0.85); nothing pending on it."""
    site = _analyst_site()
    site.docs = [_ger("GER-1", "EUR", "GBP", "Closing", 0.85, 1, owner=LEAD)]
    return site


def test_f11_correct_an_approved_rate_saves_a_draft_that_supersedes_it():
    site = _f11_correction_site()
    result = _save(site, from_currency="EUR", quote=0.86, change_reason="  " + F11_REASON + " ",
                   supersedes="GER-1")
    dicts = [c for c in site.get_doc_calls if isinstance(c, dict)]
    assert dicts == [{"doctype": "Group Exchange Rate", "to_currency": "GBP",
                      "from_currency": "EUR", "rate_type": "Closing", "fiscal_year": 2025,
                      "fiscal_period": 7, "quote": 0.86, "quoted_per": "1",
                      "change_reason": F11_REASON, "supersedes": "GER-1"}], dicts
    doc = site.new_docs[0]
    assert [c[0] for c in doc.calls] == ["insert"]
    _no_ignore_flags(site, doc)
    assert result == {"name": "GER-NEW", "docstatus": 0, "quote_label": doc.quote_label,
                      "source": "Manual"}
    assert site.only_for_calls == [("EPM Analyst", "EPM Admin", "System Manager")]
    assert site.reads == [("sql", "fiscal_period_rows"), ("sql", FR3_LOCKING_READ)]


def test_f11_the_close_lead_may_draft_a_correction_too():
    site = _f11_correction_site()
    site.user, site.roles = LEAD, {"EPM Admin"}
    result = _save(site, from_currency="EUR", quote=0.86, change_reason=F11_REASON,
                   supersedes="GER-1")
    assert result["name"] == "GER-NEW"


def test_f11_failure_path_a_viewer_and_an_entity_accountant_cannot_correct():
    for role in ("EPM User", "Entity Accountant"):
        site = _f11_correction_site()
        site.user, site.roles = VIEWER, {role}
        err = _save_raises(site, from_currency="EUR", quote=0.86, change_reason=F11_REASON,
                           supersedes="GER-1")
        assert type(err).__name__ == "PermissionError", (role, err)
        assert site.reads == [] and site.get_doc_calls == [] and site.new_docs == []


def test_f11_failure_path_a_closed_period_is_refused_before_any_read_of_rates():
    site = _f11_correction_site()
    site.docs = [dict(site.docs[0], fiscal_period=6)]
    err = _save_raises(site, from_currency="EUR", fiscal_period=6, quote=0.86,
                       change_reason=F11_REASON, supersedes="GER-1")
    assert str(err) == ("FY2025 P06 is Closed: group rates lock when their period closes "
                        "(reopen it first)."), err
    assert site.reads == [("sql", "fiscal_period_rows")], site.reads
    assert site.new_docs == []


def test_f11_failure_path_a_second_correction_while_one_is_pending_is_refused_naming_it():
    site = _f11_correction_site()
    site.docs.append(_ger("GER-3", "EUR", "GBP", "Closing", 0.86, 0, supersedes="GER-1",
                          change_reason=F11_REASON))
    err = _save_raises(site, from_currency="EUR", quote=0.87, change_reason="again",
                       supersedes="GER-1")
    assert str(err) == "GER-3 already corrects GER-1 and awaits approval: edit it.", err
    assert site.new_docs == []


def test_f11_failure_path_a_plain_draft_of_the_key_refuses_a_correction_naming_it():
    site = _f11_correction_site()
    site.docs.append(_ger("GER-3", "EUR", "GBP", "Closing", 0.86, 0))
    err = _save_raises(site, from_currency="EUR", quote=0.87, change_reason="x",
                       supersedes="GER-1")
    assert str(err) == "GER-3 is already a draft for this rate: edit it.", err
    assert site.new_docs == []


def test_f11_forge_supersedes_must_be_the_approved_rate_of_the_asked_key():
    # A draft, a cancelled rate, another key's rate and an unknown name are
    # each refused before any write; the sentence names the rate to correct.
    cases = [
        ("GER-D", "GER-D is not the approved Closing rate EUR → GBP for FY2025 P07: "
                  "correct GER-1."),
        ("GER-C", "GER-C is not the approved Closing rate EUR → GBP for FY2025 P07: "
                  "correct GER-1."),
        ("GER-A", "GER-A is not the approved Closing rate EUR → GBP for FY2025 P07: "
                  "correct GER-1."),
        ("GER-NOPE", "GER-NOPE is not the approved Closing rate EUR → GBP for FY2025 P07: "
                     "correct GER-1."),
    ]
    for supersedes, sentence in cases:
        site = _f11_correction_site()
        site.docs += [_ger("GER-C", "EUR", "GBP", "Closing", 0.84, 2),
                      _ger("GER-A", "EUR", "GBP", "Average", 0.84, 1)]
        if supersedes == "GER-D":
            # A draft of the key would be refused as "already a draft" first;
            # put it on another key so the supersedes check is what refuses.
            site.docs.append(_ger("GER-D", "USD", "GBP", "Closing", 0.79, 0))
        err = _save_raises(site, from_currency="EUR", quote=0.86, change_reason=F11_REASON,
                           supersedes=supersedes)
        assert str(err) == sentence, (supersedes, err)
        assert site.new_docs == [], supersedes


def test_f11_failure_path_nothing_approved_to_correct():
    site = _analyst_site()
    site.docs = [_ger("GER-C", "EUR", "GBP", "Closing", 0.84, 2)]
    err = _save_raises(site, from_currency="EUR", quote=0.86, change_reason=F11_REASON,
                       supersedes="GER-C")
    assert str(err) == ("GER-C is not the approved Closing rate EUR → GBP for FY2025 P07: "
                        "there is no approved rate to correct."), err
    assert site.new_docs == []


def test_f11_a_blank_supersedes_is_no_correction():
    site = _analyst_site()
    result = _save(site, supersedes="  ")
    dicts = [c for c in site.get_doc_calls if isinstance(c, dict)]
    assert set(dicts[0]) == NEW_KEYS, dicts[0]
    assert result["name"] == "GER-NEW"


def test_f11_editing_a_correction_keeps_what_it_corrects():
    site = _analyst_site()
    doc = _named(site, name="GER-3", rate_type="Closing", supersedes="GER-1",
                 source="Manual", erp_quote=None)
    for supersedes in (None, "GER-1"):
        _save(site, name="GER-3", from_currency="EUR", quote=0.861, change_reason=F11_REASON,
              supersedes=supersedes)
        assert doc.supersedes == "GER-1"
        assert doc.quote == 0.861
    assert [c[0] for c in doc.calls] == ["save", "save"]


def test_f11_failure_path_an_edit_cannot_change_what_a_draft_corrects():
    site = _analyst_site()
    doc = _named(site, name="GER-3", rate_type="Closing", supersedes="GER-1")
    err = _save_raises(site, name="GER-3", from_currency="EUR", quote=0.86,
                       change_reason=F11_REASON, supersedes="GER-9")
    assert str(err) == "GER-3 corrects GER-1, not GER-9: a draft keeps the rate it corrects.", err
    plain = _named(site, name="GER-2")
    err = _save_raises(site, name="GER-2", from_currency="EUR", rate_type="Average",
                       supersedes="GER-9")
    assert str(err) == ("GER-2 corrects no approved rate, not GER-9: a draft keeps the rate it "
                        "corrects."), err
    assert doc.calls == [] and plain.calls == []


def test_f11_save_roles_are_the_save_rate_literal():
    import ast

    tree = ast.parse(open(API_PY, encoding="utf-8").read())
    [fn] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "save_rate"]
    first = fn.body[0].value
    assert first.func.attr == "only_for"
    literal = ast.literal_eval(first.args[0])
    api = _load_rates_api(_Site())
    assert api.RATE_SAVE_ROLES == literal == ("EPM Analyst", "EPM Admin", "System Manager")


# get_rates: ``correct`` on every cell, and the pending correction.

def _f11_grid_site():
    """The golden's site: the Close Lead under Blocked reads FY2025 P07 with
    EUR → GBP Closing GER-1 approved (0.85) and the Analyst's correction GER-3
    (0.86, supersedes GER-1) pending; EUR → GBP Average GER-4 approved with
    nothing pending; USD → GBP Closing GER-5 a first draft; USD → GBP Average
    missing."""
    site = _Site()
    site.user, site.roles = LEAD, {"EPM Admin"}
    site.policy = "Blocked"
    site.docs = [
        _ger("GER-1", "EUR", "GBP", "Closing", 0.85, 1, owner=LEAD,
             modified=datetime(2025, 7, 31, 9, 0, 0)),
        _ger("GER-3", "EUR", "GBP", "Closing", 0.86, 0, owner=ANALYST,
             modified=datetime(2025, 8, 2, 9, 0, 0), change_reason=F11_REASON,
             supersedes="GER-1"),
        _ger("GER-4", "EUR", "GBP", "Average", 0.84, 1, owner=LEAD),
        _ger("GER-5", "USD", "GBP", "Closing", 0.79, 0, owner=ANALYST),
    ]
    return site


def test_f11_get_rates_matches_the_golden_payload():
    site = _f11_grid_site()
    result = _call(site)
    assert result == _golden(F11_GOLDEN), json.dumps(result, indent=1, ensure_ascii=False)


def test_f11_the_pending_correction_carries_before_after_and_its_approve_mode():
    result = _call(_f11_grid_site())
    cell = _row(result, "EUR")["closing"]
    assert (cell["name"], cell["status"], cell["extra_drafts"]) == ("GER-1", "approved", [])
    assert cell["correct"] is False  # one is already pending
    assert cell["correction"] == {
        "name": "GER-3", "supersedes": "GER-1", "owner": ANALYST, "change_reason": F11_REASON,
        "before": {"quote": 0.85, "quoted_per": "1", "rate": 0.85},
        "after": {"quote": 0.86, "quoted_per": "1", "rate": 0.86},
        "approve": {"mode": "direct", "message": None}, "edited_by": [],
        "edit": True, "delete": True}


def test_f11_failure_path_the_lead_cannot_approve_their_own_correction_under_blocked():
    site = _f11_grid_site()
    site.docs[1]["owner"] = LEAD
    cell = _row(_call(site), "EUR")["closing"]
    assert cell["correction"]["approve"]["mode"] == "refused"


def test_f11_correct_is_true_only_on_an_approved_cell_with_nothing_pending():
    result = _call(_f11_grid_site())
    eur, usd = _row(result, "EUR"), _row(result, "USD")
    assert eur["average"]["correct"] is True
    assert eur["closing"]["correct"] is False
    assert usd["closing"]["correct"] is False  # a first draft: edit it
    assert usd["average"]["correct"] is False  # missing: enter it
    for cell in (eur["average"], usd["closing"], usd["average"]):
        assert cell["correction"] is None


def test_f11_the_analyst_may_correct_too():
    site = _f11_grid_site()
    site.user, site.roles = ANALYST, {"EPM Analyst"}
    assert _row(_call(site), "EUR")["average"]["correct"] is True


def test_f11_failure_path_a_viewer_never_gets_correct():
    site = _f11_grid_site()
    site.user, site.roles = VIEWER, {"EPM User"}
    result = _call(site)
    eur = _row(result, "EUR")
    assert eur["average"]["correct"] is False
    assert eur["closing"]["correction"]["approve"]["mode"] == "not_approver"


def test_f11_failure_path_a_closed_period_never_gets_correct():
    site = _f11_grid_site()
    site.periods = [_period(2025, 6, "Closed"), _period(2025, 7, "Closed")]
    eur = _row(_call(site), "EUR")
    assert eur["average"]["correct"] is False


def test_f11_the_correction_costs_no_extra_read():
    plain, corrected = _f11_grid_site(), _f11_grid_site()
    plain.docs = [d for d in plain.docs if d["name"] != "GER-3"]
    _call(plain), _call(corrected)
    assert len(plain.reads) == len(corrected.reads) == 8, corrected.reads


# --- FR1 (konsol#305 review-d2fu S1; #312-3, #312-3b, Deepak Pai): a draft
# correction is never a dead end. ``delete_rate(name)`` deletes one draft Group
# Exchange Rate that carries ``supersedes``: a pending correction, or an
# orphan that can never be approved. Every refusal is thrown before any write.

FR1_ROLES = ("EPM Analyst", "EPM Admin", "System Manager")


def _fr1_site(user=ANALYST, roles=("EPM Analyst",)):
    """``_f11_grid_site()`` as the Analyst, with each of its rates (and a
    cancelled correction GER-C) readable by ``get_doc`` under its own name."""
    site = _f11_grid_site()
    site.user, site.roles = user, set(roles)
    site.docs.append(_ger("GER-C", "EUR", "GBP", "Closing", 0.84, 2, supersedes="GER-1",
                          change_reason=F11_REASON))
    for row in site.docs:
        site.named[row["name"]] = _FakeDoc(dict(row, doctype="Group Exchange Rate"))
    return site


def _delete(site, name):
    return _invoke(site, lambda api: api.delete_rate(name))


def _delete_raises(site, name):
    with pytest.raises(Exception) as info:
        _delete(site, name)
    return info.value


def test_fr1_the_analyst_deletes_a_pending_correction():
    site = _fr1_site()
    result = _delete(site, "GER-3")
    assert result == {"name": "GER-3", "deleted": True}, result
    assert site.deleted == [(("Group Exchange Rate", "GER-3"), {})], site.deleted
    assert site.only_for_calls == [FR1_ROLES]
    assert site.reads == [("exists", "Group Exchange Rate"), ("get_doc", "Group Exchange Rate"),
                          ("sql", "fiscal_period_rows")], site.reads
    assert site.get_doc_kwargs == [{"for_update": True}], site.get_doc_kwargs
    assert site.new_docs == [] and site.named["GER-3"].calls == []


def test_fr1_the_close_lead_deletes_a_correction_too_and_the_name_is_stripped():
    site = _fr1_site(user=LEAD, roles=("EPM Admin",))
    assert _delete(site, "  GER-3 ") == {"name": "GER-3", "deleted": True}
    assert site.deleted == [(("Group Exchange Rate", "GER-3"), {})], site.deleted


def test_fr1_failure_paths_each_refused_with_its_sentence_and_nothing_deleted():
    cases = [
        ("GER-1", "GER-1 is approved, not a draft: correct it with a Reason for Change instead."),
        ("GER-C", "GER-C is cancelled, not a draft: a cancelled rate is kept as the audit trail."),
        ("GER-5", "GER-5 corrects no approved rate: edit it instead."),
        ("GER-NOPE", "GER-NOPE is not a group exchange rate: reload the Rates screen."),
        (["GER-3"], "['GER-3'] is not a group exchange rate: reload the Rates screen."),
        ({"name": "GER-3"}, "{'name': 'GER-3'} is not a group exchange rate: reload the Rates "
                            "screen."),
        ("", " is not a group exchange rate: reload the Rates screen."),
    ]
    for name, sentence in cases:
        site = _fr1_site()
        err = _delete_raises(site, name)
        assert type(err).__name__ == "ValidationError", (name, err)
        assert str(err) == sentence, (name, str(err))
        assert site.deleted == [], name
        assert site.new_docs == [], name


def test_fr1_failure_path_a_correction_in_a_closed_period_is_refused():
    site = _fr1_site()
    site.periods = [_period(2025, 6, "Closed"), _period(2025, 7, "Closed")]
    err = _delete_raises(site, "GER-3")
    assert str(err) == ("FY2025 P07 is Closed: group rates lock when their period closes "
                        "(reopen it first)."), err
    assert site.deleted == []


def test_fr1_failure_path_the_period_read_is_the_drafts_own():
    # P07 is Open; the draft sits in P06, which is Closed: the doc's own period decides.
    site = _fr1_site()
    site.named["GER-3"].fiscal_period = 6
    err = _delete_raises(site, "GER-3")
    assert str(err) == ("FY2025 P06 is Closed: group rates lock when their period closes "
                        "(reopen it first)."), err
    assert site.deleted == []


def test_fr1_failure_path_a_viewer_and_an_entity_accountant_read_nothing():
    for role in ("EPM User", "Entity Accountant"):
        site = _fr1_site(user=VIEWER, roles=(role,))
        err = _delete_raises(site, "GER-3")
        assert type(err).__name__ == "PermissionError", (role, err)
        assert site.reads == [] and site.get_doc_calls == [], (role, site.reads)
        assert site.deleted == [], role


def test_fr1_forge_extra_keys_never_reach_it():
    # Frappe's whitelisted-args path drops keys a function does not name; a
    # direct call with them raises TypeError before any read.
    import inspect

    site = _fr1_site()
    with pytest.raises(TypeError):
        _invoke(site, lambda api: api.delete_rate(name="GER-1", docstatus=0,
                                                  ignore_permissions=True))
    assert site.reads == [] and site.deleted == [] and site.only_for_calls == []
    params = _invoke(site, lambda api: [(p.name, p.kind) for p in
                                        inspect.signature(api.delete_rate).parameters.values()])
    assert params == [("name", inspect.Parameter.POSITIONAL_OR_KEYWORD)], params


def test_fr1_delete_roles_are_the_save_roles_literal():
    import ast

    tree = ast.parse(open(API_PY, encoding="utf-8").read())
    [fn] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "delete_rate"]
    body = fn.body[1:] if ast.get_docstring(fn) is not None else fn.body
    first = body[0].value  # the first statement after the docstring
    assert first.func.attr == "only_for"
    assert isinstance(first.args[0], ast.Tuple)  # a literal tuple, not a name
    literal = ast.literal_eval(first.args[0])
    api = _load_rates_api(_Site())
    assert literal == api.RATE_SAVE_ROLES == FR1_ROLES
    [deco] = fn.decorator_list
    assert ast.unparse(deco) == "frappe.whitelist(methods=['POST'])", ast.unparse(deco)


# --- FR2 (konsol#305 review-d2fu S1; #312-3, #312-3b, Deepak Pai): the
# server decides whether a correction can be edited or deleted, exactly when
# ``save_rate`` / ``delete_rate`` would accept it, and lists each cell's orphan
# corrections (drafts that correct a rate no longer approved) with ``delete``.

FR2_ORPHAN = ("GER-7", "GER-0")  # EUR -> GBP Closing, corrects a rate no longer approved


def _fr2_site(user=LEAD, roles=("EPM Admin",), closed=False, orphan=True):
    """``_f11_grid_site()`` for ``user``, plus (``orphan``) the Analyst's draft
    GER-7 correcting GER-0 on EUR -> GBP Closing, with every rate readable by
    ``get_doc`` under its own name. ``closed``: FY2025 P07 is Closed."""
    site = _f11_grid_site()
    site.user, site.roles = user, set(roles)
    if orphan:
        site.docs.append(_ger(FR2_ORPHAN[0], "EUR", "GBP", "Closing", 0.87, 0, owner=ANALYST,
                              supersedes=FR2_ORPHAN[1], change_reason="stale"))
    if closed:
        site.periods = [_period(2025, 6, "Closed"), _period(2025, 7, "Closed")]
    for row in site.docs:
        site.named[row["name"]] = _FakeDoc(dict(row, doctype="Group Exchange Rate"))
    return site


FR2_CALLERS = [
    # (label, kwargs of _fr2_site, edit/delete expected)
    ("analyst", {"user": ANALYST, "roles": ("EPM Analyst",)}, True),
    ("close lead", {"user": LEAD, "roles": ("EPM Admin",)}, True),
    ("system manager", {"user": LEAD, "roles": ("System Manager",)}, True),
    ("viewer", {"user": VIEWER, "roles": ("EPM User",)}, False),
    ("closed period", {"user": ANALYST, "roles": ("EPM Analyst",), "closed": True}, False),
]


def _fr2_cells(result):
    for row in result["rows"] + result["unrequired"]:
        for rate_type, key in (("Closing", "closing"), ("Average", "average")):
            yield row, rate_type, row[key]


def test_fr2_the_correction_carries_edit_and_delete_from_the_callers_roles_and_period():
    for label, kwargs, expected in FR2_CALLERS:
        cell = _row(_call(_fr2_site(**kwargs)), "EUR")["closing"]
        assert cell["correction"]["name"] == "GER-3", label
        assert cell["correction"]["edit"] is expected, label
        assert cell["correction"]["delete"] is expected, label


def test_fr2_every_cell_carries_orphans_and_the_orphan_carries_delete():
    for label, kwargs, expected in FR2_CALLERS:
        result = _call(_fr2_site(**kwargs))
        cell = _row(result, "EUR")["closing"]
        assert cell["orphans"] == [{"name": "GER-7", "supersedes": "GER-0",
                                    "delete": expected}], (label, cell["orphans"])
        assert cell["extra_drafts"] == ["GER-7"], label  # still counted there
        others = [c["orphans"] for _, _, c in _fr2_cells(result) if c is not cell]
        assert others == [[], [], []], (label, others)


def test_fr2_failure_path_no_orphan_gives_an_empty_list_and_unchanged_extra_drafts():
    result = _call(_fr2_site(orphan=False))
    assert [c["orphans"] for _, _, c in _fr2_cells(result)] == [[], [], [], []]
    assert _row(result, "EUR")["closing"]["extra_drafts"] == []


def test_fr2_failure_path_cells_without_a_correction_still_carry_none():
    result = _call(_fr2_site())
    eur, usd = _row(result, "EUR"), _row(result, "USD")
    for cell in (eur["average"], usd["closing"], usd["average"]):
        assert cell["correction"] is None


def test_fr2_delete_flags_are_pinned_to_delete_rate():
    # For every correction and orphan the payload offers to delete,
    # delete_rate on the same site accepts it; for every one it does not
    # offer, delete_rate refuses it and deletes nothing.
    checked = {True: 0, False: 0}
    for label, kwargs, _ in FR2_CALLERS:
        result = _call(_fr2_site(**kwargs))
        items = []
        for _, _, cell in _fr2_cells(result):
            if cell["correction"] is not None:
                items.append(cell["correction"])
            items.extend(cell["orphans"])
        assert sorted(i["name"] for i in items) == ["GER-3", "GER-7"], label
        for item in items:
            site = _fr2_site(**kwargs)
            if item["delete"]:
                assert _delete(site, item["name"]) == {"name": item["name"], "deleted": True}, \
                    (label, item)
                assert site.deleted == [(("Group Exchange Rate", item["name"]), {})], label
            else:
                err = _delete_raises(site, item["name"])
                assert type(err).__name__ in ("PermissionError", "ValidationError"), (label, err)
                assert site.deleted == [], (label, item)
            checked[item["delete"]] += 1
    assert checked[True] and checked[False], checked  # both directions ran


def test_fr2_edit_flags_are_pinned_to_save_rate():
    # For every correction the payload offers to edit, save_rate's edit path
    # (name = the correction, supersedes = the approved cell) accepts it; for
    # every one it does not offer, save_rate refuses it and saves nothing.
    checked = {True: 0, False: 0}
    for label, kwargs, _ in FR2_CALLERS:
        result = _call(_fr2_site(**kwargs))
        for row, rate_type, cell in _fr2_cells(result):
            correction = cell["correction"]
            if correction is None:
                continue
            site = _fr2_site(**kwargs)
            args = {"from_currency": row["from_currency"], "to_currency": row["to_currency"],
                    "rate_type": rate_type, "quote": 0.861,
                    "quoted_per": correction["after"]["quoted_per"],
                    "change_reason": correction["change_reason"],
                    "name": correction["name"], "supersedes": cell["name"]}
            doc = site.named[correction["name"]]
            if correction["edit"]:
                saved = _save(site, **args)
                assert saved["name"] == correction["name"], (label, saved)
                assert [c[0] for c in doc.calls] == ["save"], label
                assert doc.supersedes == cell["name"] and doc.quote == 0.861, label
            else:
                err = _save_raises(site, **args)
                assert type(err).__name__ in ("PermissionError", "ValidationError"), (label, err)
                assert doc.calls == [] and site.new_docs == [], label
            checked[correction["edit"]] += 1
    assert checked[True] and checked[False], checked  # both directions ran


def test_fr2_the_orphans_cost_no_extra_read():
    plain, orphaned = _fr2_site(orphan=False), _fr2_site()
    _call(plain), _call(orphaned)
    assert plain.reads == orphaned.reads and len(orphaned.reads) == 8, orphaned.reads


def test_fr2_the_draft_flag_is_named_for_what_it_decides():
    # One name for the save role in an Open period, now driving correct, edit
    # and delete; the old name is gone.
    source = open(API_PY, encoding="utf-8").read()
    assert "may_correct" not in source
    assert "may_draft = " in source


# --- FR3 (review-d2fu N8): save_rate's new-draft path reads the key's draft
# and approved rows with one locking read (SELECT ... FOR UPDATE on the grain
# index), not get_all, so two users correcting one approved rate cannot both
# draft a correction: the second waits on the first's row lock, then reads
# its draft and is refused. Everything after the read is unchanged. ---------


def test_fr3_a_new_draft_reads_the_key_once_with_a_locking_read_on_the_grain():
    site = _analyst_site()
    _save(site, from_currency="USD", to_currency="GBP", rate_type="Closing")
    sqls = [q for kind, q in [r[:2] for r in site.reads] if kind == "sql"
            and "Group Exchange Rate" in q]
    assert sqls == [FR3_LOCKING_READ], site.reads
    query = sqls[0]
    assert query.endswith(" FOR UPDATE"), query
    for field in ("to_currency", "from_currency", "fiscal_year", "fiscal_period", "rate_type"):
        assert ("%s = %%s" % field) in query, (field, query)
    assert "docstatus IN (0, 1)" in query, query
    assert site.locking_values == [("GBP", "USD", 2025, 7, "Closing")], site.locking_values
    assert ("get_all", "Group Exchange Rate") not in site.reads, site.reads


def test_fr3_a_correction_reads_the_key_with_the_locking_read_too():
    site = _f11_correction_site()
    result = _save(site, from_currency="EUR", quote=0.86, change_reason=F11_REASON,
                   supersedes="GER-1")
    assert result["name"] == "GER-NEW"
    assert site.reads == [("sql", "fiscal_period_rows"), ("sql", FR3_LOCKING_READ)], site.reads
    assert site.locking_values == [("GBP", "EUR", 2025, 7, "Closing")], site.locking_values


def test_fr3_failure_path_the_second_correction_is_refused_on_what_the_locking_read_returns():
    # The first correction GER-3 is what the second user's locking read sees
    # once the first commits; the refusal is the existing sentence, nothing is
    # inserted, and no get_all of Group Exchange Rate is made.
    site = _f11_correction_site()
    site.docs.append(_ger("GER-3", "EUR", "GBP", "Closing", 0.86, 0, supersedes="GER-1",
                          change_reason=F11_REASON))
    err = _save_raises(site, from_currency="EUR", quote=0.87, change_reason="again",
                       supersedes="GER-1")
    assert str(err) == "GER-3 already corrects GER-1 and awaits approval: edit it.", err
    assert site.reads == [("sql", "fiscal_period_rows"), ("sql", FR3_LOCKING_READ)], site.reads
    assert site.new_docs == []


def test_fr3_failure_path_an_approved_key_is_refused_on_the_locking_read():
    site = _analyst_site()
    site.docs = [_ger("GER-1", "EUR", "GBP", "Closing", 0.85, 1)]
    err = _save_raises(site, from_currency="EUR")
    assert str(err) == ("GER-1 is already the approved Closing rate EUR → GBP for FY2025 P07: "
                        "correct it with a Reason for Change instead."), err
    assert site.reads == [("sql", "fiscal_period_rows"), ("sql", FR3_LOCKING_READ)], site.reads
    assert site.new_docs == []


def test_fr3_an_edit_by_name_makes_no_locking_read_of_the_key():
    # The edit path reads the named draft (get_doc) and nothing else: the row
    # does not widen to it.
    site = _analyst_site()
    _named(site)
    _save(site, name="GER-2", from_currency="EUR", rate_type="Average", quote=0.9)
    assert site.locking_values == [], site.locking_values
    assert site.reads == [("sql", "fiscal_period_rows"), ("get_doc", "Group Exchange Rate")]
