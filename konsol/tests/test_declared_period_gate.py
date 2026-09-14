"""Every document that carries a fiscal year + period refuses an undeclared one.

konsol.period_status.assert_declared raises PeriodNotDeclared naming the
missing year or period; assert_postable implies it. Each listed controller's
`validate` must reach one of them, directly or through a method of its own
class that it calls.

PENDING lists the doctypes not yet wired. The test also asserts every pending
doctype still lacks the gate, so wiring one fails here until it is removed
from PENDING: the set only shrinks."""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATES = {"assert_declared", "assert_postable"}
CONTROLLERS = {
    "Consolidation Adjustment": "consolidation/doctype/consolidation_adjustment/consolidation_adjustment.py",
    "IC Balance": "consolidation/doctype/ic_balance/ic_balance.py",
    "Allocation Run": "allocation/doctype/allocation_run/allocation_run.py",
    "Allocation Driver": "allocation/doctype/allocation_driver/allocation_driver.py",
    "Group Exchange Rate": "consolidation/doctype/group_exchange_rate/group_exchange_rate.py",
    "Trial Balance Submission": "consolidation/doctype/trial_balance_submission/trial_balance_submission.py",
}
PENDING = set()


def _called(fn):
    """Bare names called, and ``self.<m>`` as "self.<m>"; any other attribute
    call (``period_status.assert_declared``) by its attribute."""
    out = set()
    for c in ast.walk(fn):
        if isinstance(c, ast.Call):
            f = c.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f"self.{f.attr}" if ast.unparse(f.value) == "self" else f.attr)
    return out


def _validate_reaches_gate(doctype):
    path = os.path.join(APP_DIR, CONTROLLERS[doctype])
    assert os.path.exists(path), f"{doctype}: no controller at {path}"
    with open(path) as f:
        tree = ast.parse(f.read())
    # a gate imported under another name is still a gate
    gates = GATES | {a.asname for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                     for a in n.names if a.name in GATES and a.asname}
    cls_name = doctype.replace(" ", "")
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name), None)
    assert cls is not None, f"{doctype}: no class {cls_name} in {path}"
    methods = {n.name: _called(n) for n in cls.body if isinstance(n, ast.FunctionDef)}
    if "validate" not in methods:
        return False
    seen, todo = set(), ["validate"]
    while todo:
        m = todo.pop()
        if m in seen or m not in methods:
            continue
        seen.add(m)
        calls = methods[m]
        if calls & gates:
            return True
        todo.extend(c[5:] for c in calls if c.startswith("self."))
    return False


def test_declared_gate_in_validate():
    assert PENDING <= set(CONTROLLERS), PENDING - set(CONTROLLERS)
    missing = [dt for dt in sorted(set(CONTROLLERS) - PENDING) if not _validate_reaches_gate(dt)]
    assert not missing, f"validate does not reach assert_declared/assert_postable: {missing}"
    wired = [dt for dt in sorted(PENDING) if _validate_reaches_gate(dt)]
    assert not wired, f"now gated, remove from PENDING: {wired}"


# ---------------------------------------------------------------------------
# test_no_stale_period_readers (konsol#189)
# ---------------------------------------------------------------------------
# No Python code may keep reading the retired "Period Status" doctype or the
# retiring "Fiscal Period" template, or assume a fixed 0..13 calendar. The
# declared calendar (EPM Fiscal Year / EPM Fiscal Year Period, read through
# konsol/period_status.py and konsol/fiscal_calendar.py) is the only source
# of truth now.
_STALE_DOCTYPES = {"Period Status", "Fiscal Period"}

#: Whole files/directories (relative to the konsol package dir) this scan
#: never looks at, because they are the retired doctypes themselves, the
#: retire/migration machinery, or explicitly out of scope per the konsol#189
#: plan section 1.3.
_ALLOWED_PATHS = (
    "patches/",  # the retire/migration patch(es) that move data off the old doctypes
    "epm/doctype/period_status/",  # the retired controller itself
    "epm/doctype/fiscal_period/",  # the (still-live) template controller, retired later
    "dbt_config.py",  # feeds gold_period_hierarchy from the template; retired in a later task
)

#: Specific (path, lineno) exceptions that plan section 1.3 names as staying
#: on a fixed 12-month calendar by design (budget is monthly columns, not
#: declared periods), plus migration-only readers that must keep reading the
#: retiring doctypes until they are actually retired (PR4, konsol#189).
_ALLOWED_LINES = {
    ("api.py", 386): "budget: _resolve_period, PERIOD_RANGES (Q/H/FY) for Excel reads (plan 1.3)",
    ("api.py", 927): "budget: period_from/period_to are wide monthly columns (plan 1.3)",
    ("api.py", 1184): "budget: fiscal_period is a wide monthly column (plan 1.3)",
    ("epm/budget_periods.py", 10): "budget: PERIOD_FIELDS is twelve monthly columns by design (plan 1.3)",
    # TODO konsol#189: fiscal_calendar.period_status_rows() is the migration
    # planner shared by the create_fiscal_years patch and declare_years_in_use
    # (plan 3.1/3.4: "nothing reads [Period Status] any more except the
    # migration patch"). It stops reading Period Status only when Period
    # Status is actually dropped in PR4/task 90.
    ("fiscal_calendar.py", 188): "TODO konsol#189: migration planner reads Period Status until PR4 retires it",
    ("fiscal_calendar.py", 195): "TODO konsol#189: migration planner reads Period Status until PR4 retires it",
}


def _allowed(relpath, lineno):
    if relpath.startswith("tests" + os.sep):
        return True
    if any(relpath == p or relpath.startswith(p) for p in _ALLOWED_PATHS):
        return True
    return (relpath, lineno) in _ALLOWED_LINES


def _period_named(node):
    """True if the AST node's source text mentions "period" — used to tell a
    fiscal-period bound check (``fiscal_period <= 12``) apart from an
    unrelated number (a timeout, a percentage) that just happens to be 12 or
    13."""
    try:
        return "period" in ast.unparse(node).lower()
    except Exception:
        return False


def _stale_reads(path):
    """Every stale-reader offense in one file, as (lineno, reason)."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        and arg.value in _STALE_DOCTYPES):
                    out.append((node.lineno, f'reads doctype "{arg.value}"'))
            if isinstance(node.func, ast.Name) and node.func.id == "range":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == 14:
                        out.append((node.lineno, "range(14): a fixed 0..13 calendar"))
        elif isinstance(node, ast.Compare):
            chain = [node.left] + node.comparators
            for i, op in enumerate(node.ops):
                a, b = chain[i], chain[i + 1]
                if not isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)):
                    continue
                for lhs, rhs in ((a, b), (b, a)):
                    if (isinstance(rhs, ast.Constant) and rhs.value in (12, 13)
                            and _period_named(lhs)):
                        out.append((node.lineno,
                                    f"compares a period against {rhs.value}: {ast.unparse(node)}"))
    return out


def test_no_stale_period_readers():
    offenders = []
    for dirpath, dirnames, filenames in os.walk(APP_DIR):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            relpath = os.path.relpath(full, APP_DIR)
            if relpath.startswith("tests" + os.sep):
                continue
            for lineno, reason in _stale_reads(full):
                if _allowed(relpath, lineno):
                    continue
                offenders.append(f"konsol/{relpath}:{lineno} {reason}")
    assert not offenders, "stale Period Status / Fiscal Period / 0..13 reader(s):\n" + "\n".join(offenders)
