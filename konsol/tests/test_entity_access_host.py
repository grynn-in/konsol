"""entity_permissions.assert_entity_access, exercised rather than read.

The ClickHouse read paths (K.EPM, hierarchy reads, budget reads) bypass
Frappe's permission layer, so this one function is what stops a user
limited to some entities from reading the others. Loaded under a private
module name with a stub frappe, so it runs on any host.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Denied(Exception):
    pass


def _load(allowed):
    """entity_permissions with allowed_entity_codes() fixed to ``allowed``
    (a set of codes, or None for an unrestricted user)."""
    fake = types.ModuleType("frappe")
    fake.PermissionError = _Denied
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = fake
    try:
        spec = importlib.util.spec_from_file_location(
            "_host_entity_permissions", os.path.join(APP_DIR, "entity_permissions.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    mod.allowed_entity_codes = lambda user=None: allowed
    return mod


def _raises(fn, *args):
    try:
        fn(*args)
    except _Denied:
        return True
    return False


def test_restricted_user_is_refused_other_entities():
    ep = _load({"DE01", "AT01"})
    assert not _raises(ep.assert_entity_access, "DE01")
    assert _raises(ep.assert_entity_access, "US01")


def test_user_with_no_entities_is_refused_everything():
    ep = _load(set())
    assert _raises(ep.assert_entity_access, "DE01")


def test_unrestricted_user_passes():
    ep = _load(None)
    assert not _raises(ep.assert_entity_access, "US01")
