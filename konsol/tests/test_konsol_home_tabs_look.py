"""konsol#497 row 10: look and dark theme of the Konsol Home Tabs block.

The block renders in a shadow root, so ancestor selectors (body[...],
[data-theme=dark] ...) never match inside it. Static checks on the CSS file.
"""
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_PATH = os.path.join(
	APP_DIR, "consolidation", "custom_html_block", "konsol_home_tabs", "konsol_home_tabs.css"
)


def _code():
	with open(CSS_PATH) as f:
		return re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)


def _rules():
	"""(selector, body) for every rule, including those inside @container."""
	out = []
	for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", _code()):
		sel = m.group(1).strip()
		if sel.startswith("@"):
			continue
		out.append((sel, m.group(2)))
	return out


def _selectors():
	return [s.strip() for sel, _ in _rules() for s in sel.split(",") if s.strip()]


def _body_of(fragment):
	return "\n".join(b for sel, b in _rules() if fragment in sel)


def _has(fragment):
	return any(fragment in s for s in _selectors())


def test_every_selector_scoped():
	sels = _selectors()
	assert sels
	bad = [s for s in sels if not (s.startswith(".konsol-home-tabs") or s.startswith(":host"))]
	assert not bad, bad


def test_no_colour_literals():
	code = _code()
	assert not re.search(r"#[0-9a-fA-F]{3,8}\b", code)
	assert not re.search(r"\brgba?\(", code)


def test_no_body_or_theme_ancestor_selector():
	code = _code()
	assert "body[" not in code
	assert "data-theme" not in code


def test_accent_uses_brand_not_primary():
	code = _code()
	assert "--konsol-brand" in code
	assert "--primary" not in code


def test_stat_tile_selectors_present():
	for frag in (
		".widget.number-widget-box",
		".number .indicator-pill",
		".number .indicator-pill::before",
	):
		assert _has(frag), frag


def test_stat_tile_pill_shown_as_value():
	body = _body_of(".number .indicator-pill")
	assert "var(--text-xl)" in body
	assert "var(--heading-color)" in body


def test_checklist_selectors_present():
	for cls in (
		".kc-head", ".kc-title", ".kc-count", ".kc-progress", ".kc-progress-fill",
		".kc-list", ".kc-line", ".kc-glyph.indicator-pill", ".kc-body",
		".kc-label", ".kc-error", ".kc-detail", ".kc-loading",
	):
		assert _has(cls), cls


def test_checklist_progress_fill_uses_brand():
	assert "var(--konsol-brand" in _body_of(".kc-progress-fill")


def test_close_sections_present():
	for cls in (".khp-cards", ".khp-checklist", ".khp-monthly"):
		assert _has(cls), cls


def test_chart_text_uses_theme_variable():
	assert re.search(r"fill:\s*var\(--text-muted\)", _body_of("frappe-chart"))


def test_chart_actions_hidden():
	assert re.search(r"display:\s*none", _body_of(".chart-actions"))


def test_radii_are_2px_or_round():
	for m in re.finditer(r"border-radius:\s*([^;]+);", _code()):
		assert m.group(1).strip() in ("0", "2px", "50%", "inherit"), m.group(1)


def test_headline_is_styled():
	# #497 row 10b: row 6's headline (text, detail, Start) has rules of its own.
	for cls in (".khp-headline", ".khp-headline-text", ".khp-headline-detail"):
		assert _has(cls), cls
	assert "var(--konsol-brand" in _body_of(".khp-headline a")


def test_every_block_class_the_script_creates_is_styled():
	# #497 row 10c: a khp-* class the script builds (rows 6, 8, 9 added some
	# after row 10) must have a rule, or it renders unstyled.
	import pathlib
	js = (pathlib.Path(__file__).resolve().parents[1] / "consolidation" / "custom_html_block"
	      / "konsol_home_tabs" / "konsol_home_tabs.js").read_text()
	used = sorted(set(re.findall(r'"(khp-[a-z-]+)"', js)))
	assert used, "no khp-* classes found in the script"
	missing = [c for c in used if not re.search(r"\." + re.escape(c) + r"(?![a-z-])", _code())]
	assert not missing, missing
