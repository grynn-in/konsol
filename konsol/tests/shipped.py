"""Where konsol's shipped rows live (konsol#230).

konsol ships from two directories now, and which one a file is in is the whole
point of the split:

    konsol/fixtures/   force-reimported every migrate — product structure only
    konsol/defaults/   seeded create-if-missing — the site-owned semantic model

Tests that care about *what konsol ships* should not care which directory it
came from, so they use these helpers. Tests that care about *retirability* —
`test_defaults_seeding.py` — deliberately do not, and read each directory
directly.

Not named test_*, so the runner does not collect it as a test module.
"""
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(APP_DIR, "fixtures")
DEFAULTS = os.path.join(APP_DIR, "defaults")
DIRS = (FIXTURES, DEFAULTS)


def shipped_path(filename):
    """The path konsol ships `filename` from, or None if it ships no such file."""
    for directory in DIRS:
        path = os.path.join(directory, filename)
        if os.path.exists(path):
            return path
    return None


def shipped(filename):
    """The rows konsol ships in `filename`.

    Raises rather than returning [] for a file that does not ship: an empty
    list would let a test pass by asserting nothing over nothing, which is how
    a moved file stops being checked without anyone noticing.
    """
    path = shipped_path(filename)
    if path is None:
        raise AssertionError(
            f"konsol ships no {filename} (looked in fixtures/ and defaults/). "
            "If that is deliberate, the test asserting over it needs updating.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ships(filename):
    """Whether konsol ships this file at all."""
    return shipped_path(filename) is not None


def all_shipped():
    """{filename: rows} across both directories."""
    out = {}
    for directory in DIRS:
        for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
            with open(path, encoding="utf-8") as f:
                out[os.path.basename(path)] = json.load(f)
    return out
