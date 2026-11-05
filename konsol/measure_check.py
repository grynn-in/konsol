"""Check a Measure expression the way dbt will run it (konsol#229, option C).

``Measure.expression`` is raw SQL. dbt pastes it, verbatim, into
``gold_trial_balance`` and ``gold_trial_balance_by_partner``
(konsolidat ``measure_helpers.sql``: ``{{ m.expression }} as {{ m.name }}``),
over ``silver_gl_entries``, grouped by entity, period, account and the
declared dimensions. Only the measures the Published trial-balance Dataset
declares are compiled (``dbt_config._build_measures_vars``). Before this
module a bad expression saved and published cleanly and failed the next dbt
build, in another system, naming a column rather than the measure.

The check runs the expression over the same table, grouped the same way,
with ``LIMIT 0`` and ``readonly=1``. ClickHouse decides, as it will at build
time; konsol keeps no column list of its own, because ``dim_*`` columns are
declared per site. A refusal names the measure and lists the table's columns.

When the warehouse cannot answer (unreachable, or the table not built yet on
a new site), the save goes through and the user is told on screen that the
expression was not checked. A bad expression stops the build; it does not
make a number wrong, so it is reported rather than refused there.

Chosen under Deepak Pai's standing rule of 5 Nov 2026 (product,
configurable, not opinionated); the HMD table is on konsol#229. Rejected: a
pinned column list (wrong for any site with its own dimensions), and a
restricted expression grammar (konsol deciding which SQL a site may write).
"""
import re

import frappe

#: The table dbt compiles a trial-balance measure over (konsolidat
#: ``gold_trial_balance.sql``: ``from {{ ref('silver_gl_entries') }}``).
SOURCE_TABLE = "epm_silver.silver_gl_entries"

#: ``gold_trial_balance``'s GROUP BY, without the declared dimensions (which
#: are grouped too, but do not change whether an expression is legal).
GRAIN = ("data_area_id", "fiscal_year", "fiscal_period", "main_account")

#: What one expression in a SELECT list cannot contain. dbt writes the
#: expression on one line followed by ``as <name>``: a line comment or a line
#: break would swallow or split the alias, a block comment could swallow the
#: rest of the model, and a semicolon ends the statement.
FORBIDDEN = {";": "a semicolon", "--": "a line comment (--)", "/*": "a block comment (/*)",
             "\n": "a line break", "\r": "a line break"}

#: ClickHouse error names that mean the warehouse could not answer, not that
#: the expression is wrong. Anything else is the expression's fault.
CANNOT_ANSWER = {
    "UNKNOWN_TABLE", "UNKNOWN_DATABASE", "AUTHENTICATION_FAILED", "REQUIRED_PASSWORD",
    "TIMEOUT_EXCEEDED", "MEMORY_LIMIT_EXCEEDED", "TOO_MANY_SIMULTANEOUS_QUERIES",
    "ACCESS_DENIED", "NOT_ENOUGH_PRIVILEGES",
}


class CannotCheck(Exception):
    """The warehouse could not answer; the expression is neither right nor wrong."""


def static_problem(expression):
    """What makes ``expression`` unusable as one SELECT-list item, or None."""
    if not (expression or "").strip():
        return "it is empty"
    for token, what in FORBIDDEN.items():
        if token in expression:
            return f"it contains {what}"
    return None


def dry_run_sql(name, expression):
    """The statement the check runs: the expression as dbt writes it."""
    return (
        f"SELECT {', '.join(GRAIN)}, {expression} as `{name}` "
        f"FROM {SOURCE_TABLE} GROUP BY {', '.join(GRAIN)} LIMIT 0"
    )


def compiled_measure_names():
    """The measures dbt compiles into gold_trial_balance; [] when none are declared."""
    from konsol.dbt_config import TrialBalanceMeasuresNotDeclared, _trial_balance_measure_names

    try:
        return _trial_balance_measure_names()
    except TrialBalanceMeasuresNotDeclared:
        # base_measures is not written at all then, so nothing is compiled.
        return []


def _error_name(text):
    """ClickHouse's error name, e.g. UNKNOWN_IDENTIFIER, from its message."""
    found = re.findall(r"\(([A-Z][A-Z0-9_]+)\)", text or "")
    return found[0] if found else None


def _first_line(text):
    text = (text or "").strip()
    text = text.split(" from ClickHouse: ", 1)[-1]
    return text.splitlines()[0][:400] if text else ""


def _columns(run):
    try:
        out = run(f"DESCRIBE TABLE {SOURCE_TABLE} FORMAT TabSeparated", {"readonly": "1"})
    except Exception:
        return []
    return [line.split("\t", 1)[0] for line in (out or "").splitlines() if line.strip()]


def dry_run(name, expression, run=None):
    """Run the expression over SOURCE_TABLE. Raise frappe.ValidationError when
    ClickHouse refuses it; raise CannotCheck when ClickHouse cannot answer."""
    import requests

    if run is None:
        from konsol.clickhouse import execute as run
    try:
        run(dry_run_sql(name, expression), {"readonly": "1"})
    except requests.HTTPError as exc:
        text = str(exc)
        code = _error_name(text)
        if code is None or code in CANNOT_ANSWER:
            raise CannotCheck(_first_line(text)) from exc
        columns = _columns(run)
        listed = f" Columns you can use: {', '.join(columns)}." if columns else ""
        frappe.throw(
            f"Measure {name}: ClickHouse refused the expression {expression!r} when run "
            f"over {SOURCE_TABLE}, which is what dbt compiles it into "
            f"(gold_trial_balance), so the next build would fail. ClickHouse said: "
            f"{_first_line(text)}.{listed} The expression must be one aggregate "
            f"over those columns, for example sum(<column>).",
            frappe.ValidationError,
            title="Measure expression refused",
        )
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise CannotCheck(f"ClickHouse is unreachable ({type(exc).__name__})") from exc


def check_measure(name, expression, publishing, compiled=None, run=None):
    """Refuse an expression that cannot work; dry-run it when it is being published
    into a measure dbt compiles. Returns "checked", "not compiled", "unchecked"
    or None (nothing to run)."""
    problem = static_problem(expression)
    if problem:
        frappe.throw(
            f"Measure {name}: the expression must be one SQL expression on one line, "
            f"and {problem}.",
            frappe.ValidationError,
            title="Measure expression refused",
        )
    if not publishing:
        return None
    names = compiled_measure_names() if compiled is None else compiled
    if name not in names:
        return "not compiled"
    try:
        dry_run(name, expression, run=run)
    except CannotCheck as exc:
        frappe.msgprint(
            f"Measure {name} was saved but its expression was not checked: {exc}. "
            f"If it is wrong, the next dbt build will fail. Publish it again once "
            f"the warehouse answers to check it.",
            title="Expression not checked",
            indicator="orange",
        )
        return "unchecked"
    return "checked"


def describe_compilation(name):
    """One line for the Measure form: which Dataset declares this measure, and
    whether dbt compiles its expression."""
    parents = frappe.get_all(
        "Dataset Measure",
        filters={"measure": name, "parenttype": "Dataset"},
        pluck="parent",
        order_by="parent asc",
    )
    if not parents:
        return "No Dataset declares this measure, so dbt does not compile its expression."
    declared = ", ".join(sorted(set(parents)))
    if name in compiled_measure_names():
        return (f"Declared by {declared}. dbt compiles the expression into gold_trial_balance "
                f"over {SOURCE_TABLE}, grouped by entity, period, account and dimensions.")
    return (f"Declared by {declared}. dbt does not compile the expression: only the measures "
            f"of the Published trial-balance Dataset are compiled (and not its rerouted one).")
