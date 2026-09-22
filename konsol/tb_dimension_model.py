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

So the refusal names the header and says which of three things is wrong,
because the fix differs in each case:

  * not declared at all  -> declare the Dimension
  * declared, not Published -> publish it
  * Published, flag off  -> tick in_trial_balance

A dimension is identified by ``dimension_name`` verbatim: the header IS
``dim_cost_center``. There is no aliasing and no case-mapping of the
customer's own words (decision 1 of 16 Sep 2026 fixed that contract).
"""

PREFIX = "dim_"

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
    and ``in_trial_balance``. A name is accepted only when the Dimension is
    Published and the flag is ticked; names are taken verbatim.
    """
    return frozenset(
        _name(row.get("dimension_name"))
        for row in declared_rows
        if _name(row.get("dimension_name"))
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
    three refusals it is, so the caller can show the reader what to do. A
    header repeated in the file is reported once.
    """
    by_name = {}
    for row in declared_rows:
        name = _name(row.get("dimension_name"))
        if name:
            by_name.setdefault(name, row)

    problems = []
    seen = set()
    for header in headers:
        name = _name(header)
        if not is_dimension_column(name) or name in seen:
            continue
        seen.add(name)

        row = by_name.get(name)
        if row is None:
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
