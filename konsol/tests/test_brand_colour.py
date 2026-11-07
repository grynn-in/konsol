"""Brand colour (konsol#415; Deepak Pai, 7 Nov 2026: "lets go with : Teal-green
#0F766E (my pick)"). One per-site colour in Close Settings, its declared
default #0F766E, refused under 4.5 : 1 against white text (WCAG AA). The Desk's
primary accent takes it; hover and tint are derived from it.
"""
import ast
import importlib.util
import json
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, rel))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


brand_model = _load("konsol_brand_model", "brand_model.py")


def _read(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return f.read()


# --- the pure rule -------------------------------------------------------------


@pytest.mark.parametrize("raw, normal", [
    ("#0F766E", "#0F766E"), ("#0f766e", "#0F766E"), ("0F766E", "#0F766E"),
    ("  #0f766e ", "#0F766E"), ("#abc", "#AABBCC"),
])
def test_a_hex_is_normalised(raw, normal):
    assert brand_model.normalise(raw) == normal


@pytest.mark.parametrize("raw", ["teal", "#12345", "#GGGGGG", "rgb(1,2,3)", "#0F766E00"])
def test_anything_else_is_refused_naming_the_value(raw):
    with pytest.raises(ValueError) as e:
        brand_model.normalise(raw)
    assert raw.strip() in str(e.value)


@pytest.mark.parametrize("hex_, ratio", [
    ("#0F766E", 5.47), ("#0F5132", 9.36), ("#166534", 7.13), ("#FFFFFF", 1.0), ("#000000", 21.0),
])
def test_contrast_against_white_is_wcag(hex_, ratio):
    assert round(brand_model.contrast_on_white(hex_), 2) == ratio


def test_a_colour_white_text_cannot_be_read_on_is_refused_with_its_ratio():
    with pytest.raises(ValueError) as e:
        brand_model.validate("#5EEAD4")  # a light teal
    assert "4.5" in str(e.value) and "1.4" in str(e.value)


def test_the_chosen_teal_passes_and_derives_its_shades():
    palette = brand_model.palette("#0f766e")
    assert palette == {
        "primary": "#0F766E",
        "strong": "#0C5C56",
        "tint": "#E7F1F0",
        "strong_dark": "#7BB4AF",
        "tint_dark": "#152F2D",
    }


def test_the_declared_default_is_the_chosen_teal():
    assert brand_model.DEFAULT == "#0F766E"
    brand_model.validate(brand_model.DEFAULT)


# --- the setting ---------------------------------------------------------------

SETTINGS = "consolidation/doctype/close_settings/close_settings.json"


def test_close_settings_has_the_brand_colour_on_an_appearance_tab():
    doc = json.loads(_read(SETTINGS))
    fields = {f["fieldname"]: f for f in doc["fields"]}
    field = fields["brand_colour"]
    assert field["fieldtype"] == "Color" and field["label"] == "Brand Colour"
    # Close Settings fields carry no JSON default (test_close_settings); the
    # teal is set once by brand.set_default_if_blank instead.
    assert "default" not in field and not field.get("reqd")
    assert "4.5" in field["description"] and "blank" in field["description"].lower()
    order = doc["field_order"]
    assert order.index("appearance_tab") < order.index("brand_section") < order.index("brand_colour")
    assert fields["appearance_tab"]["fieldtype"] == "Tab Break"


def test_the_controller_validates_the_colour():
    tree = ast.parse(_read("consolidation/doctype/close_settings/close_settings.py"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    validate = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "validate")
    calls = {c.func.attr for c in ast.walk(validate)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
    assert "validate_brand_colour" in calls


# --- the Desk ------------------------------------------------------------------


def _hooks():
    tree = ast.parse(_read("hooks.py"))
    return {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, (ast.List, ast.Constant, ast.Dict, ast.Tuple))}


def test_the_boot_carries_the_palette_and_every_desk_page_loads_the_theme():
    hooks = _hooks()
    assert "konsol.brand.boot_session" in hooks["boot_session"]
    assert "/assets/konsol/js/konsol_brand.js" in hooks["app_include_js"]
    assert os.path.exists(os.path.join(APP_DIR, "public", "js", "konsol_brand.js"))


def test_the_theme_recolours_accents_only():
    js = _read("public/js/konsol_brand.js")
    for needle in ("frappe.boot.konsol_brand", "--primary", "--btn-primary", "--bt-solid-bg",
                   ".active-sidebar", '[data-theme="dark"]', "konsol-brand"):
        assert needle in js, needle
    # Icons and body text keep Frappe's colours.
    for forbidden in ("--text-color", "--ink-gray", "--icon-stroke:", "innerHTML"):
        assert forbidden not in js, forbidden


def test_a_patch_sets_the_teal_where_the_field_is_blank():
    assert "konsol.patches.set_default_brand_colour" in _read("patches.txt").split()
    assert "set_default_if_blank()" in _read("patches/set_default_brand_colour.py")
    assert "konsol.brand.set_default_if_blank" in _hooks()["after_install"]
    tree = ast.parse(_read("brand.py"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "set_default_if_blank")
    body = ast.unparse(fn)
    assert "if not" in body and "brand_model.DEFAULT" in body


def test_the_boot_survives_code_that_is_ahead_of_its_migrate():
    """Measured on 8069: before the doctype reload, get_single_value raises
    "Field brand_colour does not exist", which would fail every Desk boot."""
    source = _read("brand.py")
    fn = source[source.index("def boot_session"):source.index("def set_default_if_blank")]
    assert fn.index('has_field("brand_colour")') < fn.index("get_single_value")


def test_a_changed_colour_drops_every_cached_boot_after_the_commit():
    """Review of #416, shown red on 8069: a save left hget("bootinfo", user)
    holding the old palette, so users kept it until they logged in again."""
    tree = ast.parse(_read("consolidation/doctype/close_settings/close_settings.py"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    on_update = ast.unparse(next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "on_update"))
    assert "has_value_changed('brand_colour')" in on_update
    assert "frappe.db.after_commit.add(_drop_boot_cache)" in on_update
    drop = ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_drop_boot_cache"))
    assert "frappe.cache.delete_key('bootinfo')" in drop
