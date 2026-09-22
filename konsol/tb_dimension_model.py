"""Which dim_* columns a trial-balance file may carry (konsol#255). Pure; host-tested.

A site declares its analysis dimensions as Dimension records, and each one
says whether it belongs on a trial balance. A file may therefore carry a
``dim_<name>`` column only for a Dimension that is Published AND ticked
``in_trial_balance``; every other ``dim_*`` header is refused.

This is konsol#247 applied to the intake. The loader must never promote an
undeclared value into configuration: a file that arrives with
``dim_cost_centre`` when the site declared ``dim_cost_center`` is a typo or a
different site's export, and quietly creating the dimension — or quietly
dropping the column — would make the uploader, not the administrator, the
author of the site's chart of analysis.

So the refusal names the header and says which of four things is wrong,
because the fix differs in each case:

  * name is not lower snake case -> rename the Dimension
  * not declared at all  -> declare the Dimension
  * declared, not Published -> publish it
  * Published, flag off  -> tick in_trial_balance

A dimension is identified by ``dimension_name`` verbatim: the header IS
``dim_cost_center``. There is no aliasing and no case-mapping of the
customer's own words (decision 1 of 16 Sep 2026 fixed that contract).

That contract is why the name itself has to be constrained. Both parsers
lowercase a header before validating it, so a Dimension named
``dim_Cost_Center`` could match no header ever written, and the admin was
refused with "create the Dimension dim_cost_center" while looking at the
Published, ticked Dimension on screen. ``schema_apply`` already refuses that
name for its own reason — it interpolates the name into DDL and will not
create a column it cannot spell — so there is one answer in the system
already, and this module adopts it rather than inventing a third position:

  a dimension name is ``dim_`` followed by lower-case letters, digits and
  underscores, and nothing else.

A name that is not is REFUSED by name, not quietly lowercased: lowercasing
would collapse ``dim_Cost_Center`` and ``dim_cost_center`` onto one column
and put two dimensions' values in one place — a silent-data bug worse than
the unactionable refusal it would replace.
"""
import re

PREFIX = "dim_"

#: What a ``dimension_name`` may be. This MUST stay character-for-character
#: identical to ``_SAFE_TB_DIM_COLUMN`` in ``konsol/schema_apply.py``, which is
#: what decides whether the ClickHouse column can be created at all. It is
#: copied rather than imported because that module imports frappe and this one
#: is pure (see the host-test purity check in test_submit_period_gate.py). If
#: the two ever drift, the drift is silent in exactly the worst direction: this
#: rule accepts a header whose column the sync then refuses to make, and the
#: file's values land nowhere. ``\Z`` rather than ``$`` is load-bearing — ``$``
#: also matches before a trailing newline, so "dim_x\n" would pass.
_LEGAL_DIMENSION_NAME = re.compile(r"^dim_[a-z0-9_]+\Z")

PUBLISHED = "Published"

FLAG = "in_trial_balance"

#: Strings a Check field can arrive as that mean "off". A Frappe Check is an
#: int, but a row that came through JSON, CSV or a REST payload can carry the
#: text "0" — which is truthy in Python. Treating it as on would switch a
#: dimension into the trial balance by accident, so the off-values are named
#: here rather than left to truthiness.
_OFF_TEXT = ("0", "false", "no", "")


def _name(value):
    """A header or dimension name as a plain stripped string ("" for nothing)."""
    return str(value).strip() if value is not None else ""


def _stored(value):
    """A ``dimension_name`` exactly as stored, UNSTRIPPED ("" for nothing).

    The legality test runs on this, not on ``_name``: ``schema_apply``
    fullmatches the raw field, so a padded name is refused there too, and
    stripping here would accept a name whose column can never be created.
    """
    return str(value) if value is not None else ""


def is_legal_dimension_name(name):
    """Whether `name` may be a ``dimension_name``. See ``_LEGAL_DIMENSION_NAME``."""
    return bool(_LEGAL_DIMENSION_NAME.fullmatch(_stored(name)))


def is_dimension_column(header):
    """True when `header` is a dim_* column name, i.e. this module's business.

    A bare ``dim_`` with nothing after it names no dimension, so it is not one.
    """
    name = _name(header)
    return name.startswith(PREFIX) and len(name) > len(PREFIX)


def _is_on(value):
    """Whether a Check-shaped value means ticked. See ``_OFF_TEXT``."""
    if isinstance(value, str):
        return value.strip().casefold() not in _OFF_TEXT
    return bool(value)


def accepted_dimension_columns(declared_rows):
    """The dim_* header names a trial-balance file may carry.

    `declared_rows` is an iterable of dicts with ``dimension_name``, ``status``
    and ``in_trial_balance``. A name is accepted only when it is a legal
    dimension name AND the Dimension is Published AND the flag is ticked;
    accepted names are taken verbatim, which is now safe because a legal name
    is already lower snake case and so is every header the parsers produce.
    """
    return frozenset(
        _stored(row.get("dimension_name"))
        for row in declared_rows
        if is_legal_dimension_name(row.get("dimension_name"))
        and row.get("status") == PUBLISHED
        and _is_on(row.get(FLAG))
    )


def dimension_problems(headers, declared_rows):
    """Sentences describing every dim_* header the file may not carry.

    `headers` are the file's header names, already alias-resolved and
    lowercased. Blank headers are not columns and non-``dim_*`` headers are
    somebody else's rule; both are ignored. An empty list means every dim_*
    header in the file is accepted.

    One sentence per offending header, each naming the header and which of the
    four refusals it is, so the caller can show the reader what to do. A
    header repeated in the file is reported once.
    """
    by_name = {}
    # Dimensions whose own name is illegal, keyed the way a parser would have
    # rendered them as a header (lower-cased, padding gone). This map is used
    # ONLY to explain a refusal — never to accept anything — so a loose match
    # here cannot let a value into a column; it can only turn "create the
    # Dimension you are looking at" into "rename the Dimension you are
    # looking at". The legal map is built first and wins, so a site that has
    # both dim_cost_center and dim_Cost_Center keeps them apart.
    illegal_by_headerish = {}
    for row in declared_rows:
        stored = _stored(row.get("dimension_name"))
        if is_legal_dimension_name(stored):
            by_name.setdefault(stored, row)
        elif stored.strip():
            illegal_by_headerish.setdefault(stored.strip().casefold(), stored)

    problems = []
    seen = set()
    for header in headers:
        name = _name(header)
        if not is_dimension_column(name) or name in seen:
            continue
        seen.add(name)

        row = by_name.get(name)
        if row is None:
            # An illegal name is reported for its name and nothing else: its
            # status and its flag are beside the point, because no amount of
            # publishing or ticking can make a column the sync will not create.
            stored = illegal_by_headerish.get(name)
            if stored is not None:
                problems.append(
                    # The Dimension is shown as repr so padding and other
                    # invisible characters are visible in the refusal; the
                    # header keeps the plain quoting the other three use.
                    f'Column "{name}" can only be the Dimension {stored!r}, whose '
                    f"name is not a legal dimension name; rename that Dimension "
                    f"to lower snake case ({PREFIX} then lower-case letters, "
                    f"digits or underscores) before a file may carry it."
                )
                continue
            problems.append(
                f'Column "{name}" is not declared as a dimension on this site; '
                f"create the Dimension {name} before a file may carry it."
            )
            continue

        status = _name(row.get("status")) or "Draft"
        if status != PUBLISHED:
            problems.append(
                f'Column "{name}" names a dimension that is {status}, not Published; '
                f"publish the Dimension {name} before a file may carry it."
            )
            continue

        if not _is_on(row.get(FLAG)):
            problems.append(
                f'Column "{name}" names a dimension that is not on the trial balance; '
                f"tick {FLAG} on the Dimension {name} before a file may carry it."
            )
    return problems
