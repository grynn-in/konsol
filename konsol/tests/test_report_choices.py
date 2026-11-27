"""report_choices: the entity and group lists the Excel add-in's report pickers
show, and the is_allowed check the server runs on submitted parameters.

Each list is filtered by the same rule the read paths use (entity_permissions
.entity_read_scope, the rule epm_batch applies), so a picker never offers a
code the server would refuse. Loaded under a stub frappe, so it runs on any
host; no pytest fixtures (the host runner skips those).
"""
import contextlib
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Row(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


ENTITIES = [
    {"entity_code": "US01", "entity_name": "US Co", "is_group": 0,
     "functional_currency": "USD", "status": "Active", "lft": 1, "rgt": 2},
    {"entity_code": "DE01", "entity_name": "DE GmbH", "is_group": 0,
     "functional_currency": "EUR", "status": "Active", "lft": 3, "rgt": 4},
    {"entity_code": "AT01", "entity_name": "AT GmbH", "is_group": 0,
     "functional_currency": "EUR", "status": "Dormant", "lft": 5, "rgt": 6},
]

# Two groups. GRP-A's root sits in DE01 and its member in AT01; GRP-B's root
# sits in US01. A member row carries the root's code and a parent.
GROUPS = [
    {"consolidation_group": "GRP-A", "entity_name": "DE GmbH",
     "data_area_id": "DE01", "parent_consolidation_group": None},
    {"consolidation_group": "GRP-A", "entity_name": "AT GmbH",
     "data_area_id": "AT01", "parent_consolidation_group": "GRP-A"},
    {"consolidation_group": "GRP-B", "entity_name": "US Co",
     "data_area_id": "US01", "parent_consolidation_group": None},
]


def _fake_frappe(user, roles, perms):
    fake = types.ModuleType("frappe")
    fake.PermissionError = type("PermissionError", (Exception,), {})
    fake.session = types.SimpleNamespace(user=user)
    fake.get_roles = lambda u=None: list(roles)
    fake.permissions = types.SimpleNamespace(
        get_user_permissions=lambda u=None: {"Entity": [{"doc": p} for p in perms]})
    fake.get_cached_value = lambda *a, **k: None
    fake.db = types.SimpleNamespace(sql=lambda *a, **k: [])

    def get_all(doctype, filters=None, fields=None, order_by=None,
                pluck=None, limit_page_length=None, **k):
        # The picker lists must say their order: get_all sorts by creation
        # otherwise. The subtree lookup (lft/rgt) is order-free and is skipped.
        if not (fields and "lft" in fields):
            assert order_by, f"get_all({doctype}) without order_by"
        if doctype == "Entity":
            rows = ENTITIES
            if filters and "name" in filters:
                _, names = filters["name"]
                rows = [r for r in rows if r["entity_code"] in names]
            rows = [_Row({**r, "name": r["entity_code"]}) for r in rows]
        elif doctype == "Consolidation Group":
            rows = [_Row({**r, "name": f"CG-{r['consolidation_group']}-{r['data_area_id']}"})
                    for r in GROUPS]
        else:
            return []
        # Frappe sorts by order_by; the stub must too, or a picker's order is untested.
        if order_by:
            for clause in reversed(order_by.split(",")):
                key, _, direction = clause.strip().partition(" ")
                rows.sort(key=lambda r: r.get(key) or "",
                          reverse=direction.lower() == "desc")
        return rows

    fake.get_all = get_all
    return fake


@contextlib.contextmanager
def _stubbed(fake):
    """Put the stub frappe, and the real entity_permissions, where the code under
    test imports them, for the length of one call."""
    names = ("frappe", "konsol.entity_permissions")
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules["frappe"] = fake
    spec = importlib.util.spec_from_file_location(
        "_report_choices_ep", os.path.join(APP_DIR, "entity_permissions.py"))
    ep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ep)
    sys.modules["konsol.entity_permissions"] = ep
    try:
        yield
    finally:
        for n, v in saved.items():
            if v is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = v


def _call(name, *args, user="Administrator", roles=(), perms=()):
    fake = _fake_frappe(user, roles, perms)
    with _stubbed(fake):
        spec = importlib.util.spec_from_file_location(
            "_report_choices_under_test", os.path.join(APP_DIR, "report_choices.py"))
        rc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rc)
        return getattr(rc, name)(*args)


RESTRICTED = {"user": "viewer", "roles": [], "perms": ["DE01"]}


def test_unrestricted_user_sees_every_entity_sorted_by_code():
    rows = _call("entities")
    assert [r["code"] for r in rows] == ["AT01", "DE01", "US01"]
    first = rows[0]
    assert set(first) == {"code", "name", "is_group", "currency", "status"}
    assert first["name"] == "AT GmbH"
    assert first["currency"] == "EUR"
    assert first["status"] == "Dormant"


def test_restricted_user_sees_only_allowed_entities():
    rows = _call("entities", **RESTRICTED)
    assert [r["code"] for r in rows] == ["DE01"]


def test_unrestricted_user_sees_every_group_code_once():
    rows = _call("groups")
    assert [r["code"] for r in rows] == ["GRP-A", "GRP-B"]
    assert len({r["code"] for r in rows}) == len(rows)


def test_group_name_comes_from_the_root_row():
    rows = {r["code"]: r for r in _call("groups")}
    assert rows["GRP-A"]["name"] == "DE GmbH"
    assert rows["GRP-B"]["name"] == "US Co"


def test_restricted_user_sees_only_groups_whose_root_entity_is_allowed():
    rows = _call("groups", **RESTRICTED)
    assert [r["code"] for r in rows] == ["GRP-A"]


def test_is_allowed_accepts_listed_code_and_refuses_unknown():
    assert _call("is_allowed", "entity", "DE01") is True
    assert _call("is_allowed", "entity", "ZZ99") is False
    assert _call("is_allowed", "group", "GRP-A") is True
    assert _call("is_allowed", "group", "GRP-NOPE") is False


def test_is_allowed_applies_the_reader_permission():
    assert _call("is_allowed", "entity", "US01", **RESTRICTED) is False
    assert _call("is_allowed", "group", "GRP-B", **RESTRICTED) is False
    assert _call("is_allowed", "group", "GRP-A", **RESTRICTED) is True


def test_is_allowed_refuses_an_unknown_kind():
    try:
        _call("is_allowed", "report", "DE01")
    except ValueError:
        return
    raise AssertionError("an unknown kind must be refused, not answered")
