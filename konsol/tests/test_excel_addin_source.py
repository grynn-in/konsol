"""Source-text checks for the Excel add-in page (konsol#211).

The add-in is plain HTML/JS run inside Excel, so these tests read
konsol/public/excel-addin/index.html as text; they import neither frappe nor a
browser.
"""
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDIN = os.path.join(APP_DIR, "public", "excel-addin", "index.html")


def _src():
    with open(ADDIN) as f:
        return f.read()


def _apply_cell_map_body():
    src = _src()
    start = src.index("function applyCellMap(")
    end = src.index("\n      function ", start + 1)
    return src[start:end]


def test_apply_cell_map_clears_used_range_before_writing():
    # A re-apply with fewer P&L accounts must not leave the old rows and the
    # old total below the new total.
    body = _apply_cell_map_body()
    writes = [m.start() for m in re.finditer(r"\.(formulas|values)\s*=", body)]
    assert writes, "applyCellMap writes no .formulas/.values"
    first_write = min(writes)
    used = body.find("getUsedRange(")
    clear = body.find(".clear(")
    assert 0 <= used < first_write, "getUsedRange must come before the first write"
    assert 0 <= clear < first_write, ".clear( must come before the first write"
    assert used < clear, ".clear( must act on the used range"


def test_addin_shows_no_customer_sample():
    src = _src()
    assert '"AMUS"' not in src
    assert '"4010"' not in src
