"""A change to functions.json ships with a new manifest Version (6 Nov 2026).

#393 changed every dimensional function's parameters without raising the
manifest's <Version>, so Excel kept the old functions.json and refused every
cell ("takes at most 11 arguments, not 12"). When functions.json changes, raise
<Version> in both manifests and update the pair below.
"""
import hashlib
import os
import re

ADDIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public", "excel-addin")

#: functions.json's sha256 prefix, and the manifest Version that ships it.
SHIPPED = ("5c3367c9e9325489", "2.0.1.0")


def _version(name):
    with open(os.path.join(ADDIN, name)) as f:
        return re.search(r"<Version>([^<]+)</Version>", f.read()).group(1)


def test_functions_json_change_ships_with_a_new_manifest_version():
    with open(os.path.join(ADDIN, "functions.json"), "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:16]
    assert (digest, _version("manifest.xml")) == SHIPPED, (
        "functions.json changed: raise <Version> in manifest.xml and "
        "manifest.demo.xml, then update SHIPPED")


def test_both_manifests_carry_the_same_version():
    assert _version("manifest.xml") == _version("manifest.demo.xml")
