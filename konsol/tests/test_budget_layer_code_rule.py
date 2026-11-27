"""Budget Layer code rule (konsol#564): the readers refuse a layer that is not
a lower-case identifier ("Invalid layer format"), so a Budget Layer must refuse
the same code at save. One definition (konsol/identifier_rule.py), used by the
K.EPM readers and by the doctype."""
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULE_PY = os.path.join(APP_DIR, "identifier_rule.py")
LAYER_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_layer", "budget_layer.py")


class _Thrown(Exception):
    pass


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _layer(monkeypatch, code):
    class Document:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe.ValidationError = _Thrown

    def throw(msg, exc=None, *a, **k):
        raise _Thrown(msg)

    frappe.throw = throw
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document
    for n, m in {"frappe": frappe, "frappe.model": types.ModuleType("frappe.model"),
                 "frappe.model.document": docmod}.items():
        monkeypatch.setitem(sys.modules, n, m)
    rule = _load(RULE_PY, "konsol.identifier_rule")
    konsol = sys.modules.get("konsol") or types.ModuleType("konsol")
    monkeypatch.setattr(konsol, "identifier_rule", rule, raising=False)
    monkeypatch.setitem(sys.modules, "konsol", konsol)
    monkeypatch.setitem(sys.modules, "konsol.identifier_rule", rule)
    mod = _load(LAYER_PY, "_budget_layer_under_test")
    return mod.BudgetLayer(layer_code=code, label="L")


@pytest.mark.parametrize("code", ["ZZS6CL", "Base", "1base", "base-x", "base x", "_base", "bäse"])
def test_a_code_the_readers_refuse_is_refused_at_save_naming_rule_and_code(monkeypatch, code):
    with pytest.raises(_Thrown) as info:
        _layer(monkeypatch, code).validate()
    message = str(info.value)
    assert code in message, message
    assert "lower-case" in message and "a-z" in message, message


@pytest.mark.parametrize("code", ["base", "challenge", "zzs6cl", "q3_board", "a1"])
def test_a_code_the_readers_accept_saves(monkeypatch, code):
    _layer(monkeypatch, code).validate()


def test_the_rule_is_one_definition_the_readers_import():
    rule = _load(RULE_PY, "identifier_rule_under_test")
    assert rule.SAFE_IDENTIFIER.match("base") and not rule.SAFE_IDENTIFIER.match("ZZS6CL")
    # A trailing newline must not slip past `$`.
    assert not rule.SAFE_IDENTIFIER.match("base\n")
    for name in ("api.py", "hierarchy_query.py"):
        with open(os.path.join(APP_DIR, name)) as f:
            src = f.read()
        assert "from konsol.identifier_rule import SAFE_IDENTIFIER as _SAFE_IDENTIFIER" in src, name
        assert "_SAFE_IDENTIFIER = re.compile" not in src, f"{name} keeps its own copy of the rule"
