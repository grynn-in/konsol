"""A DocType linked from a Workspace Sidebar must be routable in the Desk.

A DocType-level `"read_only": 1` is Frappe's "User Cannot Search". In
`frappe/utils/user.py` `build_permissions` puts such a doctype in
`no_list_view_link` and removes it from `can_read`; the v16 Desk router builds
routes only for can_read doctypes. The sidebar link `/desk/<slug>` then has no
route and opens the workspace instead of the list (Dimension Value, 22 Nov 2026).
Keep a list read-only with read-only permissions and read-only fields, not this flag.
"""
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _doctype_files():
    out = {}
    for path in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.json")):
        try:
            meta = json.load(open(path))
        except ValueError:
            continue
        if meta.get("doctype") == "DocType":
            out[meta["name"]] = (path, meta)
    return out


def _sidebar_doctypes():
    names = set()
    for path in glob.glob(os.path.join(APP_DIR, "*", "sidebar", "*", "*.json")):
        for item in json.load(open(path)).get("items", []):
            if item.get("link_type") == "DocType" and item.get("link_to"):
                names.add(item["link_to"])
    return names


def test_dimension_value_has_no_doctype_level_read_only():
    _, meta = _doctype_files()["Dimension Value"]
    assert not meta.get("read_only")


def test_no_sidebar_linked_doctype_is_user_cannot_search():
    docs = _doctype_files()
    linked = _sidebar_doctypes()
    assert linked, "no DocType links found in sidebars: the glob is wrong"
    bad = sorted(n for n in linked if n in docs and docs[n][1].get("read_only") == 1)
    assert not bad, f"doctype-level read_only hides the list from the Desk router: {bad}"
