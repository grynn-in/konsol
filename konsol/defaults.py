"""Seed the site-owned semantic model, create-if-missing (konsol#230).

`konsol/fixtures/` is force-reimported on every `bench migrate`: a row there
overwrites whatever a site holds under the same name. That is right for the
product's own structure — Build Scope, Build Model, Pipeline — and wrong for a
semantic model. A site could unpublish a Dataset, deactivate a Scenario or
retire a Measure, and the next migrate would put it back, Published. The
product already contradicted itself about this, because `konsol config export`
moves Dimension, Measure, Dataset and Connector as a portable, site-owned
bundle: a site could export its semantic model, edit it, apply it, and lose the
edits on the next migrate.

Decision (Deepak Pai, 18 September 2026, konsol#230): **option A — the semantic
model is site-owned.** What ships from here is a *starting point*, not a
contract. This mirrors `workflows.install_workflows()`, for the reason its own
docstring gives: *"so a site may customise them."*

The two rules it expresses:

  1. **Nothing that ships is un-retirable.** Retire a row here and it stays
     retired — this module never updates and never re-publishes.
  2. **Nothing that ships creates a table in the customer's warehouse.** The
     three driver-sample Datasets that carried ``generates_source = 1`` left
     with the allocation removal (konsol#264); a test holds the rule.

Dimension ships nothing at all (decided 17 September 2026 — the three shipped
dimensions encoded one customer's vocabulary), and neither does Spread Profile.

**Known trade-off, deliberate:** a site that edits a shipped Dataset no longer
receives later product improvements to it. That is the price of being able to
retire it, and it is the same trade the workflows already make.
"""

import json
import os

import frappe

DEFAULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "defaults")

#: Insert order. Datasets reference Measures through their `fact_measures`
#: child rows, so Measures must exist first or a fresh install inserts a
#: Dataset whose measures point at nothing.
ORDER = ("measure.json", "dataset.json", "scenario.json")


def _rows():
    """Every row in defaults/, in insert order, then anything added later.

    The directory is enumerated rather than ORDER trusted as a whitelist: a new
    file dropped in must be seeded, not silently ignored.
    """
    listed = [f for f in ORDER if os.path.exists(os.path.join(DEFAULTS_DIR, f))]
    rest = sorted(f for f in os.listdir(DEFAULTS_DIR)
                  if f.endswith(".json") and f not in listed)
    for filename in listed + rest:
        with open(os.path.join(DEFAULTS_DIR, filename), encoding="utf-8") as fh:
            for row in json.load(fh):
                yield filename, row


def install_defaults():
    """Insert any default the site does not already have. Never overwrites.

    Returns the names inserted, so `bench migrate` says what it did — a silent
    seeder is indistinguishable from one that did not run.
    """
    inserted = []
    for filename, row in _rows():
        doctype = row.get("doctype")
        name = row.get("name")
        if not doctype or not name:
            frappe.logger().warning(
                f"konsol#230: skipping a row in defaults/{filename} with no doctype/name")
            continue
        if not frappe.db.table_exists(doctype):
            # A doctype that is not installed yet (fresh site, mid-migrate).
            continue
        # The guard that makes this seeding and not fixtures. A row the site
        # has — edited, unpublished, or untouched — is left exactly as it is.
        if frappe.db.exists(doctype, name):
            continue
        frappe.get_doc(dict(row)).insert(ignore_permissions=True)
        inserted.append(f"{doctype} {name}")
    if inserted:
        print(f"konsol#230 defaults: seeded {len(inserted)} row(s): "
              + ", ".join(inserted[:10])
              + (" …" if len(inserted) > 10 else ""))
    return inserted
