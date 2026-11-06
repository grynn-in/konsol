"""The add-in panel's version label is the manifest's, never typed by hand (7 Nov 2026).

The panel said "v2.4.0.0" while the manifest was 2.0.0.0 and then 2.0.1.0: a
label nothing read or updated, so it could not tell a user which code ran.
The label now carries the manifest Version, pinned here, and the panel adds
the deploy time of the functions.js it loaded (its Last-Modified header).
"""
import os
import re

ADDIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public", "excel-addin")


def _read(name):
    with open(os.path.join(ADDIN, name)) as f:
        return f.read()


def test_the_label_is_the_manifest_version():
    manifest = re.search(r"<Version>([^<]+)</Version>", _read("manifest.xml")).group(1)
    label = re.search(r'<p class="version" id="addin-version">v([^<\s]+)', _read("index.html"))
    assert label, "the panel has no version label with id addin-version"
    assert label.group(1) == manifest, (label.group(1), manifest)


def test_the_panel_shows_when_its_code_was_deployed():
    src = _read("index.html")
    assert re.search(r'fetch\(\s*"functions\.js"\s*,\s*\{\s*method:\s*"HEAD"', src), "no HEAD of functions.js"
    assert "Last-Modified" in src
