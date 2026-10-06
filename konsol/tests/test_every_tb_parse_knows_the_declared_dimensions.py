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
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _production_files():
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in ("tests", "node_modules", "public", "__pycache__")]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


#: (file, function): each trial-balance parser, which must not default
#: declared_dimensions (konsol#319).
PARSERS = (
    (os.path.join("consolidation", "doctype", "trial_balance_submission",
                  "trial_balance_submission.py"), "parse_tb_csv"),
    ("tb_bulk_model.py", "split_table"),
)


def _defaulted_params(path, name):
    """The parameters of function ``name`` in ``path`` that have a default."""
    with open(os.path.join(APP_DIR, path), encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            a = node.args
            positional = a.posonlyargs + a.args
            out = {p.arg for p in positional[len(positional) - len(a.defaults):]}
            return out | {p.arg for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None}
    raise AssertionError(f"{name} not found in {path}")


def test_no_parser_defaults_the_declared_dimensions():
    """A default of () meant a caller that left the argument out refused every
    dim_* column, and nothing said so at the call site: that is how three close
    screen calls broke (konsol#305, fixed in #315/#316). With no default, a
    call that leaves it out is a TypeError the first time it runs, and a caller
    that means "no dimensions" writes () where a reviewer can see it."""
    defaulted = {f"{path}:{name}" for path, name in PARSERS
                 if "declared_dimensions" in _defaulted_params(path, name)}
    assert not defaulted, sorted(defaulted)


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
