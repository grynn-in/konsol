"""konsol#305 P10: the C1 walk-through declares the two close policies.

``scripts/close_c1_setup.py`` sets up the data C1 needs on a live site. It
already sets Close Settings' first close (``first_close_fiscal_year`` /
``first_close_fiscal_period``); after P05, sign-off is *also* refused while
``self_approval`` or ``rate_move_threshold`` is undeclared
(``self_approval_undeclared`` / ``rate_move_undeclared``,
konsol/close/close_policy_model.py). Unless the setup script declares both
next to the first close, the C1 walk-through's own sign-off step is refused.

This only inspects the script's source with ``ast``: the script itself needs
a live site (frappe.init/connect) and is not importable, let alone run, here.
"""
import ast
import os

# this file: <repo>/konsol/tests/test_close_c1_setup_policies.py
REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETUP_PY = os.path.join(REPO_DIR, "scripts", "close_c1_setup.py")


def _src():
    with open(SETUP_PY) as fh:
        return fh.read()


def _main_func(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    return None


def _close_settings_var(func):
    """The name bound to ``frappe.get_single("Close Settings")`` inside
    ``func``, or None."""
    for stmt in ast.walk(func):
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Attribute)
                and stmt.value.func.attr == "get_single"
                and stmt.value.args
                and isinstance(stmt.value.args[0], ast.Constant)
                and stmt.value.args[0].value == "Close Settings"):
            return stmt.targets[0].id
    return None


def _attr_assignments(func, varname):
    """``{attr: value node}`` for every ``varname.attr = <value>`` assignment
    directly inside ``func`` (any nesting depth, since setup runs
    straight-line code with no branches around these lines). The value may be
    a literal (``"Blocked"``) or a name (``FY``); ``_literal`` resolves the
    literal case."""
    out = {}
    for stmt in ast.walk(func):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                    and target.value.id == varname):
                out[target.attr] = stmt.value
    return out


def _literal(value_node):
    """The Python value of ``value_node`` if it is a literal constant, else
    None (for example a bare ``Name`` such as ``FY``, which this test does
    not need to resolve)."""
    return value_node.value if isinstance(value_node, ast.Constant) else None


def test_module_parses_and_has_a_main_with_first_close():
    tree = ast.parse(_src())
    func = _main_func(tree)
    assert func is not None, "close_c1_setup.py must define main()"
    varname = _close_settings_var(func)
    assert varname is not None, "main() must bind frappe.get_single(\"Close Settings\") to a name"
    assigned = _attr_assignments(func, varname)
    # Anchor: the first close is declared here today. If this stops being
    # true the row's premise (declare the two policies "next to" it) is
    # wrong, and this test should fail loudly rather than silently pass.
    assert "first_close_fiscal_year" in assigned
    assert "first_close_fiscal_period" in assigned


def test_c1_setup_declares_both_close_policies():
    tree = ast.parse(_src())
    func = _main_func(tree)
    varname = _close_settings_var(func)
    assigned = _attr_assignments(func, varname)
    # This is the failure path: neither policy exists yet, so C1 sign-off is
    # refused with self_approval_undeclared / rate_move_undeclared (P05).
    assert "self_approval" in assigned, (
        "close_c1_setup.py must declare Close Settings.self_approval, or C1 "
        "sign-off is refused with self_approval_undeclared")
    assert "rate_move_threshold" in assigned, (
        "close_c1_setup.py must declare Close Settings.rate_move_threshold, "
        "or C1 sign-off is refused with rate_move_undeclared")


def test_declared_policy_values_are_not_the_undeclared_sentinels():
    tree = ast.parse(_src())
    func = _main_func(tree)
    varname = _close_settings_var(func)
    assigned = _attr_assignments(func, varname)
    # Blank self_approval and 0 rate_move_threshold both read back as
    # "undeclared" (close_policy_model.policy_gaps); a real policy must be a
    # non-blank string and a strictly positive number.
    self_approval = _literal(assigned.get("self_approval"))
    rate_move_threshold = _literal(assigned.get("rate_move_threshold"))
    assert isinstance(self_approval, str) and self_approval
    assert isinstance(rate_move_threshold, (int, float)) and rate_move_threshold > 0
    # Problems 0 (tasks.md): rate_move_threshold must be a positive number,
    # not just non-zero; and the setup uses the row's own stated value (50).
    assert self_approval == "Blocked"
    assert rate_move_threshold == 50
