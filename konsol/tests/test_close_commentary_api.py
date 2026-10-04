"""Statement Commentary API: konsol/close/commentary_api.py ``save_commentary``
(konsol#305 M44; stories 8.3; R1-R6 roles; #305-W4-5 5b; W4-E14).

``save_commentary(fiscal_year, fiscal_period, consolidation_group, heading,
text, modified=None)`` (POST) creates or updates the one Statement Commentary
record for a key through the document's own ``insert()``/``save()`` — no
``ignore_permissions`` — with a stale edit refused first
(``commentary_model.stale_problem``, M42, the real module).

Loaded against a stub frappe (pattern: test_close_rates_api.py's ``_Site`` /
``_invoke``, copied, not imported). The Statement Commentary document itself
is a recording fake (M43's controller is not loaded here: M44 only has to
prove it drives the document's own insert/save with no override flag; M43's
own tests prove the controller's rules and event).
"""
import importlib.util
import inspect
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
API_PY = os.path.join(CLOSE_DIR, "commentary_api.py")
MODEL_PY = os.path.join(CLOSE_DIR, "commentary_model.py")
TIMEFMT_PY = os.path.join(CLOSE_DIR, "timefmt.py")

#: The stub site's system time zone (A55: BST, +01:00, in July).
SITE_TZ = "Europe/London"

LEAD = "zz-lead@example.com"
ANALYST = "zz-analyst@example.com"
VIEWER = "zz-viewer@example.com"

SAVE_PARAMS = ["fiscal_year", "fiscal_period", "consolidation_group", "heading", "text", "modified"]


class _FakeDoc:
    """A recording Statement Commentary: insert()/save() are counted (and
    their ``ignore_permissions`` captured), and bump ``modified``/
    ``modified_by`` the way a real save would — nothing else is decided
    here (M43's controller owns the real rules and event)."""

    def __init__(self, data, name=None):
        self.__dict__["_data"] = dict(data)
        if name is not None:
            self._data["name"] = name
        self.__dict__["calls"] = []

    def __getattr__(self, key):
        try:
            return self.__dict__["_data"][key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        self.__dict__["_data"][key] = value

    def insert(self, ignore_permissions=False, **k):
        self.calls.append(("insert", ignore_permissions, k))
        self._data.setdefault("name", "SC-ZZGRP-2025-7-4")
        self._data["modified"] = "2025-07-15 09:00:00.000000"
        self._data["modified_by"] = LEAD
        return self

    def save(self, ignore_permissions=False, **k):
        self.calls.append(("save", ignore_permissions, k))
        self._data["modified"] = "2025-07-20 11:30:00.000000"
        self._data["modified_by"] = LEAD
        return self


class _Site:
    def __init__(self, roles=("EPM Analyst",), user=ANALYST):
        self.user = user
        self.roles = set(roles)
        self.only_for_calls = []
        self.exists_calls = []
        self.get_doc_calls = []
        self.docs = {}  # name -> _FakeDoc (pre-existing records)
        self.new_docs = []  # every _FakeDoc created from a dict


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

    def exists(doctype, name):
        site.exists_calls.append((doctype, name))
        assert doctype == "Statement Commentary", doctype
        return name in site.docs

    def get_doc(arg, name=None, **k):
        if isinstance(arg, dict):
            doc = _FakeDoc(arg)
            site.new_docs.append(doc)
            return doc
        site.get_doc_calls.append((arg, name))
        assert arg == "Statement Commentary", arg
        return site.docs[name]

    frappe.throw = throw
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.exists = exists
    frappe.get_doc = get_doc
    frappe.db = types.SimpleNamespace(exists=exists)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.utils = types.SimpleNamespace(get_system_timezone=lambda: SITE_TZ)
    return frappe


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _invoke(site, run):
    frappe = _frappe(site)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    konsol.close = close
    names = ["frappe", "konsol", "konsol.close", "konsol.close.commentary_model",
             "konsol.close.timefmt", "close_commentary_api_under_test"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"frappe": frappe, "konsol": konsol, "konsol.close": close})
    try:
        close.commentary_model = _load_path("konsol.close.commentary_model", MODEL_PY)
        close.timefmt = _load_path("konsol.close.timefmt", TIMEFMT_PY)
        api = _load_path("close_commentary_api_under_test", API_PY)
        return run(api)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _save(site, fy=2025, fp=7, group="ZZGRP", heading="4", text="Volume down 4%.", modified=None):
    return _invoke(site, lambda api: api.save_commentary(fy, fp, group, heading, text, modified))


def _save_raises(site, **kw):
    with pytest.raises(Exception) as info:
        _save(site, **kw)
    return info.value


# ---- the first save (insert) ------------------------------------------------

def test_first_save_inserts_with_the_four_key_fields_and_the_text():
    site = _Site()
    result = _save(site)
    assert site.only_for_calls == [("EPM Admin", "EPM Analyst", "System Manager")]
    assert len(site.new_docs) == 1
    doc = site.new_docs[0]
    assert dict(doc._data, name=doc.name) == {
        "doctype": "Statement Commentary", "consolidation_group": "ZZGRP",
        "fiscal_year": 2025, "fiscal_period": 7, "heading": "4",
        "text": "Volume down 4%.", "modified": "2025-07-15 09:00:00.000000",
        "modified_by": LEAD, "name": "SC-ZZGRP-2025-7-4",
    }
    assert doc.calls == [("insert", False, {})]
    assert result["name"] == "SC-ZZGRP-2025-7-4"
    assert result["heading"] == "4"
    assert result["text"] == "Volume down 4%."
    assert result["modified"] == "2025-07-15 09:00:00.000000"
    assert result["by"] == LEAD
    assert result["at"] == "2025-07-15T09:00:00+01:00"


def test_first_save_strips_the_text_and_a_blank_save_clears_it():
    site = _Site()
    _save(site, text="  trimmed  ")
    assert site.new_docs[0].text == "trimmed"
    site2 = _Site()
    result = _save(site2, text="   ")
    assert result["text"] == ""


def test_a_modified_token_on_a_first_save_is_stale_and_nothing_is_inserted():
    site = _Site()
    _save_raises(site, modified="2025-07-01 00:00:00.000000")
    assert site.new_docs == []


# ---- update (save) ----------------------------------------------------------

def _existing(name="SC-ZZGRP-2025-7-4", modified="2025-07-10 08:00:00.000000", by=LEAD):
    return _FakeDoc({
        "doctype": "Statement Commentary", "consolidation_group": "ZZGRP",
        "fiscal_year": 2025, "fiscal_period": 7, "heading": "4",
        "text": "Volume down 4%.", "modified": modified, "modified_by": by,
    }, name=name)


def test_update_with_the_matching_modified_saves():
    site = _Site()
    site.docs["SC-ZZGRP-2025-7-4"] = _existing()
    result = _save(site, text="Volume down 6%, see the IC note.",
                    modified="2025-07-10 08:00:00.000000")
    doc = site.docs["SC-ZZGRP-2025-7-4"]
    assert doc.calls == [("save", False, {})]
    assert doc.text == "Volume down 6%, see the IC note."
    assert result["modified"] == "2025-07-20 11:30:00.000000"
    assert result["by"] == LEAD


def test_a_different_modified_throws_naming_the_editor_and_saves_nothing():
    site = _Site()
    site.docs["SC-ZZGRP-2025-7-4"] = _existing(by="other.lead@example.com")
    e = _save_raises(site, modified="2025-01-01 00:00:00.000000")
    assert "other.lead@example.com" in str(e)
    assert "2025-07-10 08:00:00.000000" in str(e)
    assert site.docs["SC-ZZGRP-2025-7-4"].calls == []


def test_no_modified_sent_for_an_existing_document_is_stale_too():
    site = _Site()
    site.docs["SC-ZZGRP-2025-7-4"] = _existing()
    _save_raises(site, modified=None)
    assert site.docs["SC-ZZGRP-2025-7-4"].calls == []


def test_a_modified_for_a_missing_document_throws():
    site = _Site()
    _save_raises(site, modified="2025-07-10 08:00:00.000000")
    assert site.new_docs == []


# ---- forge: the exact signature, no **kwargs --------------------------------

def test_signature_has_exactly_the_six_named_parameters_and_no_var_keyword():
    sig = _invoke(_Site(), lambda api: inspect.signature(api.save_commentary))
    params = list(sig.parameters.values())
    names = [p.name for p in params]
    assert names == SAVE_PARAMS, names
    kinds = {p.kind for p in params}
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.VAR_POSITIONAL not in kinds
    assert sig.parameters["modified"].default is None


# ---- roles -------------------------------------------------------------------

def test_epm_user_is_refused():
    site = _Site(roles={"EPM User"}, user=VIEWER)
    with pytest.raises(Exception):
        _save(site)
    assert site.new_docs == []


def test_entity_accountant_is_refused():
    site = _Site(roles={"Entity Accountant"}, user="zz-entity@example.com")
    with pytest.raises(Exception):
        _save(site)
    assert site.new_docs == []


def test_close_lead_and_system_manager_are_allowed():
    for role in ("EPM Admin", "System Manager"):
        site = _Site(roles={role})
        _save(site)  # does not raise
