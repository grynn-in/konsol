"""konsol#255 × konsol#305: every production parse of a trial-balance file
passes the site's declared dimensions.

parse_tb_csv refuses a dim_* header the site has not declared, and with no
second argument it treats NOTHING as declared. The submission and the bulk
load pass ``declared_dimensions()``; the close screens (konsol#305) were
written before dimensions existed and called ``parse_tb_csv(content)``. After
the two met, a file the submission accepted — and stored — with a declared
dimension column was refused when the close screen re-read it, and the
pre-submit check refused a file the submit would have accepted.

konsol#319: the parsers no longer default ``declared_dimensions``, so a call
that leaves it out fails the first time it runs instead of refusing every
dimension column quietly.
"""
import ast
import functools
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _production_files():
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in ("tests", "node_modules", "public", "__pycache__")]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


#: (file, function): each trial-balance parser. Neither may default
#: declared_dimensions (konsol#319).
PARSERS = (
    (os.path.join("consolidation", "doctype", "trial_balance_submission",
                  "trial_balance_submission.py"), "parse_tb_csv"),
    ("tb_bulk_model.py", "split_table"),
)
NAMES = frozenset(name for _, name in PARSERS)


def _signature(path, name):
    """(positional parameter names, the ones with a default, has **kwargs) of
    function ``name`` in ``path``."""
    with open(os.path.join(APP_DIR, path), encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            a = node.args
            positional = [p.arg for p in a.posonlyargs + a.args]
            defaulted = set(positional[len(positional) - len(a.defaults):]) if a.defaults else set()
            return positional, defaulted, a.kwarg is not None
    raise AssertionError(f"{name} not found in {path}")


def test_each_parser_requires_the_declared_dimensions():
    """A default of () meant a caller that left the argument out refused every
    dim_* column, and nothing said so at the call site: that is how three close
    screen calls broke (konsol#305, fixed in #315/#316). The argument must be
    the second positional parameter, required, under its own name, and not
    reachable through **kwargs, so leaving it out is a TypeError the first
    time the call runs."""
    bad = []
    for path, name in PARSERS:
        positional, defaulted, kwargs = _signature(path, name)
        if positional[1:2] != ["declared_dimensions"]:
            bad.append(f"{name}: second parameter is {positional[1:2]}, not declared_dimensions")
        elif "declared_dimensions" in defaulted:
            bad.append(f"{name}: declared_dimensions has a default")
        if kwargs:
            bad.append(f"{name}: takes **kwargs, which can hide the argument")
    assert not bad, bad


def _production_files():
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in ("tests", "node_modules", "public", "__pycache__")]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


@functools.lru_cache(maxsize=1)
def _scan():
    """One pass over production code: (calls, uncalled references).

    The signature check makes a call that leaves the argument out fail when it
    runs; this one fails CI on it before any path runs it, and catches what a
    TypeError cannot. A call is a bare-name call of a parser or of an alias it
    was imported under, or an ``x.parser(...)`` attribute call. Any other
    mention (``functools.partial(parse_tb_csv, ())``, ``map(split_table, ...)``,
    ``p = parse_tb_csv``) is an uncalled reference: its arguments cannot be
    judged here, so it is reported rather than trusted."""
    calls, uncalled = [], []
    for path in _production_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), path)
        where = os.path.relpath(path, APP_DIR)
        aliases = set(NAMES)
        called_ids = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                aliases |= {a.asname for a in node.names if a.name in NAMES and a.asname}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if (isinstance(f, ast.Name) and f.id in aliases) or (
                        isinstance(f, ast.Attribute) and f.attr in NAMES):
                    calls.append((f"{where}:{node.lineno}", node))
                    called_ids.add(id(f))
        for node in ast.walk(tree):
            named = (isinstance(node, ast.Name) and node.id in aliases) or (
                isinstance(node, ast.Attribute) and node.attr in NAMES)
            if named and id(node) not in called_ids and isinstance(node.ctx, ast.Load):
                uncalled.append(f"{where}:{node.lineno}")
    return calls, uncalled


def _dimensions_arg(node):
    if len(node.args) >= 2:
        return node.args[1]
    return next((k.value for k in node.keywords if k.arg == "declared_dimensions"), None)


def _is_empty_literal(arg):
    return isinstance(arg, (ast.Tuple, ast.List)) and not arg.elts


def test_every_parser_call_passes_the_sites_declared_dimensions():
    """Every production call passes the site's dimensions: not nothing, and
    not a literal () (which type-checks, and refuses every dim_* column all
    the same). The one () allowed is the stored read, which judges no header."""
    calls, uncalled = _scan()
    bad = list(uncalled)
    for where, node in calls:
        arg = _dimensions_arg(node)
        stored = any(k.arg == "stored" for k in node.keywords)
        if arg is None or (_is_empty_literal(arg) and not stored):
            bad.append(where)
    assert not bad, (
        "a trial-balance parser called without the site's declared dimensions, "
        "so a declared dim_* column is refused here and accepted elsewhere: "
        + ", ".join(sorted(bad)))


def test_the_scan_finds_the_calls_it_judges():
    """A scan that finds no call at all would pass for the wrong reason. Counts
    call nodes, so a function's own ``def`` line does not count as one."""
    calls, _ = _scan()
    found = {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
             for _, node in calls}
    assert found == NAMES and len(calls) >= 6, [where for where, _ in calls]


def test_only_the_stored_read_path_skips_the_header_rules():
    """stored=True reads a landed file back without judging its header. At
    intake it would bring back the silent drop konsol#255 removed, so only
    tb_read_api may pass it, and it must pass it somewhere (or this guards
    nothing)."""
    stored = set()
    for path in _production_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(k.arg == "stored" for k in node.keywords):
                stored.add(os.path.relpath(path, APP_DIR))
    assert stored == {os.path.join("close", "tb_read_api.py")}, sorted(stored)
