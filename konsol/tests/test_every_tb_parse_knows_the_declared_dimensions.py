"""konsol#255 × konsol#305: every production parse of a trial-balance file
passes the site's declared dimensions.

parse_tb_csv refuses a dim_* header the site has not declared, and with no
second argument it treats NOTHING as declared. The submission and the bulk
load pass ``declared_dimensions()``; the close screens (konsol#305) were
written before dimensions existed and called ``parse_tb_csv(content)``. After
the two met, a file the submission accepted — and stored — with a declared
dimension column was refused when the close screen re-read it, and the
pre-submit check refused a file the submit would have accepted.
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


def _parse_calls():
    """(path:line, call node) for every production call of parse_tb_csv,
    including one through an import alias (``import parse_tb_csv as parse``)."""
    calls = []
    for path in _production_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), path)
        names = {"parse_tb_csv"} | {
            a.asname for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
            for a in n.names if a.name == "parse_tb_csv" and a.asname}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in names:
                    calls.append((f"{os.path.relpath(path, APP_DIR)}:{node.lineno}", node))
    return calls


def _parse_calls_without_dimensions():
    return [where for where, node in _parse_calls()
            if len(node.args) < 2
            and not any(k.arg == "declared_dimensions" for k in node.keywords)]


def test_every_parse_tb_csv_call_passes_the_declared_dimensions():
    bad = _parse_calls_without_dimensions()
    assert not bad, (
        "parse_tb_csv called without the declared dimensions, so a declared "
        "dim_* column is refused here and accepted by the submission: "
        + ", ".join(sorted(bad)))


def test_the_scan_finds_the_calls_it_judges():
    """A scan that finds no call at all would pass for the wrong reason. Counts
    call nodes, so the function's own ``def`` line no longer counts as one."""
    calls = _parse_calls()
    assert len(calls) >= 4, [where for where, _ in calls]
