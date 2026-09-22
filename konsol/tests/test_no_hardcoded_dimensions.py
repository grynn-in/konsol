"""konsol#287 — shipped konsol code must not name a customer's dimensions.

``konsol/hooks.py`` records the decision of 17 September 2026: *Dimension ships
nothing*. There is no ``dimension.json`` in ``konsol/fixtures/`` or
``konsol/defaults/``, so no konsol install arrives with somebody else's chart
of dimensions. That decision was applied to what ships **as records** and never
to what is **named in code** — and fifty lines below the comment that records
it, ``konsol/clickhouse.py`` still issues a ``CREATE TABLE`` whose columns are
``dim_cost_center`` and ``dim_department``: three specific customers'
dimensions, welded into the product.

A rule nobody can enforce is not a rule. This module turns it into a build
failure.

What is a violation
-------------------
A **structural** occurrence of a ``dim_<name>`` literal: a fieldname, a column
in DDL, a dict key or a dict value that binds behaviour. Those are the places
where a customer's dimension stops being an example and starts being the
product's shape.

What is not
-----------
**Prose.** An ``e.g. dim_cost_center`` in a field description, a docstring or a
comment explains the convention to a human and harms nobody. A guard that bans
those makes field help unreadable and gets deleted by the first person it
annoys, taking the real rule with it. So prose stays.

The discriminator, per file type:

``.py``
    The file is parsed. Only **string literals** count, and only those that are
    not statements in their own right — a bare string expression is a
    docstring (module, class, function or attribute) and is prose. Comments
    never reach the AST at all, so they are prose for free. Bare identifiers
    are *never* violations: ``dim_names``, ``dim_valid``, ``dim_headers`` are
    program machinery that names no dimension, and a customer's dimension only
    becomes behaviour when it is quoted.

``.json``
    A string sitting under a prose key (``description`` and friends) is prose;
    a string anywhere else — a ``fieldname``, an ``options``, a dict key
    itself — is structural. This is the obvious discriminator for a Frappe
    doctype: ``"fieldname": "dim_cost_center"`` builds a column,
    ``"description": "e.g. dim_cost_center"`` builds a sentence.

everything else (``.js``, ``.html``, ``.css``, ``.xml``, ``.txt``)
    No parser, so: comments are stripped and anything left is structural.
    These files are not written in Python, so a stray ``dim_foo`` in them is a
    data key, not a local variable.

The allow-list is the point
---------------------------
Every violation that exists today is listed in ``ALLOWED_DIMENSION_LITERALS``
or ``ALLOWED_DIMENSION_PATHS`` below, with an exact count and the issue that
will remove it. Shrinking that list **is** the migration. Nothing can be added
to it without somebody editing this file and writing down why, and nothing can
be quietly re-permitted either: an entry whose violation has since been fixed
is reported as **stale** and fails the run, so the list cannot rot into a
licence for the next person.
"""
import ast
import collections
import json
import os
import re
import unittest

#: Repo root — this file is konsol/tests/test_no_hardcoded_dimensions.py.
#: realpath, not abspath: /tmp is a symlink to /private/tmp on macOS and an
#: unresolved root makes every relpath() below walk out of the tree.
REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)

#: The shipped app. Only what installs on a customer's site is in scope.
SHIPPED_ROOT = "konsol"

#: A dimension column name. Requires at least one character after the prefix,
#: so the bare prefix ``"dim_"``, the LIKE pattern ``"dim_%"`` and the regex
#: source ``r"^dim_[a-z0-9_]+$"`` — all of which are the machinery that makes
#: dimensions configurable — are not matches. Those are the *cure*, not the
#: disease.
DIMENSION_LITERAL = re.compile(r"\bdim_[a-z][a-z0-9_]*\b")

#: File suffixes that are source. Anything else (images, fonts) cannot hide a
#: fieldname.
SCANNED_SUFFIXES = (".py", ".json", ".js", ".html", ".css", ".xml", ".txt")

#: JSON keys whose value is prose written for a human.
PROSE_JSON_KEYS = frozenset(
    {"description", "documentation", "help", "hint", "comment", "_comment", "doc"}
)

#: Directories that are never scanned.
EXCLUDED_DIR_NAMES = frozenset({"__pycache__", "node_modules", ".git", "dist"})

#: Paths excluded from the scan, repo-relative, and why.
EXCLUDED_PATHS = {
    # Frozen by standing instruction and being dumped and redesigned; adding a
    # rule to code nobody may edit would be a rule that can only ever fail.
    "konsol/d365_writeback.py": "frozen; being dumped, not fixed",
    # Tests name example dimensions on purpose — that is how they test.
    "konsol/tests": "tests use example dimension names as data",
}

#: The dimension roots that belong to a specific customer rather than to the
#: product. Used for the *path* scan: a file or directory named after one of
#: these is the same violation as a literal, just spelled in the filesystem.
#: This list shrinks with the allow-list below.
CUSTOMER_DIMENSION_ROOTS = ("cost_center", "cost_centre", "department", "business_unit")


# --------------------------------------------------------------------------
# The debt, enumerated. Each entry: (path, literal) -> (count, why it is here).
#
# Adding a line here is a deliberate act. It says "konsol still names this
# customer's dimension, and here is the issue that will stop it". If you are
# here because the guard failed on code you just wrote, the answer is almost
# never to add a line — it is to take the dimension name out of your code and
# read it from Dimension.
# --------------------------------------------------------------------------
ALLOWED_DIMENSION_LITERALS = {}

#: Shipped paths named after a customer's dimension. Same contract.
ALLOWED_DIMENSION_PATHS = {}


# --------------------------------------------------------------------------
# Scanning. Every function here is pure over (relpath, text) so that the tests
# of the guard itself can run it on synthetic content and never on the tree.
# --------------------------------------------------------------------------
def _is_excluded(relpath):
    """True when `relpath` is, or is inside, a declared exclusion."""
    for excluded in EXCLUDED_PATHS:
        if relpath == excluded or relpath.startswith(excluded + "/"):
            return True
    return False


def shipped_files():
    """Every scannable source file that installs on a customer's site."""
    found = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, SHIPPED_ROOT)):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIR_NAMES)
        for filename in sorted(filenames):
            if not filename.endswith(SCANNED_SUFFIXES):
                continue
            relpath = os.path.relpath(os.path.join(dirpath, filename), REPO_ROOT)
            if not _is_excluded(relpath):
                found.append(relpath)
    return found


def _python_structural_strings(source):
    """String literals in `source` that bind behaviour rather than explain it.

    A string that is a statement on its own is a docstring — module, class,
    function, or the attribute docstring convention — and is prose. Comments
    are not in the AST, so they never arrive here.
    """
    tree = ast.parse(source)
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
    ]


def _json_structural_strings(source):
    """Strings in a JSON document that are not prose written for a human."""
    out = []

    def walk(node, key):
        if isinstance(node, dict):
            for k, v in node.items():
                out.append(k)  # a key is always structural
                walk(v, k)
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif isinstance(node, str) and key not in PROSE_JSON_KEYS:
            out.append(node)

    walk(json.loads(source), None)
    return out


#: Comment syntaxes stripped from files we have no parser for.
_COMMENT_PATTERNS = (
    re.compile(r"/\*.*?\*/", re.DOTALL),   # /* ... */
    re.compile(r"<!--.*?-->", re.DOTALL),  # <!-- ... -->
    re.compile(r"//[^\n]*"),               # // to end of line
    re.compile(r"#[^\n]*"),                # # to end of line
)


def _text_structural_strings(source):
    """Everything left in an unparsed file once its comments are gone."""
    for pattern in _COMMENT_PATTERNS:
        source = pattern.sub(" ", source)
    return [source]


def scan_source(relpath, source):
    """Structural ``dim_<name>`` occurrences in one file, as a Counter.

    Keyed by ``(relpath, literal)`` — not by line number, which would make the
    allow-list churn on every unrelated edit above it.
    """
    if relpath.endswith(".py"):
        strings = _python_structural_strings(source)
    elif relpath.endswith(".json"):
        strings = _json_structural_strings(source)
    else:
        strings = _text_structural_strings(source)

    found = collections.Counter()
    for text in strings:
        for literal in DIMENSION_LITERAL.findall(text):
            found[(relpath, literal)] += 1
    return found


def scan_tree():
    """Structural occurrences across everything konsol ships."""
    found = collections.Counter()
    for relpath in shipped_files():
        with open(os.path.join(REPO_ROOT, relpath), encoding="utf-8") as fh:
            source = fh.read()
        found.update(scan_source(relpath, source))
    return found


def scan_paths():
    """Shipped files and directories named after a customer's dimension.

    Reported at the shallowest offending path, so a doctype directory is one
    finding rather than one per file inside it.
    """
    found = set()
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, SHIPPED_ROOT)):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIR_NAMES)
        for name in sorted(dirnames) + sorted(filenames):
            relpath = os.path.relpath(os.path.join(dirpath, name), REPO_ROOT)
            if _is_excluded(relpath):
                continue
            if any(root in name for root in CUSTOMER_DIMENSION_ROOTS):
                if not any(relpath.startswith(seen + "/") for seen in found):
                    found.add(relpath)
    return found


def unallowed_literals(found, allowed):
    """Occurrences the allow-list does not cover, as (path, literal, n, allowed_n)."""
    out = []
    for (relpath, literal), count in sorted(found.items()):
        permitted = allowed.get((relpath, literal), (0, ""))[0]
        if count > permitted:
            out.append((relpath, literal, count, permitted))
    return out


def stale_literals(found, allowed):
    """Allow-list entries the tree no longer justifies, as (path, literal, was, now).

    An entry that over-states the debt is as dangerous as a missing one: it
    silently re-permits the violation somebody already paid to remove.
    """
    out = []
    for (relpath, literal), (count, _why) in sorted(allowed.items()):
        actual = found.get((relpath, literal), 0)
        if actual < count:
            out.append((relpath, literal, count, actual))
    return out


def _literal_report(rows):
    lines = []
    for relpath, literal, count, permitted in rows:
        lines.append(f"  {relpath}: {literal} x{count} (allow-list permits {permitted})")
    return "\n".join(lines)


class TestShippedCodeNamesNoCustomerDimensions(unittest.TestCase):
    """The real tree, checked against the allow-list."""

    def test_no_unallowed_dimension_literals(self):
        found = scan_tree()
        rows = unallowed_literals(found, ALLOWED_DIMENSION_LITERALS)
        self.assertEqual(
            rows,
            [],
            "konsol#287: shipped konsol names a customer's dimension where it "
            "binds behaviour (a fieldname, a DDL column, a dict key or value).\n"
            "Dimension ships nothing (hooks.py, 17 Sep 2026) — the name has to "
            "come from the site's own Dimension records, not from this code.\n\n"
            + _literal_report(rows)
            + "\n\nIf this really cannot be fixed now, add it to "
            "ALLOWED_DIMENSION_LITERALS in this file with the issue number that "
            "will remove it. That is a deliberate act and it is reviewed.\n"
            "If the occurrence is prose — an 'e.g.' in a description or a "
            "docstring — it should not be reaching this test at all; fix the "
            "discriminator, not the prose.",
        )

    def test_allow_list_is_not_stale(self):
        found = scan_tree()
        rows = stale_literals(found, ALLOWED_DIMENSION_LITERALS)
        self.assertEqual(
            rows,
            [],
            "konsol#287: the allow-list claims more debt than the tree has. "
            "Somebody removed a hardcoded dimension and left its licence "
            "behind, which would let the next one back in unnoticed. Lower the "
            "count, or delete the entry:\n"
            + "\n".join(
                f"  {relpath}: {literal} listed x{was}, found x{now}"
                for relpath, literal, was, now in rows
            ),
        )

    def test_no_unallowed_dimension_paths(self):
        rows = sorted(scan_paths() - set(ALLOWED_DIMENSION_PATHS))
        self.assertEqual(
            rows,
            [],
            "konsol#287: konsol ships a file or directory named after one "
            "customer's dimension:\n  " + "\n  ".join(rows),
        )

    def test_allow_list_paths_are_not_stale(self):
        rows = sorted(set(ALLOWED_DIMENSION_PATHS) - scan_paths())
        self.assertEqual(
            rows,
            [],
            "konsol#287: these paths are allow-listed but no longer exist. "
            "Delete the entries:\n  " + "\n  ".join(rows),
        )


class TestGuardCatchesStructuralUse(unittest.TestCase):
    """The guard itself, on synthetic content. Proves it can fail."""

    def test_python_dict_key_is_caught(self):
        source = 'CH_FIELD_MAP = {"dim_cost_center": "dim_cost_center"}\n'
        self.assertEqual(
            scan_source("konsol/thing.py", source),
            collections.Counter({("konsol/thing.py", "dim_cost_center"): 2}),
        )

    def test_python_ddl_string_is_caught(self):
        source = 'DDL = "main_account String, dim_department String"\n'
        self.assertEqual(
            scan_source("konsol/thing.py", source)[("konsol/thing.py", "dim_department")],
            1,
        )

    def test_python_fstring_is_caught(self):
        source = 'sql = f"SELECT dim_business_unit FROM {table}"\n'
        self.assertEqual(
            scan_source("konsol/thing.py", source)[
                ("konsol/thing.py", "dim_business_unit")
            ],
            1,
        )

    def test_json_fieldname_is_caught(self):
        source = '{"fields": [{"fieldname": "dim_cost_center", "fieldtype": "Data"}]}'
        self.assertEqual(
            scan_source("konsol/thing.json", source)[
                ("konsol/thing.json", "dim_cost_center")
            ],
            1,
        )

    def test_json_key_is_caught(self):
        source = '{"dim_department": {"fieldtype": "Data"}}'
        self.assertEqual(
            scan_source("konsol/thing.json", source)[
                ("konsol/thing.json", "dim_department")
            ],
            1,
        )

    def test_js_data_key_is_caught(self):
        source = "if (costCenter) data.dim_cost_center = String(costCenter);\n"
        self.assertEqual(
            scan_source("konsol/thing.js", source)[("konsol/thing.js", "dim_cost_center")],
            1,
        )


class TestGuardIgnoresProse(unittest.TestCase):
    """The other direction. A guard that eats documentation gets deleted."""

    def test_module_docstring_is_not_caught(self):
        source = '"""Only a dim_cost_center column is accepted."""\nX = 1\n'
        self.assertEqual(scan_source("konsol/thing.py", source), collections.Counter())

    def test_function_docstring_is_not_caught(self):
        source = 'def f():\n    """e.g. dimensions={"dim_cost_center": "CC001"}."""\n    return 1\n'
        self.assertEqual(scan_source("konsol/thing.py", source), collections.Counter())

    def test_attribute_docstring_is_not_caught(self):
        source = 'X = 1\n"""The dim_department column, when declared."""\n'
        self.assertEqual(scan_source("konsol/thing.py", source), collections.Counter())

    def test_python_comment_is_not_caught(self):
        source = "# The D365 name for cost center == dim_cost_center's own.\nX = 1\n"
        self.assertEqual(scan_source("konsol/thing.py", source), collections.Counter())

    def test_json_description_is_not_caught(self):
        source = '{"fieldname": "axis", "description": "e.g. dim_business_unit"}'
        self.assertEqual(scan_source("konsol/thing.json", source), collections.Counter())

    def test_js_comment_is_not_caught(self):
        source = "// e.g. dim_cost_center is sent as a data key\nvar x = 1;\n"
        self.assertEqual(scan_source("konsol/thing.js", source), collections.Counter())

    def test_generic_machinery_identifiers_are_not_caught(self):
        """dim_names, dim_valid, dim_headers name no dimension — they are the
        code that makes dimensions configurable, which is the goal, not the
        debt."""
        source = (
            "def f(dim_names):\n"
            "    dim_valid = True\n"
            "    dim_headers = sorted(dim_names)\n"
            "    dim_types = {}\n"
            "    for dim_name in dim_headers:\n"
            "        dim_types[dim_name] = 'string'\n"
            "    return dim_valid, dim_types\n"
        )
        self.assertEqual(scan_source("konsol/thing.py", source), collections.Counter())

    def test_bare_prefix_and_patterns_are_not_caught(self):
        """The configurable machinery quotes 'dim_', 'dim_%' and a regex — none
        of which names anybody's dimension."""
        source = (
            'PREFIX = "dim_"\n'
            'SAFE = re.compile(r"^dim_[a-z0-9_]+$")\n'
            'FILTER = {"fieldname": ("like", "dim_%")}\n'
        )
        self.assertEqual(scan_source("konsol/thing.py", source), collections.Counter())


class TestAllowListCannotRot(unittest.TestCase):
    """Synthetic allow-lists, so these tests cannot pass by accident when the
    real list happens to be right."""

    def test_new_violation_beyond_the_allowance_is_reported(self):
        allowed = {("konsol/thing.py", "dim_cost_center"): (1, "konsol#287")}
        found = collections.Counter({("konsol/thing.py", "dim_cost_center"): 2})
        self.assertEqual(
            unallowed_literals(found, allowed),
            [("konsol/thing.py", "dim_cost_center", 2, 1)],
        )

    def test_violation_in_a_new_file_is_reported(self):
        allowed = {("konsol/thing.py", "dim_cost_center"): (1, "konsol#287")}
        found = collections.Counter({("konsol/other.py", "dim_cost_center"): 1})
        self.assertEqual(
            unallowed_literals(found, allowed),
            [("konsol/other.py", "dim_cost_center", 1, 0)],
        )

    def test_allowed_violation_at_its_exact_count_passes(self):
        allowed = {("konsol/thing.py", "dim_cost_center"): (2, "konsol#287")}
        found = collections.Counter({("konsol/thing.py", "dim_cost_center"): 2})
        self.assertEqual(unallowed_literals(found, allowed), [])
        self.assertEqual(stale_literals(found, allowed), [])

    def test_fixed_site_still_on_the_list_is_reported_as_stale(self):
        """The one that matters: somebody removed the hardcoded name, the
        licence stayed, and the next person could put it back for free."""
        allowed = {("konsol/thing.py", "dim_cost_center"): (2, "konsol#287")}
        found = collections.Counter()
        self.assertEqual(
            stale_literals(found, allowed),
            [("konsol/thing.py", "dim_cost_center", 2, 0)],
        )

    def test_partly_fixed_site_is_reported_as_stale(self):
        allowed = {("konsol/thing.py", "dim_cost_center"): (2, "konsol#287")}
        found = collections.Counter({("konsol/thing.py", "dim_cost_center"): 1})
        self.assertEqual(
            stale_literals(found, allowed),
            [("konsol/thing.py", "dim_cost_center", 2, 1)],
        )


class TestAllowListIsDocumented(unittest.TestCase):
    """The list is only useful if every line says what removes it."""

    def test_every_literal_entry_cites_an_issue(self):
        for key, (count, why) in sorted(ALLOWED_DIMENSION_LITERALS.items()):
            self.assertGreater(count, 0, f"{key}: an allowance of zero is not an allowance")
            self.assertIn("konsol#", why, f"{key}: no issue cited for this allowance")

    def test_every_path_entry_cites_an_issue(self):
        for path, why in sorted(ALLOWED_DIMENSION_PATHS.items()):
            self.assertIn("konsol#", why, f"{path}: no issue cited for this allowance")

    def test_scan_actually_reaches_the_tree(self):
        """A scanner that reads nothing passes everything (verify, don't
        predict): the allow-list above is only evidence if these files exist."""
        files = shipped_files()
        self.assertGreater(len(files), 100, "the scan found almost no shipped files")
        self.assertIn("konsol/clickhouse.py", files)
        self.assertNotIn("konsol/d365_writeback.py", files)
        self.assertFalse([f for f in files if f.startswith("konsol/tests/")])


if __name__ == "__main__":
    unittest.main()
