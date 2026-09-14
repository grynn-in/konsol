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
PENDING = {
    "Trial Balance Submission",
}


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
