"""get_reporting_hierarchy_tree answers "as of a date" (konsol#220), host-run.

api.py is loaded by file path against a stub frappe (the pattern of
``_failing_api`` in test_hierarchy_query.py). The stub site holds one
Reporting Hierarchy whose members are dated tranches of their codes:

    ZZ_TOP  root, always
    ZZ_E    group under ZZ_TOP, 2017-01-01 .. 2024-12-31
    ZZ_B    group under ZZ_TOP, 2017-01-01 .. open
    ZZ_EX   leaf under ZZ_E 2017-01-01 .. 2024-12-31, then under ZZ_B from 2025
    ZZ_A    group under ZZ_TOP, "ZZ Old A" to 2024-12-31, "ZZ New A" from 2025
    ZZ_AX   leaf linked to the OLD ZZ_A row, 2017-01-01 .. open
    ZZ_C    leaf under ZZ_B, 2017-01-01 .. 2020-12-31 (ended)

get_all returns only the fields asked for, as real frappe does.
"""
import datetime
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HIERARCHY = "ZZ Mgmt"
TODAY = "2025-06-30"


class Refused(Exception):
    pass


class _Dict(dict):
    """frappe._dict: attribute access, None for a missing key."""

    def __getattr__(self, key):
        return self.get(key)


def _d(iso):
    return datetime.date.fromisoformat(iso) if iso else None


MEMBERS = [
    # name, code, label, parent row, is_group, from, to
    ("RHM-TOP", "ZZ_TOP", "ZZ Top", None, 1, "1900-01-01", None),
    ("RHM-E", "ZZ_E", "ZZ East", "RHM-TOP", 1, "2017-01-01", "2024-12-31"),
    ("RHM-B", "ZZ_B", "ZZ Bravo", "RHM-TOP", 1, "2017-01-01", None),
    ("RHM-EX1", "ZZ_EX", "ZZ Export", "RHM-E", 0, "2017-01-01", "2024-12-31"),
    ("RHM-EX2", "ZZ_EX", "ZZ Export", "RHM-B", 0, "2025-01-01", None),
    ("RHM-A1", "ZZ_A", "ZZ Old A", "RHM-TOP", 1, "2017-01-01", "2024-12-31"),
    ("RHM-A2", "ZZ_A", "ZZ New A", "RHM-TOP", 1, "2025-01-01", None),
    ("RHM-AX", "ZZ_AX", "ZZ A Leaf", "RHM-A1", 0, "2017-01-01", None),
    ("RHM-C", "ZZ_C", "ZZ Closed", "RHM-B", 0, "2017-01-01", "2020-12-31"),
]


def _site_rows():
    rows = []
    for name, code, label, parent, is_group, frm, to in MEMBERS:
        rows.append({
            "name": name, "reporting_hierarchy": HIERARCHY,
            "member_code": code, "member_label": label,
            "parent_member": parent, "is_group": is_group,
            "effective_from": _d(frm), "effective_to": _d(to),
        })
    return rows


def _load_api():
    import requests  # noqa: F401  (api.py imports it; the venv has it)

    site = _site_rows()

    def get_value(doctype, filters, fields, as_dict=False, **kw):
        assert doctype == "Reporting Hierarchy", doctype
        if filters.get("hierarchy_name") != HIERARCHY:
            return None
        return _Dict(name=HIERARCHY, dimension="dim_cost_center",
                     label="ZZ Management", status="Published")

    def get_all(doctype, filters=None, fields=None, order_by=None, **kw):
        assert doctype == "Reporting Hierarchy Member", doctype
        found = [r for r in site
                 if all(r.get(k) == v for k, v in (filters or {}).items())]
        found.sort(key=lambda r: (r["member_code"], str(r["effective_from"])))
        return [_Dict({f: r.get(f) for f in fields}) for r in found]

    def throw(msg, *a, **k):
        raise Refused(msg)

    fake_frappe = types.ModuleType("frappe")
    fake_frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    fake_frappe.throw = throw
    fake_frappe.get_all = get_all
    fake_frappe.db = types.SimpleNamespace(get_value=get_value)
    fake_utils = types.ModuleType("frappe.utils")
    fake_utils.now_datetime = lambda: None
    fake_utils.today = lambda: TODAY
    fake_frappe.utils = fake_utils
    stubs = {
        "frappe": fake_frappe,
        "frappe.utils": fake_utils,
        "konsol.clickhouse": types.SimpleNamespace(
            connection_url=lambda s: "http://zz-clickhouse:8123/",
            get_connection=lambda: {}),
    }
    before = set(sys.modules)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "_host_api_k220_tree", os.path.join(APP_DIR, "api.py"))
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
    finally:
        for key in set(sys.modules) - before:
            if key.split(".")[0] in ("frappe", "konsol"):
                del sys.modules[key]
        for k, mod in saved.items():
            if mod is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = mod
    return api


def _tree(as_of):
    return _load_api().get_reporting_hierarchy_tree(HIERARCHY, as_of=as_of)


def _index(result):
    """code -> (node, parent code) over the whole tree."""
    out = {}

    def walk(node, parent):
        assert node["member_code"] not in out, f"{node['member_code']} twice"
        out[node["member_code"]] = (node, parent)
        for child in node["children"]:
            walk(child, node["member_code"])

    for root in result["tree"]:
        walk(root, None)
    return out


def test_moved_child_sits_under_its_parent_of_the_date():
    before = _index(_tree("2018-06-30"))
    assert before["ZZ_EX"][1] == "ZZ_E"
    after = _index(_tree("2025-06-30"))
    assert after["ZZ_EX"][1] == "ZZ_B"
    assert "ZZ_E" not in after


def test_renamed_member_shows_the_label_of_the_date():
    old = _index(_tree("2024-06-30"))["ZZ_A"][0]
    assert old["member_label"] == "ZZ Old A"
    assert old["name"] == "RHM-A1"
    assert old["effective_from"] == "2017-01-01"
    assert old["effective_to"] == "2024-12-31"
    new = _index(_tree("2025-06-30"))["ZZ_A"][0]
    assert new["member_label"] == "ZZ New A"
    assert new["name"] == "RHM-A2"
    assert new["effective_from"] == "2025-01-01"
    assert new["effective_to"] is None


def test_child_follows_the_parent_code_not_the_linked_row():
    # ZZ_AX links the old ZZ_A row; in 2025 it sits under the new tranche.
    idx = _index(_tree("2025-06-30"))
    assert idx["ZZ_AX"][1] == "ZZ_A"
    assert [c["member_code"] for c in idx["ZZ_A"][0]["children"]] == ["ZZ_AX"]


def test_ended_member_is_absent_after_its_end():
    assert "ZZ_C" not in _index(_tree("2024-06-30"))
    assert _index(_tree("2019-06-30"))["ZZ_C"][1] == "ZZ_B"


def test_window_bounds_are_inclusive():
    assert _index(_tree("2024-12-31"))["ZZ_EX"][1] == "ZZ_E"
    assert _index(_tree("2025-01-01"))["ZZ_EX"][1] == "ZZ_B"


def test_as_of_defaults_to_today():
    result = _load_api().get_reporting_hierarchy_tree(HIERARCHY)
    idx = _index(result)
    assert idx["ZZ_A"][0]["member_label"] == "ZZ New A"
    assert idx["ZZ_EX"][1] == "ZZ_B"
    assert result["as_of"] == TODAY


def test_one_root_and_header_fields():
    result = _tree("2025-06-30")
    assert [r["member_code"] for r in result["tree"]] == ["ZZ_TOP"]
    assert result["hierarchy_name"] == HIERARCHY
    assert result["status"] == "Published"
    assert result["as_of"] == "2025-06-30"


def test_invalid_as_of_is_refused():
    api = _load_api()
    for bad in ("30/06/2025", "2025-13-01", "yesterday"):
        try:
            api.get_reporting_hierarchy_tree(HIERARCHY, as_of=bad)
        except Refused as exc:
            assert str(exc) == "as_of must be a date (YYYY-MM-DD)."
        else:
            raise AssertionError(f"{bad!r} accepted")
