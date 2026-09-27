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
    "Group Exchange Rate": "consolidation/doctype/group_exchange_rate/group_exchange_rate.py",
    "Trial Balance Submission": "consolidation/doctype/trial_balance_submission/trial_balance_submission.py",
    "Assertion Run": "consolidation/doctype/assertion_run/assertion_run.py",
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

#: Specific (path, enclosing function name) exceptions that plan section 1.3
#: names as staying on a fixed 12-month calendar by design (budget is
#: monthly columns, not declared periods), plus migration-only readers that
#: must keep reading the retiring doctypes until they are actually retired
#: (PR4, konsol#189). Keyed on the AST's innermost enclosing function name
#: ("<module>" for module-level code) rather than line number, so an
#: unrelated edit above an allowed offender can't silently break the gate.
#:
#: Each entry is ``(limit, reason)``: ``limit`` pins how many offenders the
#: function is allowed today. A new stale read that pushes the count past
#: ``limit`` fails the sweep instead of silently riding along with the
#: allowed ones (konsol#189 finding 6, re-review of PR #191).
_ALLOWED_FUNCS = {
    ("api.py", "build_snapshot"): (1, "budget: period_from/period_to are wide monthly columns (plan 1.3)"),
    # TODO konsol#189: fiscal_calendar.period_status_rows() is the migration
    # planner shared by the create_fiscal_years patch and declare_years_in_use
    # (plan 3.1/3.4: "nothing reads [Period Status] any more except the
    # migration patch"). It stops reading Period Status only when Period
    # Status is actually dropped in PR4/task 90.
    ("fiscal_calendar.py", "period_status_rows"): (2, "TODO konsol#189: migration planner reads Period Status until PR4 retires it"),
}


def _allowed(relpath, func_name):
    if relpath.startswith("tests" + os.sep):
        return True
    if any(relpath == p or relpath.startswith(p) for p in _ALLOWED_PATHS):
        return True
    return (relpath, func_name) in _ALLOWED_FUNCS


def _period_named(node):
    """True if the AST node's source text mentions "period" — used to tell a
    fiscal-period bound check (``fiscal_period <= 12``) apart from an
    unrelated number (a timeout, a percentage) that just happens to be 12 or
    13."""
    try:
        return "period" in ast.unparse(node).lower()
    except Exception:
        return False


def _enclosing_functions(tree):
    """Map each node's id to the name of its innermost enclosing function
    def, walking FunctionDef/AsyncFunctionDef nodes; ``"<module>"`` for
    anything at module level (or inside a class body, outside any def)."""
    owner = {}

    def visit(node, current):
        owner[id(node)] = current
        for child in ast.iter_child_nodes(node):
            child_scope = (
                child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else current
            )
            visit(child, child_scope)

    visit(tree, "<module>")
    return owner


def _stale_reads(path, source):
    """Every stale-reader offense in one source string, as
    (lineno, reason, enclosing_function_name)."""
    tree = ast.parse(source, filename=path)
    owner = _enclosing_functions(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        and arg.value in _STALE_DOCTYPES):
                    out.append((node.lineno, f'reads doctype "{arg.value}"', owner[id(node)]))
            if isinstance(node.func, ast.Name) and node.func.id == "range":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == 14:
                        out.append((node.lineno, "range(14): a fixed 0..13 calendar", owner[id(node)]))
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
                                    f"compares a period against {rhs.value}: {ast.unparse(node)}",
                                    owner[id(node)]))
    return out


def test_allow_list_survives_line_shifts():
    """The allow-list keys on (file, enclosing function), not line number: an
    allowed offender must stay allowed after lines are inserted above it in
    its own function, and must be flagged if it moves into a different
    function (konsol#189)."""
    relpath = "fiscal_calendar.py"
    allowed_func = "period_status_rows"

    def make_source(n_blank_lines, func_name):
        return (
            "def other():\n"
            "    pass\n"
            + ("\n" * n_blank_lines)
            + f"def {func_name}(bad):\n"
            + '    frappe.db.exists("Period Status", "x")\n'
        )

    for n in (0, 5, 20):
        source = make_source(n, allowed_func)
        offenders = _stale_reads(relpath, source)
        assert offenders, "expected an offender in the constructed source"
        for lineno, reason, func_name in offenders:
            assert func_name == allowed_func
            assert _allowed(relpath, func_name), (
                f"shift of {n} blank lines broke the allow-list"
            )

    source = make_source(0, "unrelated_helper")
    offenders = _stale_reads(relpath, source)
    assert offenders, "expected an offender in the constructed source"
    for lineno, reason, func_name in offenders:
        assert func_name == "unrelated_helper"
        assert not _allowed(relpath, func_name), (
            "offender moved to a new function should still be flagged"
        )


def _flag_allow_list_overflow(relpath, hits_by_func):
    """``hits_by_func`` maps an enclosing function name to the list of
    ``(lineno, reason)`` offenders found in it for ``relpath``. A function on
    the ``_ALLOWED_FUNCS`` list is only exempt up to its pinned count
    (konsol#189 finding 6): once a function has *more* offenders than that,
    the extra one(s) are reported by file, function and line, instead of
    riding along silently with the allowed ones."""
    messages = []
    for func_name, hits in hits_by_func.items():
        key = (relpath, func_name)
        if key not in _ALLOWED_FUNCS:
            continue
        limit, reason = _ALLOWED_FUNCS[key]
        if len(hits) > limit:
            extra_lineno, extra_reason = hits[limit]
            messages.append(
                f"konsol/{relpath}:{extra_lineno} {func_name}(): {len(hits)} stale "
                f"offender(s) found, allow-listed for only {limit} ({reason}); "
                f"extra offender: {extra_reason}"
            )
    return messages


def test_no_stale_period_readers():
    offenders = []
    hits_by_file_func = {}
    for dirpath, dirnames, filenames in os.walk(APP_DIR):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            relpath = os.path.relpath(full, APP_DIR)
            if relpath.startswith("tests" + os.sep):
                continue
            with open(full, encoding="utf-8") as f:
                source = f.read()
            for lineno, reason, func_name in _stale_reads(full, source):
                if (relpath, func_name) in _ALLOWED_FUNCS:
                    hits_by_file_func.setdefault(relpath, {}).setdefault(func_name, []).append(
                        (lineno, reason)
                    )
                    continue
                if _allowed(relpath, func_name):
                    continue
                offenders.append(f"konsol/{relpath}:{lineno} {reason}")
    for relpath, hits_by_func in hits_by_file_func.items():
        offenders.extend(_flag_allow_list_overflow(relpath, hits_by_func))
    assert not offenders, "stale Period Status / Fiscal Period / 0..13 reader(s):\n" + "\n".join(offenders)


def test_allow_list_flags_extra_offender_in_allowed_function():
    """An allow-listed function is only exempt up to its pinned offender
    count. One more stale read than that must be flagged (with the extra
    offender's file, function and line); exactly the pinned count must stay
    allowed (konsol#189 finding 6)."""
    relpath = "fiscal_calendar.py"
    func_name = "period_status_rows"
    limit, _reason = _ALLOWED_FUNCS[(relpath, func_name)]

    def make_source(n_offenders):
        body = "\n".join('    frappe.db.exists("Period Status", "x")' for _ in range(n_offenders))
        return f"def {func_name}():\n" + (body or "    pass") + "\n"

    def hits_for(n_offenders):
        source = make_source(n_offenders)
        offenders = _stale_reads(relpath, source)
        assert len(offenders) == n_offenders
        hits_by_func = {}
        for lineno, reason, fn in offenders:
            assert fn == func_name
            hits_by_func.setdefault(fn, []).append((lineno, reason))
        return hits_by_func

    # Exactly the pinned count: allowed.
    assert _flag_allow_list_overflow(relpath, hits_for(limit)) == []

    # One more than pinned: the extra offender is flagged.
    messages = _flag_allow_list_overflow(relpath, hits_for(limit + 1))
    assert len(messages) == 1
    assert relpath in messages[0]
    assert func_name in messages[0]


def test_no_zero_count_allow_entries():
    """An allow-list entry pinned at 0 exempts nothing: it only makes a
    function look reviewed (PR #191 re-review 2, nit 4). Every entry must
    allow at least the offenders it was added for."""
    zero = [key for key, (limit, _reason) in _ALLOWED_FUNCS.items() if limit < 1]
    assert not zero, f"allow-list entries that exempt nothing: {zero}"


# ---------------------------------------------------------------------------
# test_no_absent_means_open_copy (konsol#189 PR2 row 67)
# ---------------------------------------------------------------------------
# The SPA still had copy describing the old implied calendar (a template of
# "fourteen" periods, "1 to 12") or treating an absent close state as Open.
# The declared calendar means a period only has a state when the server says
# it is declared; the UI's words must say that, not guess it.
_CLOSE_UI_SRC = os.path.join(os.path.dirname(APP_DIR), "close-ui", "src")
_STALE_COPY_PHRASES = (
    "no period status record",
    "fourteen",
    "1 to 12",
)


def _close_ui_source_files():
    """Every non-test .js/.vue file under close-ui/src (konsol#305 R01: the
    check moved here from the deleted konsol-exec/src)."""
    out = {}
    for root, _dirs, files in os.walk(_CLOSE_UI_SRC):
        for name in files:
            if name.endswith(".test.mjs") or not name.endswith((".js", ".vue")):
                continue
            path = os.path.join(root, name)
            out[os.path.relpath(path, _CLOSE_UI_SRC)] = path
    return out


def test_no_absent_means_open_copy():
    """No close-ui source file may describe the old implied calendar, or
    fall back an absent/undeclared period status to Open."""
    files = _close_ui_source_files()
    assert files, f"no close-ui source files found under {_CLOSE_UI_SRC}"
    offenders = []
    for label, path in sorted(files.items()):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        lower = text.lower()
        for phrase in _STALE_COPY_PHRASES:
            if phrase in lower:
                offenders.append(f"{label}: contains {phrase!r}")
        if '|| "Open"' in text or "|| 'Open'" in text:
            offenders.append(f'{label}: status falls back to || "Open"')
    assert not offenders, "stale absent-means-open copy:\n" + "\n".join(offenders)
