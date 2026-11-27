"""Capture report_compiler outputs as golden JSON (konsol#548 v1, T1).

python3 scripts/capture_report_golden.py [--compiler PATH] [--out DIR]

Loads the compiler BY PATH, so it can be pointed at another copy (e.g. main's)
to prove a refactor leaves every output identical. Default: the working-tree
konsol/report_compiler.py, writing konsol/tests/fixtures/report_golden/.
"""
import argparse
import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_COMPILER = os.path.join(ROOT, "konsol", "report_compiler.py")
DEFAULT_OUT = os.path.join(ROOT, "konsol", "tests", "fixtures", "report_golden")

A1 = ("4000", "Revenue")
A2 = ("5000", "Cost of sales")
A3 = ("6100", "Salaries")
A_BLANK = ("6200", "")

PNL_ACCOUNTS = {
    "1acct": [A1],
    "2acct": [A1, A2],
    "3acct_blank_name": [A1, A_BLANK, A3],
}

TB_ROWS_FULL = [
    {"entity": "ZZ1", "year": 2024, "period": 1, "account": "1000",
     "account_name": "Cash", "account_type": "Asset", "bs_pnl": "BS",
     "debit": 100.5, "credit": 0, "net": 100.5},
    {"entity": "ZZ1", "year": 2024, "period": 3, "account": "4000",
     "account_name": "Revenue", "account_type": "Income", "bs_pnl": "PL",
     "debit": "0", "credit": "250.25", "net": "-250.25"},
]
TB_ROWS_ODD = [
    {"period": 4, "account": "1000", "debit": None, "credit": None, "net": None},
    {"period": 5, "account": "2000", "account_name": "Payables",
     "debit": "n/a", "credit": "abc", "net": ""},
    {},
    {"entity": "Other", "year": 2023, "account": "3000", "debit": "1e2",
     "credit": 7, "net": [1]},
]


# OPN, P01..P12 each starting the 1st of its month, CLS. Only the Regular ones
# are report columns, so only they reach the compiler.
CALENDAR = (
    [{"number": 0, "code": "OPN", "label": "Opening", "period_type": "Opening",
      "start_date": "2024-01-01"}]
    + [{"number": m, "code": f"P{m:02d}", "label": f"P{m:02d}", "period_type": "Regular",
        "start_date": f"2024-{m:02d}-01"} for m in range(1, 13)]
    + [{"number": 13, "code": "CLS", "label": "Closing", "period_type": "Closing",
        "start_date": "2024-12-31"}]
)
JAN_DEC = [p for p in CALENDAR if p["period_type"] == "Regular"]


def cases():
    """name -> (function name, args, kwargs). Order is the capture order."""
    out = {}
    for tag, accts in PNL_ACCOUNTS.items():
        for scenario in ("actuals", "BUD"):
            for year in (2024, 2026):
                out[f"pnl_monthly__{tag}__{scenario}__{year}"] = (
                    "build_cell_map",
                    ["pnl_monthly", "ZZ1", year],
                    {"scenario_id": scenario, "accounts": [list(a) for a in accts],
                     "periods": JAN_DEC},
                )
    out["pnl_monthly__entity_double_quote"] = (
        "build_cell_map", ["pnl_monthly", 'ZZ "Q" Ltd', 2024],
        {"scenario_id": "actuals", "accounts": [list(A1), ['40"00', 'Rev "x"']],
         "periods": JAN_DEC},
    )
    for tag, rows in (("0rows", []), ("2rows", TB_ROWS_FULL), ("odd_rows", TB_ROWS_ODD)):
        for pf, pt in ((1, 12), (3, 5)):
            out[f"tb_long__{tag}__p{pf}_{pt}"] = (
                "build_trial_balance_long_map", ["ZZ1", 2024, rows],
                {"period_from": pf, "period_to": pt},
            )
    out["list_templates"] = ("list_templates", [], {})
    return out


def load_compiler(path):
    spec = importlib.util.spec_from_file_location("report_compiler_under_capture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def compute(mod, case):
    fn, args, kwargs = case
    kwargs = dict(kwargs)
    if "accounts" in kwargs:
        kwargs["accounts"] = [tuple(a) for a in kwargs["accounts"]]
    got = getattr(mod, fn)(*args, **kwargs)
    if isinstance(got, dict):
        got.pop("report", None)
    return got


def dumps(value):
    return json.dumps(value, indent=1, ensure_ascii=False) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compiler", default=DEFAULT_COMPILER)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    mod = load_compiler(a.compiler)
    os.makedirs(a.out, exist_ok=True)
    for name, case in cases().items():
        with open(os.path.join(a.out, f"{name}.json"), "w") as f:
            f.write(dumps(compute(mod, case)))
    print(f"wrote {len(cases())} fixtures to {a.out}")


if __name__ == "__main__":
    main()
