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
Python comment explains the convention to a human and harms nobody. A guard
that bans those makes field help unreadable and gets deleted by the first
person it annoys, taking the real rule with it. So prose stays — for free
where the file is parsed, and with a marker that says so where it is not.

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

everything else (``.js``, ``.css``, ``.html``, ``.xml``, ``.txt``, ``.md``, …)
    No parser, so **every** character counts. A stray ``dim_foo`` in one of
    these is a data key, not a local variable, and there is nowhere in them it
    can hide.

    This used to strip "comments" first, with one regex for every language:
    ``/*…*/``, ``<!--…-->``, ``//…`` and ``#…``. But ``#`` is not a comment in
    JS, CSS, HTML or XML — it is a hex colour and an id selector — and ``//``
    is in every URL. Over the real tree that blanked 78% of the scannable text
    (96.4% of ``konsol_exec.css``, 80.6% of ``konsol_exec.js``), so the rule
    was really *"a hardcoded dimension is a build failure unless you put a hex
    colour, an id selector or a URL earlier on the line"*. Two one-line
    payloads, both measured, walked straight through it.

    A per-language comment grammar would fix that particular hole and leave
    the same *shape* of hole behind: blanking text is how a guard goes blind,
    and each new suffix would need a new grammar nobody would write. So
    nothing is blanked. A comment that genuinely needs to name a dimension
    says so out loud — see below.

The escape hatch, for prose the discriminator cannot see
--------------------------------------------------------
The rules above keep a Python docstring, a Python comment and a JSON
``description`` out of the scan for free. They cannot see that
``frappe.msgprint("Add a column such as dim_cost_center")`` is a sentence, or
that a ``.html`` help page, a shipped release note or a ``//`` comment in
JavaScript is addressed to a human. Without a way to say so, the guard shapes
the product: ``dimension.py`` already omits the most useful thing its error
message could say — a concrete example name — because of this test.

So: a line carrying the marker ``konsol#287-prose`` has its ``dim_<name>``
occurrences read as prose. The marker is deliberately ugly and greppable::

    grep -rn 'konsol#287-prose' konsol/

and it is not enough on its own. Every escaped occurrence must also appear in
``DECLARED_PROSE_MENTIONS`` below with a count and a reason, checked in both
directions exactly like the debt allow-list: an undeclared escape fails the
run, and an escape whose prose has since been deleted is reported as stale. An
escape is visible at the site *and* counted here. It is not a way to be quiet.

What this guard cannot see, and says so
---------------------------------------
A name assembled at runtime — ``"dim_" + name``, ``"dim_%s" % name``,
``f"dim_{name}"``, ``"_".join(...)`` — is invisible to any text scanner, and
those spellings are also how the *cure* is written, so they cannot simply be
banned. Only the fully static form ``"dim_" + "cost_center"`` is decidable,
and it is folded and caught. The rest is a declared blind spot with a test
naming it, not an oversight: see
``test_dynamic_name_construction_is_a_declared_blind_spot``.

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
#:
#: The character after the prefix is ``[a-z0-9]``, not ``[a-z]``: the column
#: rule the product actually enforces is ``^dim_[a-z0-9_]+$``, so
#: ``dim_2024_region`` is a legal dimension for a customer to declare. A guard
#: that only looked for a letter would have been blind to every dimension
#: whose name starts with a digit.
DIMENSION_LITERAL = re.compile(r"\bdim_[a-z0-9][a-z0-9_]*\b")

#: File suffixes that are source. Nothing is parsed but ``.py`` and ``.json``,
#: so a suffix costs nothing to add and a missing one is silent — which is why
#: ``test_no_shipped_text_suffix_escapes_the_scan`` below fails the build when
#: konsol starts shipping a text type that is in neither this tuple nor
#: ``NON_SOURCE_SUFFIXES``. The list is wider than the tree needs today on
#: purpose: the first shipped ``.sql`` or ``.yml`` is scanned on arrival.
SCANNED_SUFFIXES = (
    ".py",
    ".json",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".vue",
    ".html",
    ".css",
    ".scss",
    ".less",
    ".xml",
    ".svg",
    ".txt",
    ".md",
    ".rst",
    ".sql",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".tsv",
    ".sh",
)

#: Suffixes that are assets, not source: bytes that cannot carry a fieldname a
#: human wrote. Declared rather than assumed, so that the completeness test can
#: tell "binary" from "nobody thought about it".
NON_SOURCE_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".avif",
        ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".pdf", ".zip", ".gz", ".xlsx", ".mp4", ".webm",
    }
)

#: The inline marker that reads a line's dimension names as prose. Ugly and
#: greppable on purpose — ``grep -rn 'konsol#287-prose' konsol/`` is the whole
#: audit. Never enough on its own: see ``DECLARED_PROSE_MENTIONS``.
PROSE_ESCAPE = "konsol#287-prose"

#: What an escaped name is replaced with before scanning. Must not itself look
#: like a dimension, and must stay a valid identifier so that redacting a line
#: of Python or JSON leaves it parseable.
_PROSE_PLACEHOLDER = "prose_mention"

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
#: Counts are exact on purpose. "One of these two got fixed" has to be visible,
#: and so does a fourteenth occurrence appearing in a file that already had
#: thirteen — otherwise the debt regrows inside its own licence.
_BUDGET_TABLES = (
    "konsol#287 — the epm_gold budget DDL hardcodes two customers' dimensions "
    "as columns. Removed when the budget tables take their dimension columns "
    "from the site's Dimension records, the way schema_apply.py already does "
    "for the raw trial balance."
)
_BUDGET_ANNUAL_INPUT = (
    "konsol#287 — Budget Annual Input has a fixed two-dimension grain: two "
    "fieldnames in the doctype, the ClickHouse field map and the "
    "unique-grain check. Removed with konsol#287, together with the DDL "
    "those fields write into."
)
_SOURCE_COLUMN_PATCH = (
    "konsol#287 — a one-shot data migration that names all three dimensions "
    "to realign Dimension.source_column. It is a patch, so it describes a "
    "moment in a site's history rather than the product's shape; it goes when "
    "the patch is retired, not before."
)
_EXCEL_ADDIN = (
    "konsol#287 — the Excel add-in's budget write takes cost centre and "
    "department as fixed positional arguments and sends them as fixed data "
    "keys, so a customer with different dimensions cannot use the spreadsheet "
    "at all. Removed when the add-in reads the declared dimensions instead."
)
_LEGACY_REQUEST_KEYS = (
    "konsol#287 — _LEGACY_DIM_MAP translates two pre-Dimension request keys "
    "('cost_center', 'department') to column names. Removed when the last "
    "caller of the legacy keys is gone; the map is the compatibility shim, "
    "not the model."
)

ALLOWED_DIMENSION_LITERALS = {
    ("konsol/clickhouse.py", "dim_cost_center"): (2, _BUDGET_TABLES),
    ("konsol/clickhouse.py", "dim_department"): (2, _BUDGET_TABLES),
    ("konsol/api.py", "dim_cost_center"): (1, _LEGACY_REQUEST_KEYS),
    ("konsol/api.py", "dim_department"): (1, _LEGACY_REQUEST_KEYS),
    (
        "konsol/epm/doctype/budget_annual_input/budget_annual_input.json",
        "dim_cost_center",
    ): (1, _BUDGET_ANNUAL_INPUT),
    (
        "konsol/epm/doctype/budget_annual_input/budget_annual_input.json",
        "dim_department",
    ): (1, _BUDGET_ANNUAL_INPUT),
    (
        "konsol/epm/doctype/budget_annual_input/budget_annual_input.py",
        "dim_cost_center",
    ): (3, _BUDGET_ANNUAL_INPUT),
    (
        "konsol/epm/doctype/budget_annual_input/budget_annual_input.py",
        "dim_department",
    ): (3, _BUDGET_ANNUAL_INPUT),
    ("konsol/patches/fix_dimension_source_column_drift.py", "dim_cost_center"): (
        2,
        _SOURCE_COLUMN_PATCH,
    ),
    ("konsol/patches/fix_dimension_source_column_drift.py", "dim_department"): (
        2,
        _SOURCE_COLUMN_PATCH,
    ),
    ("konsol/patches/fix_dimension_source_column_drift.py", "dim_business_unit"): (
        2,
        _SOURCE_COLUMN_PATCH,
    ),
    ("konsol/public/excel-addin/functions.js", "dim_cost_center"): (1, _EXCEL_ADDIN),
    ("konsol/public/excel-addin/functions.js", "dim_department"): (1, _EXCEL_ADDIN),
}

# --------------------------------------------------------------------------
# The prose escapes, enumerated. Each entry: (path, literal) -> (count, why).
#
# Not debt — a sentence addressed to a human harms nobody, and banning those is
# how a guard gets deleted. But an escape that nobody counted is a hole, so the
# contract is the allow-list's: exact counts, a reason, checked in both
# directions. An undeclared ``konsol#287-prose`` fails the run; a declaration
# whose prose has since been deleted is reported as stale.
#
# Empty today: no shipped file has needed one yet. The mechanism exists so that
# a message like "Add a column such as dim_cost_center" can be written at all —
# konsol/dimension.py currently omits that example *because of this test*.
# --------------------------------------------------------------------------
DECLARED_PROSE_MENTIONS = {}

#: Shipped paths named after a customer's dimension. Same contract.
ALLOWED_DIMENSION_PATHS = {
    "konsol/epm/doctype/budget_cost_center": (
        "konsol#287 — a whole doctype named after one customer's dimension. "
        "Removed when budget dimension members are Dimension records rather "
        "than a doctype per dimension."
    ),
}


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


def _static_concat(node):
    """The value of `node` if it is a tree of added string literals, else None.

    ``"dim_" + "cost_center"`` is the one runtime-assembled name a text scanner
    can decide, because nothing about it is runtime. Implicit adjacent
    concatenation (``"dim_" "cost_center"``) never reaches here: the parser
    folds it into a single Constant, so it is caught as an ordinary literal.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_concat(node.left)
        right = _static_concat(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _python_structural_strings(source):
    """String literals in `source` that bind behaviour rather than explain it.

    A string that is a statement on its own is a docstring — module, class,
    function, or the attribute docstring convention — and is prose. Comments
    are not in the AST, so they never arrive here.

    Statically concatenated literals are folded and reported once, as the name
    they spell; the pieces they were spelled with are then not reported again.
    """
    tree = ast.parse(source)
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }

    folded = []
    consumed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and id(node) not in consumed:
            value = _static_concat(node)
            if value is not None:
                folded.append(value)
                for part in ast.walk(node):
                    consumed.add(id(part))

    return folded + [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
        and id(node) not in consumed
    ]


def _json_structural_strings(source):
    """Strings in a JSON document that are not prose written for a human.

    A prose key covers its own string value and nothing else. It is not
    inherited by the members of a list or an object underneath it:
    ``{"description": ["dim_cost_center"]}`` is a list of strings that happens
    to sit under a prose name, and there is no reason to believe a human wrote
    it as a sentence.
    """
    out = []

    def walk(node, key):
        if isinstance(node, dict):
            for k, v in node.items():
                out.append(k)  # a key is always structural
                walk(v, k)
        elif isinstance(node, list):
            for item in node:
                walk(item, None)
        elif isinstance(node, str) and key not in PROSE_JSON_KEYS:
            out.append(node)

    walk(json.loads(source), None)
    return out


def _text_structural_strings(source):
    """An unparsed file, whole.

    Nothing is stripped. There is no comment syntax shared by JS, CSS, HTML,
    XML, Markdown and plain text, and the guard that pretended there was went
    blind on 78% of the tree — a ``#`` is a hex colour, a ``//`` is a URL. A
    comment that genuinely needs to name a dimension carries ``PROSE_ESCAPE``.
    """
    return [source]


def redact_prose_escapes(source):
    """Split `source` into (text to scan, Counter of names read as prose).

    A line carrying ``PROSE_ESCAPE`` has its dimension names replaced with a
    placeholder — a real identifier, so the line stays parseable as Python or
    JSON — and those names are returned instead, to be declared and counted.

    The marker covers its whole line, so a structural use sharing a line with
    a marked sentence would be hidden too. That is not a loophole in the dark:
    putting the marker there is a deliberate edit, and the occurrence it hides
    still has to be declared in ``DECLARED_PROSE_MENTIONS`` with a reason.
    """
    escaped = collections.Counter()
    lines = []
    for line in source.split("\n"):
        if PROSE_ESCAPE in line:
            escaped.update(DIMENSION_LITERAL.findall(line))
            line = DIMENSION_LITERAL.sub(_PROSE_PLACEHOLDER, line)
        lines.append(line)
    return "\n".join(lines), escaped


def scan_source(relpath, source):
    """Structural ``dim_<name>`` occurrences in one file, as a Counter.

    Keyed by ``(relpath, literal)`` — not by line number, which would make the
    allow-list churn on every unrelated edit above it.
    """
    source, _escaped = redact_prose_escapes(source)

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


def scan_escapes(relpath, source):
    """The occurrences ``PROSE_ESCAPE`` hid in one file, as a Counter."""
    _source, escaped = redact_prose_escapes(source)
    return collections.Counter(
        {(relpath, literal): count for literal, count in escaped.items()}
    )


def scan_tree():
    """Structural occurrences across everything konsol ships."""
    found = collections.Counter()
    for relpath in shipped_files():
        with open(os.path.join(REPO_ROOT, relpath), encoding="utf-8") as fh:
            source = fh.read()
        found.update(scan_source(relpath, source))
    return found


def scan_escapes_tree():
    """Every occurrence ``PROSE_ESCAPE`` hides, across everything konsol ships."""
    found = collections.Counter()
    for relpath in shipped_files():
        with open(os.path.join(REPO_ROOT, relpath), encoding="utf-8") as fh:
            source = fh.read()
        found.update(scan_escapes(relpath, source))
    return found


def shipped_suffixes():
    """Every file suffix konsol ships, excluding the declared exclusions."""
    found = set()
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, SHIPPED_ROOT)):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIR_NAMES)
        for filename in sorted(filenames):
            relpath = os.path.relpath(os.path.join(dirpath, filename), REPO_ROOT)
            if not _is_excluded(relpath):
                found.add(os.path.splitext(filename)[1].lower())
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


# --------------------------------------------------------------------------
# The tests. Plain module-level `test_*` functions taking no arguments — that
# is what scripts/run-host-tests.py collects (`for name in dir(module)`). A
# unittest.TestCase here would pass under `python -m unittest` and be invisible
# to the project's own runner: the file would be counted among the files and
# contribute zero tests, so this guard could never fail the build it exists to
# fail.
# --------------------------------------------------------------------------

# --- the real tree, against the allow-list --------------------------------

def test_shipped_code_has_no_unallowed_dimension_literals():
    rows = unallowed_literals(scan_tree(), ALLOWED_DIMENSION_LITERALS)
    assert rows == [], (
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
        "discriminator, not the prose."
    )


def test_literal_allow_list_is_not_stale():
    rows = stale_literals(scan_tree(), ALLOWED_DIMENSION_LITERALS)
    assert rows == [], (
        "konsol#287: the allow-list claims more debt than the tree has. "
        "Somebody removed a hardcoded dimension and left its licence behind, "
        "which would let the next one back in unnoticed. Lower the count, or "
        "delete the entry:\n"
        + "\n".join(
            f"  {relpath}: {literal} listed x{was}, found x{now}"
            for relpath, literal, was, now in rows
        )
    )


def test_every_prose_escape_in_the_tree_is_declared():
    rows = unallowed_literals(scan_escapes_tree(), DECLARED_PROSE_MENTIONS)
    assert rows == [], (
        "konsol#287: a shipped line carries the " + PROSE_ESCAPE + " marker "
        "for a dimension name nobody declared. The marker makes the escape "
        "visible at the site; DECLARED_PROSE_MENTIONS is what counts it. Add "
        "the entry with the reason the mention is a sentence and not a "
        "fieldname — or take the marker off and fix the code:\n"
        + _literal_report(rows)
    )


def test_prose_escape_declarations_are_not_stale():
    rows = stale_literals(scan_escapes_tree(), DECLARED_PROSE_MENTIONS)
    assert rows == [], (
        "konsol#287: a declared prose escape no longer exists in the tree. An "
        "escape left on the list is a licence for the next person to be quiet "
        "with. Lower the count, or delete the entry:\n"
        + "\n".join(
            f"  {relpath}: {literal} declared x{was}, found x{now}"
            for relpath, literal, was, now in rows
        )
    )


def test_no_shipped_text_suffix_escapes_the_scan():
    """The silent hole: a suffix in neither list is simply never opened. A
    first ``.sql`` or ``.yml`` arriving should fail the build, not vanish."""
    unclassified = sorted(
        shipped_suffixes() - set(SCANNED_SUFFIXES) - NON_SOURCE_SUFFIXES
    )
    assert unclassified == [], (
        "konsol#287: konsol now ships file types this guard neither scans nor "
        "declares to be assets, so a hardcoded dimension in one of them would "
        "be invisible. Add each to SCANNED_SUFFIXES, or to NON_SOURCE_SUFFIXES "
        "if it is bytes a human never wrote a fieldname into:\n  "
        + "\n  ".join(unclassified)
    )


def test_shipped_code_has_no_unallowed_dimension_paths():
    rows = sorted(scan_paths() - set(ALLOWED_DIMENSION_PATHS))
    assert rows == [], (
        "konsol#287: konsol ships a file or directory named after one "
        "customer's dimension:\n  " + "\n  ".join(rows)
    )


def test_path_allow_list_is_not_stale():
    rows = sorted(set(ALLOWED_DIMENSION_PATHS) - scan_paths())
    assert rows == [], (
        "konsol#287: these paths are allow-listed but no longer exist. Delete "
        "the entries:\n  " + "\n  ".join(rows)
    )


# --- the guard catches structural use (synthetic content) -----------------

def test_python_dict_key_and_value_are_caught():
    source = 'CH_FIELD_MAP = {"dim_cost_center": "dim_cost_center"}\n'
    assert scan_source("konsol/thing.py", source) == collections.Counter(
        {("konsol/thing.py", "dim_cost_center"): 2}
    )


def test_python_ddl_string_is_caught():
    source = 'DDL = "main_account String, dim_department String"\n'
    found = scan_source("konsol/thing.py", source)
    assert found[("konsol/thing.py", "dim_department")] == 1


def test_python_fstring_is_caught():
    source = 'sql = f"SELECT dim_business_unit FROM {table}"\n'
    found = scan_source("konsol/thing.py", source)
    assert found[("konsol/thing.py", "dim_business_unit")] == 1


def test_json_fieldname_is_caught():
    source = '{"fields": [{"fieldname": "dim_cost_center", "fieldtype": "Data"}]}'
    found = scan_source("konsol/thing.json", source)
    assert found[("konsol/thing.json", "dim_cost_center")] == 1


def test_json_dict_key_is_caught():
    source = '{"dim_department": {"fieldtype": "Data"}}'
    found = scan_source("konsol/thing.json", source)
    assert found[("konsol/thing.json", "dim_department")] == 1


def test_js_data_key_is_caught():
    source = "if (costCenter) data.dim_cost_center = String(costCenter);\n"
    found = scan_source("konsol/thing.js", source)
    assert found[("konsol/thing.js", "dim_cost_center")] == 1


def test_digit_bearing_dimension_is_caught():
    """``^dim_[a-z0-9_]+$`` is the column rule the product enforces, so
    ``dim_2024_region`` is a name a customer can legally declare. A pattern
    demanding a letter after the prefix was blind to every one of them."""
    found = scan_source("konsol/thing.py", 'DDL = "dim_2024_region String"\n')
    assert found[("konsol/thing.py", "dim_2024_region")] == 1


def test_python_static_concatenation_is_caught():
    """The one assembled name that is decidable: nothing about it is runtime.
    Folded and reported once — as the name, not as its pieces."""
    source = 'COLUMN = "dim_" + "cost_center"\n'
    assert scan_source("konsol/thing.py", source) == collections.Counter(
        {("konsol/thing.py", "dim_cost_center"): 1}
    )


def test_python_implicit_concatenation_is_caught():
    source = 'COLUMN = ("dim_" "department")\n'
    assert scan_source("konsol/thing.py", source) == collections.Counter(
        {("konsol/thing.py", "dim_department"): 1}
    )


def test_json_list_under_a_prose_key_is_caught():
    """A prose key covers its own string value, not the members of a list
    beneath it. ``{"description": ["dim_cost_center"]}`` is data wearing a
    prose name."""
    source = '{"description": ["dim_cost_center"]}'
    found = scan_source("konsol/thing.json", source)
    assert found[("konsol/thing.json", "dim_cost_center")] == 1


# --- the hole this guard was built with: a '#' made it blind ---------------
# Both of these are the measured defeats, run against the real tree before the
# fix, reduced to one line each. `#` is a hex colour and an id selector, `//`
# is in every URL, and the old scanner called all three "comment" and blanked
# the rest of the line — 78% of the shipped unparsed text, 96.4% of the CSS.

def test_hex_colour_does_not_hide_a_data_key():
    """Defeat A, reduced: appending to any konsol_exec.js line containing a
    '#' put two hardcoded dimensions into shipped JS with 26/26 still green."""
    source = (
        'var u = "https://vuejs.org/error-reference/#runtime";'
        ';data.dim_cost_center=String(cc);data.dim_department=String(d);\n'
    )
    found = scan_source("konsol/public/konsol_exec/konsol_exec.js", source)
    key = "konsol/public/konsol_exec/konsol_exec.js"
    assert found[(key, "dim_cost_center")] == 1
    assert found[(key, "dim_department")] == 1


def test_hex_colour_earlier_on_the_line_does_not_hide_a_data_key():
    """Defeat B, reduced: a whole new shipped file passed the guard."""
    source = 'var theme = "#0b5fff"; data.dim_cost_center = String(cc);\n'
    found = scan_source("konsol/public/probe_guard.js", source)
    assert found[("konsol/public/probe_guard.js", "dim_cost_center")] == 1


def test_url_does_not_hide_a_data_key():
    source = 'fetch("https://erp.example.com/api").then(r => r.dim_department);\n'
    found = scan_source("konsol/thing.js", source)
    assert found[("konsol/thing.js", "dim_department")] == 1


def test_css_id_selector_does_not_hide_a_dimension():
    source = '#grid td[data-col="dim_cost_center"] { color: #0b5fff; }\n'
    found = scan_source("konsol/thing.css", source)
    assert found[("konsol/thing.css", "dim_cost_center")] == 1


def test_html_fragment_link_does_not_hide_a_dimension():
    source = '<a href="//cdn.example.com/help#dims">x</a><td>dim_business_unit</td>\n'
    found = scan_source("konsol/thing.html", source)
    assert found[("konsol/thing.html", "dim_business_unit")] == 1


def test_js_comment_mention_is_caught_without_the_escape():
    """The deliberate trade. No language's comment syntax is stripped any
    more, so an unmarked mention in a JS comment now fires. The cost is one
    greppable marker; the benefit is that nothing in an unparsed file can
    hide behind a character that only looks like a comment."""
    source = "// e.g. dim_cost_center is sent as a data key\nvar x = 1;\n"
    found = scan_source("konsol/thing.js", source)
    assert found[("konsol/thing.js", "dim_cost_center")] == 1


# --- and leaves prose alone (synthetic content) ---------------------------
# A guard that eats documentation gets deleted by the first person it annoys,
# and takes the real rule with it.

def test_module_docstring_mention_is_not_caught():
    source = '"""Only a dim_cost_center column is accepted."""\nX = 1\n'
    assert scan_source("konsol/thing.py", source) == collections.Counter()


def test_function_docstring_mention_is_not_caught():
    source = (
        'def f():\n'
        '    """e.g. dimensions={"dim_cost_center": "CC001"}."""\n'
        '    return 1\n'
    )
    assert scan_source("konsol/thing.py", source) == collections.Counter()


def test_attribute_docstring_mention_is_not_caught():
    source = 'X = 1\n"""The dim_department column, when declared."""\n'
    assert scan_source("konsol/thing.py", source) == collections.Counter()


def test_python_comment_mention_is_not_caught():
    source = "# The D365 name for cost center == dim_cost_center's own.\nX = 1\n"
    assert scan_source("konsol/thing.py", source) == collections.Counter()


def test_json_description_mention_is_not_caught():
    source = '{"fieldname": "axis", "description": "e.g. dim_business_unit"}'
    assert scan_source("konsol/thing.json", source) == collections.Counter()


def test_js_comment_mention_with_the_escape_is_not_caught():
    source = (
        "// e.g. dim_cost_center is sent as a data key (konsol#287-prose)\n"
        "var x = 1;\n"
    )
    assert scan_source("konsol/thing.js", source) == collections.Counter()


def test_a_user_facing_message_can_name_an_example_with_the_escape():
    """The wrong-direction failure this fixes. A msgprint argument is a
    structural string to the AST and a sentence to the person reading it;
    without an escape the guard makes the product's error messages vaguer.
    konsol/dimension.py already omits a concrete example name for this reason."""
    source = (
        'def f():\n'
        '    frappe.msgprint("Add a column such as dim_cost_center")'
        '  # konsol#287-prose\n'
    )
    assert scan_source("konsol/thing.py", source) == collections.Counter()


def test_shipped_help_text_can_name_an_example_with_the_escape():
    source = '<p>Name the column dim_cost_center.</p><!-- konsol#287-prose -->\n'
    assert scan_source("konsol/help.html", source) == collections.Counter()


def test_the_escape_reports_what_it_hid():
    """An escape is not silence. Whatever it covers comes back out here, to be
    declared and counted."""
    source = 'X = "dim_cost_center"  # konsol#287-prose\n'
    assert scan_escapes("konsol/thing.py", source) == collections.Counter(
        {("konsol/thing.py", "dim_cost_center"): 1}
    )


def test_an_unescaped_line_hides_nothing():
    source = 'X = "dim_cost_center"\n'
    assert scan_escapes("konsol/thing.py", source) == collections.Counter()


def test_the_escape_only_covers_its_own_line():
    source = (
        'A = "dim_cost_center"  # konsol#287-prose\n'
        'B = "dim_department"\n'
    )
    assert scan_source("konsol/thing.py", source) == collections.Counter(
        {("konsol/thing.py", "dim_department"): 1}
    )


def test_redaction_leaves_python_parseable():
    """The placeholder is a real identifier, so redacting a bare name — not a
    string — cannot turn a scannable file into a syntax error."""
    redacted, escaped = redact_prose_escapes(
        "dim_cost_center = 1  # konsol#287-prose\n"
    )
    ast.parse(redacted)
    assert "dim_cost_center" not in redacted
    assert escaped == collections.Counter({"dim_cost_center": 1})


def test_undeclared_escape_is_reported():
    """The escape is checked by the same machinery as the debt: an escape
    nobody wrote down is a hole, and fails the run."""
    found = collections.Counter({("konsol/thing.js", "dim_cost_center"): 1})
    assert unallowed_literals(found, {}) == [
        ("konsol/thing.js", "dim_cost_center", 1, 0)
    ]


def test_declared_escape_whose_prose_is_gone_is_reported_as_stale():
    declared = {("konsol/thing.js", "dim_cost_center"): (1, "konsol#287 prose")}
    assert stale_literals(collections.Counter(), declared) == [
        ("konsol/thing.js", "dim_cost_center", 1, 0)
    ]


def test_dynamic_name_construction_is_a_declared_blind_spot():
    """Named, not hidden. A name assembled from a variable is invisible to any
    text scanner — and these spellings are also how the *cure* is written, so
    they cannot be banned either. Only the fully static form is decidable, and
    ``test_python_static_concatenation_is_caught`` covers it. This test exists
    so the hole has a name somebody can grep for."""
    for source in (
        'C = "dim_" + name\n',
        'C = "dim_%s" % name\n',
        'C = f"dim_{name}"\n',
        'C = "_".join(["dim", name])\n',
    ):
        assert scan_source("konsol/thing.py", source) == collections.Counter(), source


def test_machinery_identifiers_are_not_caught():
    """dim_names, dim_valid, dim_headers name no dimension. They are the code
    that makes dimensions configurable — the cure, not the disease."""
    source = (
        "def f(dim_names):\n"
        "    dim_valid = True\n"
        "    dim_headers = sorted(dim_names)\n"
        "    dim_types = {}\n"
        "    for dim_name in dim_headers:\n"
        "        dim_types[dim_name] = 'string'\n"
        "    return dim_valid, dim_types\n"
    )
    assert scan_source("konsol/thing.py", source) == collections.Counter()


def test_bare_prefix_and_column_patterns_are_not_caught():
    """'dim_', 'dim_%' and the column regex are quoted, but none of them names
    anybody's dimension."""
    source = (
        'PREFIX = "dim_"\n'
        'SAFE = re.compile(r"^dim_[a-z0-9_]+$")\n'
        'FILTER = {"fieldname": ("like", "dim_%")}\n'
    )
    assert scan_source("konsol/thing.py", source) == collections.Counter()


# --- the allow-list cannot rot (synthetic allow-lists) --------------------
# Synthetic, so these cannot pass by accident because the real list happens to
# be right today.

def test_violation_beyond_the_allowance_is_reported():
    allowed = {("konsol/thing.py", "dim_cost_center"): (1, "konsol#287")}
    found = collections.Counter({("konsol/thing.py", "dim_cost_center"): 2})
    assert unallowed_literals(found, allowed) == [
        ("konsol/thing.py", "dim_cost_center", 2, 1)
    ]


def test_violation_in_an_unlisted_file_is_reported():
    allowed = {("konsol/thing.py", "dim_cost_center"): (1, "konsol#287")}
    found = collections.Counter({("konsol/other.py", "dim_cost_center"): 1})
    assert unallowed_literals(found, allowed) == [
        ("konsol/other.py", "dim_cost_center", 1, 0)
    ]


def test_allowed_violation_at_its_exact_count_passes():
    allowed = {("konsol/thing.py", "dim_cost_center"): (2, "konsol#287")}
    found = collections.Counter({("konsol/thing.py", "dim_cost_center"): 2})
    assert unallowed_literals(found, allowed) == []
    assert stale_literals(found, allowed) == []


def test_fixed_site_left_on_the_list_is_reported_as_stale():
    """The one that matters. Somebody removed the hardcoded name, the licence
    stayed, and the next person could put it back for free."""
    allowed = {("konsol/thing.py", "dim_cost_center"): (2, "konsol#287")}
    assert stale_literals(collections.Counter(), allowed) == [
        ("konsol/thing.py", "dim_cost_center", 2, 0)
    ]


def test_partly_fixed_site_is_reported_as_stale():
    allowed = {("konsol/thing.py", "dim_cost_center"): (2, "konsol#287")}
    found = collections.Counter({("konsol/thing.py", "dim_cost_center"): 1})
    assert stale_literals(found, allowed) == [
        ("konsol/thing.py", "dim_cost_center", 2, 1)
    ]


# --- the list is only useful if every line says what removes it -----------

def test_every_literal_allowance_cites_an_issue():
    for key, (count, why) in sorted(ALLOWED_DIMENSION_LITERALS.items()):
        assert count > 0, f"{key}: an allowance of zero is not an allowance"
        assert "konsol#" in why, f"{key}: no issue cited for this allowance"


def test_every_prose_declaration_gives_a_reason():
    for key, (count, why) in sorted(DECLARED_PROSE_MENTIONS.items()):
        assert count > 0, f"{key}: a declaration of zero declares nothing"
        assert len(why) > 20, f"{key}: no reason given for reading this as prose"


def test_every_path_allowance_cites_an_issue():
    for path, why in sorted(ALLOWED_DIMENSION_PATHS.items()):
        assert "konsol#" in why, f"{path}: no issue cited for this allowance"


def test_the_scan_actually_reaches_the_tree():
    """A scanner that reads nothing passes everything. The allow-list above is
    only evidence if these files are really being opened."""
    files = shipped_files()
    assert len(files) > 100, f"the scan found only {len(files)} shipped files"
    assert "konsol/clickhouse.py" in files
    assert "konsol/d365_writeback.py" not in files
    assert not [f for f in files if f.startswith("konsol/tests/")]
