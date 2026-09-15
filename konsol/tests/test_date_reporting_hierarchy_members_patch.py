"""The date_reporting_hierarchy_members migration patch (konsol#220), host-run.

Reporting Hierarchy Member now requires effective_from. A member saved
before that has none; the patch gives it its hierarchy's effective_from, or
1900-01-01 when the hierarchy has none (an undated member meant "always").
effective_to stays blank. A second run changes nothing.

A stub frappe records every call in order over in-memory tables.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "date_reporting_hierarchy_members.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
MODULE = "konsol.patches.date_reporting_hierarchy_members"


class _Site:
    def __init__(self, hierarchies, members):
        self.calls = []
        # name -> header dict
        self.hierarchies = {h["name"]: dict(h) for h in hierarchies}
        # name -> member dict
        self.members = {m["name"]: dict(m) for m in members}

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")

        def reload_doc(module, dt, name, *a, **k):
            site.calls.append(("reload_doc", module, dt, name))

        def get_all(doctype, filters=None, fields=None, **k):
            site.calls.append(("get_all", doctype))
            if doctype == "Reporting Hierarchy":
                table = site.hierarchies
            elif doctype == "Reporting Hierarchy Member":
                table = site.members
            else:
                raise AssertionError("unexpected get_all(%s)" % doctype)
            if filters:
                raise AssertionError("the stub reads whole tables; filter in the patch")
            return [{f: row.get(f) for f in (fields or ["name"])} for row in table.values()]

        def set_value(doctype, name, field, value=None, *a, **k):
            site.calls.append(("db.set_value", doctype, name, field, value))
            assert doctype == "Reporting Hierarchy Member", doctype
            site.members[name][field] = value

        frappe.reload_doc = reload_doc
        frappe.get_all = get_all
        frappe.db = types.SimpleNamespace(set_value=set_value)
        return frappe


def _run(site):
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = site.module()
    try:
        spec = importlib.util.spec_from_file_location("_date_rh_members_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        return patch.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _site():
    return _Site(
        hierarchies=[
            {"name": "ZZ Dated", "effective_from": "2017-01-01", "effective_to": None},
            {"name": "ZZ Undated", "effective_from": None, "effective_to": None},
        ],
        members=[
            {"name": "RHM-1", "reporting_hierarchy": "ZZ Dated", "member_code": "ZZ_A",
             "effective_from": None, "effective_to": None},
            {"name": "RHM-2", "reporting_hierarchy": "ZZ Undated", "member_code": "ZZ_B",
             "effective_from": None, "effective_to": None},
            {"name": "RHM-3", "reporting_hierarchy": "ZZ Dated", "member_code": "ZZ_C",
             "effective_from": "2020-04-01", "effective_to": "2024-12-31"},
        ],
    )


def _writes(site):
    return [c for c in site.calls if c[0] == "db.set_value"]


def test_reload_before_any_query():
    site = _site()
    _run(site)
    first_query = next(i for i, c in enumerate(site.calls) if c[0] != "reload_doc")
    reloaded = [c[3] for c in site.calls[:first_query]]
    assert "reporting_hierarchy_member" in reloaded, site.calls


def test_undated_member_under_dated_header_takes_header_date():
    site = _site()
    _run(site)
    assert site.members["RHM-1"]["effective_from"] == "2017-01-01"
    assert site.members["RHM-1"]["effective_to"] is None


def test_undated_member_under_undated_header_takes_1900():
    site = _site()
    _run(site)
    assert site.members["RHM-2"]["effective_from"] == "1900-01-01"
    assert site.members["RHM-2"]["effective_to"] is None


def test_dated_member_unchanged():
    site = _site()
    _run(site)
    assert site.members["RHM-3"]["effective_from"] == "2020-04-01"
    assert site.members["RHM-3"]["effective_to"] == "2024-12-31"
    assert not [w for w in _writes(site) if w[2] == "RHM-3"], _writes(site)
    assert sorted(w[2] for w in _writes(site)) == ["RHM-1", "RHM-2"], _writes(site)
    assert all(w[3] == "effective_from" for w in _writes(site)), _writes(site)


def test_second_run_changes_nothing():
    site = _site()
    _run(site)
    site.calls.clear()
    _run(site)
    assert not _writes(site), _writes(site)


def test_listed_once_in_patches_txt():
    with open(PATCHES_TXT) as f:
        lines = [l.strip() for l in f if l.strip()]
    assert lines.count(MODULE) == 1, lines.count(MODULE)
