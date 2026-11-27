"""A change to functions.json ships with a new manifest Version (6 Nov 2026).

#393 changed every dimensional function's parameters without raising the
manifest's <Version>, so Excel kept the old functions.json and refused every
cell ("takes at most 11 arguments, not 12"). When functions.json changes, raise
<Version> in both manifests and update the pair below.
"""
import hashlib
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDIN = os.path.join(APP_DIR, "public", "excel-addin")
TEMPLATE = os.path.join(APP_DIR, "excel_addin_manifest.template.xml")

#: functions.json's sha256 prefix, and the manifest Version that ships it.
#: 2.0.2.0 (ribbon Refresh buttons, 7 Nov 2026) left functions.json unchanged;
#: the pair moves because the test pins the Version too. 2.0.3.0 (konsol#348
#: S2-X3): K.EPMSAVE takes currency and amountBasis. 2.0.4.0 (konsol#348 S3
#: K5): K.EPMSAVE takes asOfDate, the forecast round. 2.0.5.0 (konsol#348 S6
#: KC1): K.EPM's scenario tooltip lists the Dataset words, the S6 ones included.
#: 2.0.6.0 (konsol#618 review): functions.json unchanged; the server moves into
#: the workbook and functions.js survives a cached page without server.js, so a
#: new Version makes Excel take the new files.
SHIPPED = ("988c0179f42299f5", "2.0.6.0")


def _version():
    with open(TEMPLATE) as f:
        return re.search(r"<Version>([^<]+)</Version>", f.read()).group(1)


def test_functions_json_change_ships_with_a_new_manifest_version():
    with open(os.path.join(ADDIN, "functions.json"), "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:16]
    assert (digest, _version()) == SHIPPED, (
        "functions.json changed: raise <Version> in excel_addin_manifest.template.xml, "
        "then update SHIPPED")
