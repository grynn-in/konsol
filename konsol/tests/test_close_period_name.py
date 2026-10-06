"""The one server-side period label: konsol/close/period_name.py (konsol#305
review-w5). The live ``period_code`` is "P07" alone, ambiguous across a year
boundary, so every server label names the period as "FY2025 P07", built
from the fiscal year and period by ``period_name`` — never a second copy of
the format.
"""
import importlib.util
import os
import re

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _period_name():
    return _load("close_period_name_under_test", os.path.join(CLOSE_DIR, "period_name.py")).period_name


def _journal_model():
    return _load("close_journal_model_for_period_name", os.path.join(CLOSE_DIR, "journal_model.py"))


def _row(fy, fp, status="Open"):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_type": "Regular", "status": status}


def test_period_name_is_the_fiscal_year_and_two_digit_period():
    period_name = _period_name()
    assert period_name(2025, 7) == "FY2025 P07"
    assert period_name("2024", "12") == "FY2024 P12"
    assert period_name(2025, 0) == "FY2025 P00"


def test_period_name_refuses_a_missing_part():
    period_name = _period_name()
    with pytest.raises((TypeError, ValueError)):
        period_name(None, 7)
    with pytest.raises((TypeError, ValueError)):
        period_name(2025, "")


def test_duration_label_names_the_year_when_the_code_is_p07():
    jm = _journal_model()
    rows = [_row(2025, 6), _row(2025, 7)]
    assert jm.duration_label(2025, 7, rows) == "Reverses in FY2025 P07"
    assert jm.duration_label(0, 0, rows) == "This period only, no reversal"


def test_reversal_problem_names_the_year_and_padded_period():
    jm = _journal_model()
    rows = [_row(2025, 6), _row(2025, 7, "Closed")]
    assert jm.reversal_problem(2025, 6, 2025, 7, rows) == "FY2025 P07 is Closed; name an Open period."
    assert jm.reversal_problem(2025, 7, 2025, 6, rows).startswith("FY2025 P06 is not after this journal's period FY2025 P07")


#: Every server module that labels a period. Each reads ``period_name``;
#: none keeps its own "FY… P%02d" format.
LABEL_MODULES = [
    "close/journal_model.py",
    "close/statement_export_model.py",
    "close/statement_model.py",
    "close/approvals_model.py",
    "close/period_model.py",
    "close/signoff_model.py",
    "close/mywork_api.py",
    "close/ic_balance_api.py",
    "close/rates_api.py",
    "consolidation/doctype/close_settings/close_settings.py",
]

_INLINE_FORMAT = re.compile(r"FY%[sd] P%02d|FY\{[^}]*\} P\{[^}]*:02d\}")


@pytest.mark.parametrize("relpath", LABEL_MODULES)
def test_label_modules_share_period_name(relpath):
    with open(os.path.join(APP_DIR, relpath), encoding="utf-8") as f:
        source = f.read()
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    # Docstrings may quote the format; the code may not build it.
    code = re.sub(r'"""[\s\S]*?"""', "", code)
    assert not _INLINE_FORMAT.search(code), relpath
    assert "period_name" in code, relpath
