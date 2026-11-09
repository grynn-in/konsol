"""Behaviour of the Konsol home's frappe-bound code against a stub frappe (konsol#407, review of #409).

- ``desk_blocks.install_custom_blocks`` (after_install / after_migrate):
  creates a missing block, leaves a current one alone, overwrites one whose
  HTML, script, style, privacy or roles differ.
- ``overview_api._count``: None when the caller may not read the doctype
  (the count is never read, and never shown as 0); otherwise the
  ``frappe.get_list`` length.
- ``overview_api._shown``: a ValueError from a reader becomes a gray
  "Cannot read" card; a permission or validation error still propagates.
- ``overview_api.tb_exceptions``: cut to ``allowed_entity_codes``.
"""
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class _Doc(dict):
    __getattr__ = dict.get

    def update(self, values):
        dict.update(self, values)

    def set(self, key, value):
        self[key] = [_Doc(v) for v in value]

    def get(self, key, default=None):
        return dict.get(self, key, default)


def _frappe(**attrs):
    f = _module("frappe")
    f.ValidationError = type("ValidationError", (Exception,), {})
    f.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or f.ValidationError)(msg)

    def whitelist(*a, **k):
        return lambda fn: fn

    f.throw = throw
    f.whitelist = whitelist
    f.only_for = lambda roles: None
    f.parse_json = lambda v: __import__("json").loads(v) if isinstance(v, str) else v
    f.__dict__.update(attrs)
    return f


def _load(name, rel, modules):
    saved = {k: sys.modules.get(k) for k in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, rel))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


# --- desk_blocks -------------------------------------------------------------

class _BlockSite:
    def __init__(self, stored=None):
        self.stored = stored  # name -> _Doc
        self.saved = []
        self.inserted = []


def _blocks(site):
    def exists(dt, name):
        return name in (site.stored or {})

    def get_doc(dt, name):
        return site.stored[name]

    def new_doc(dt):
        doc = _Doc(roles=[])
        doc.insert = lambda set_name=None: site.inserted.append(set_name)
        return doc

    frappe = _frappe(db=types.SimpleNamespace(exists=exists), get_doc=get_doc, new_doc=new_doc)
    return _load("desk_blocks_under_test", "desk_blocks.py", {"frappe": frappe})


def _current_doc(mod, entry, **change):
    name, folder, roles = entry
    doc = _Doc(html=mod._read(folder, "html"), script=mod._read(folder, "js"),
               style=mod._read(folder, "css"), private=0,
               roles=[_Doc(role=r) for r in roles])
    doc.update(change)
    return doc


def _all_current(mod):
    return {entry[0]: _current_doc(mod, entry) for entry in mod.BLOCKS}


def test_the_app_ships_the_checklist_and_the_handbook():
    mod = _blocks(_BlockSite())
    assert [b[0] for b in mod.BLOCKS] == ["Konsol Close Checklist", "Konsol Handbook",
                                          "Konsol My Work Board", "Konsol Home Tabs"]


def test_home_tabs_block_has_the_checklist_roles():
    mod = _blocks(_BlockSite())
    blocks = {b[0]: b for b in mod.BLOCKS}
    assert blocks["Konsol Home Tabs"][1:] == ("konsol_home_tabs",
                                              blocks["Konsol Close Checklist"][2])


def test_install_creates_a_missing_block():
    site = _BlockSite({})
    mod = _blocks(site)
    names = [b[0] for b in mod.BLOCKS]
    assert mod.install_custom_blocks() == ["%s: created" % n for n in names]
    assert site.inserted == names


def test_install_leaves_a_current_block_alone():
    mod = _blocks(_BlockSite())
    stored = _all_current(mod)
    for doc in stored.values():
        doc.save = lambda: pytest.fail("a current block was saved")
    mod = _blocks(_BlockSite(stored))
    assert mod.install_custom_blocks() == ["%s: current" % b[0] for b in mod.BLOCKS]


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("change", [{"html": "<p>site edit</p>"}, {"script": ""},
                                    {"style": None}, {"private": 1},
                                    {"roles": [_Doc(role="EPM User")]}])
def test_install_overwrites_a_block_that_differs(index, change):
    mod = _blocks(_BlockSite())
    stored = _all_current(mod)
    entry = mod.BLOCKS[index]
    doc = _current_doc(mod, entry, **change)
    stored[entry[0]] = doc
    saves = []
    doc.save = lambda: saves.append(1)
    for other in stored.values():
        if other is not doc:
            other.save = lambda: pytest.fail("a current block was saved")
    mod = _blocks(_BlockSite(stored))
    lines = mod.install_custom_blocks()
    assert lines[index] == "%s: updated" % entry[0]
    assert [l for i, l in enumerate(lines) if i != index] == [
        "%s: current" % b[0] for i, b in enumerate(mod.BLOCKS) if i != index]
    assert saves == [1]
    assert doc.html == mod._read(entry[1], "html") and doc.private == 0
    assert sorted(r.role for r in doc.roles) == sorted(entry[2])


# --- overview_api --------------------------------------------------------------

def _api(has_permission=True, listed=(), allowed=None, current=((2025, 7), None)):
    frappe = _frappe()
    frappe.has_permission = lambda dt, ptype="read", throw=False: (
        has_permission if not throw or has_permission
        else (_ for _ in ()).throw(frappe.PermissionError("no")))
    frappe.get_list = lambda dt, **k: list(listed)
    model = _load("overview_model_for_api_test", os.path.join("close", "overview_model.py"), {})
    close = _module("konsol.close", overview_model=model, approvals_api=_module("a"),
                    freshness_api=_module("f"), ic_api=_module("i"), period_api=_module("p"),
                    readiness_model=_module("r"), signoff_gate=_module("s"))
    modules = {
        "frappe": frappe,
        "konsol": _module("konsol", fiscal_calendar=_module("fc"), group_rates=_module("gr")),
        "konsol.close": close,
        "konsol.consolidation": _module("konsol.consolidation"),
        "konsol.consolidation.doctype": _module("konsol.consolidation.doctype"),
        "konsol.consolidation.doctype.assertion_run": _module("x"),
        "konsol.consolidation.doctype.assertion_run.assertion_run": _module(
            "y", latest_close_run=lambda *a: None),
        "konsol.entity_permissions": _module("e", allowed_entity_codes=lambda: allowed),
    }
    api = _load("overview_api_under_test", os.path.join("close", "overview_api.py"), modules)
    api._current = lambda: (current[0], current[1], {})
    return api, frappe


def test_count_is_none_when_the_doctype_cannot_be_read():
    api, _ = _api(has_permission=False, listed=["J1"])
    assert api._count("Consolidation Journal", {}) is None
    api, _ = _api(listed=["J1", "J2"])
    assert api._count("Consolidation Journal", {}) == 2


def test_shown_turns_a_value_error_into_a_gray_card_and_lets_permission_errors_through():
    api, frappe = _api()

    def broken():
        raise ValueError("unknown close run status 'Purple'")

    html = api._shown(broken)
    assert 'indicator-pill gray' in html and "Cannot read: unknown close run status" in html
    assert "&#x27;Purple&#x27;" in html  # escaped

    def refused():
        raise frappe.PermissionError("no")

    with pytest.raises(frappe.PermissionError):
        api._shown(refused)


def test_tb_exceptions_counts_only_the_callers_entities():
    api, _ = _api(listed=["A", "B", "C"], allowed={"A"})
    assert '<span class="indicator-pill orange">1</span>' in api.tb_exceptions("[]")
    api, _ = _api(listed=["A", "B", "C"], allowed=None)
    assert '<span class="indicator-pill orange">3</span>' in api.tb_exceptions("[]")


def test_band_methods_refuse_filters():
    api, frappe = _api()
    with pytest.raises(frappe.ValidationError):
        api.tb_exceptions('[["TB Exception", "docstatus", "=", 1]]')
