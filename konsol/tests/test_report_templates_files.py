"""The shipped report template files load, validate, and match report_compiler's text (konsol#548 v1 T4)."""
import json
import os

from konsol import report_compiler as rc
from konsol import report_template_model as model

DIR = os.path.join(os.path.dirname(rc.__file__), "report_templates")


def _load():
    return model.load_templates(DIR)


def test_both_files_load_and_validate():
    templates = _load()
    assert list(templates) == ["pnl_monthly", "trial_balance_long"]
    for name in ("pnl_monthly", "trial_balance_long"):
        with open(os.path.join(DIR, f"{name}.json"), encoding="utf-8") as f:
            assert model.validate(json.load(f), name) == []


def test_pnl_text_equals_the_pre_template_compiler_text():
    # The literals report_compiler hard-coded before T5 made the file the
    # only source; the golden fixtures pin the same text in the cell maps.
    sign = "debit positive; a negative total is a profit"
    t = _load()["pnl_monthly"]
    assert t["sign"]["sentence"] == sign
    assert t["errors"]["no_rows"] == (
        "The group chart has no Published Profit and Loss accounts, "
        "so the monthly P&L has no lines."
    )
    total = t["rows"]["blocks"][-1]["caption"]
    assert total.replace("{sign.sentence}", sign) == f"Net Profit and Loss ({sign})"
    assert t["sheet_name"] == "P&L"
    assert t["title"] == "Monthly P&L"


def test_tb_text_equals_compiler_constants():
    t = _load()["trial_balance_long"]
    assert t["title"] == rc.TB_LONG_TITLE
    notes = [c for c in t["header"] if c["at"] == "A2"]
    assert notes[0]["text"] == rc.TB_LONG_NOTE
    assert [f["h"] for f in t["fields"]] == rc.TB_LONG_HEADERS
    assert t["max_rows"] == rc.TB_LONG_MAX_ROWS
    assert t["sheet_name"] == "TB"


def test_several_charts_message_matches_api():
    t = _load()["pnl_monthly"]
    msg = t["errors"]["several_charts"]
    with open(os.path.join(os.path.dirname(rc.__file__), "api.py"), encoding="utf-8") as f:
        src = f.read()
    assert "The monthly P&L reads one chart, and Published Profit and Loss accounts exist in " in src
    assert msg == (
        "The monthly P&L reads one chart, and Published Profit and Loss accounts exist in "
        "charts {charts}. Make one chart Inactive or Draft."
    )
