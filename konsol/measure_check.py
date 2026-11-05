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
with ``WHERE 0`` (nothing is read or aggregated), ``readonly=1`` and a
5-second server-side limit. ClickHouse decides, as it will at build time;
konsol keeps no column list of its own, because ``dim_*`` columns are
declared per site. A refusal names the measure and lists the table's columns.

It runs as a dedicated ClickHouse user that may SELECT only
``epm_silver.silver_gl_entries`` (EPM Settings: Measure-Check ClickHouse
User; defined in konsolidat ``clickhouse/set-password.sh``). An expression is
SQL an EPM Admin writes, and ClickHouse runs a scalar subquery inside it
before ``WHERE 0`` applies, so under the site's main user the check would
let them read any table in the shared warehouse and see the answer in the
error text. The grants are the control. Refusing ``SELECT``, table functions
and ``system.`` in the expression before sending is defence in depth only.
When the check user is not configured the expression is not run at all: it
never falls back to the main user.

Whether the warehouse can answer is decided by a probe (``EXISTS TABLE`` as
the check user), not by the expression's error. When the probe fails (the
user is refused, ClickHouse is unreachable, the table is not built yet on a
new site) the save goes through and the user is told, on screen and in the
Measure's Expression Check field, that the expression was not checked. A bad
expression stops the build; it does not make a number wrong, so it is
reported rather than refused there. Once the probe answers, any error from
the expression refuses the save.

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
#: rest of the model, and a semicolon ends the statement. ClickHouse also reads
#: ``#`` as a line comment.
FORBIDDEN = {";": "a semicolon", "--": "a line comment (--)", "#": "a line comment (#)",
             "/*": "a block comment (/*)", "\n": "a line break", "\r": "a line break"}

#: Defence in depth, matched case-insensitively before anything is sent: a
#: subquery, a system table, or a table function. The control is the check
#: user's grants; this only keeps an obvious probe off the warehouse.
SUSPECT = (
    (re.compile(r"\bselect\b", re.I), "a subquery (SELECT)"),
    (re.compile(r"\bsystem\s*\.", re.I), "a system table (system.)"),
    (re.compile(r"\b(url|file|remote|remoteSecure|cluster|clusterAllReplicas|s3|s3Cluster|"
                r"gcs|azureBlobStorage|hdfs|mysql|postgresql|mongodb|redis|sqlite|jdbc|odbc|"
                r"input|executable|viewIfPermitted|generateRandom|"
                r"numbers|numbers_mt|zeros|deltaLake|iceberg|hudi)\s*\(", re.I),
     "a table function"),
)

#: Sent with every statement. The check user's profile sets the same values
#: (readonly=1 refuses any other), so neither side can relax them.
SETTINGS = {"readonly": "1", "max_execution_time": "5",
            "cancel_http_readonly_queries_on_client_close": "1"}

#: The HTTP timeout, above max_execution_time so ClickHouse stops first.
HTTP_TIMEOUT = 10

NOT_CONFIGURED = "the measure-check ClickHouse user is not configured"


class CannotCheck(Exception):
    """The warehouse could not answer; the expression is neither right nor wrong."""


def static_problem(expression):
    """What makes ``expression`` unusable as one SELECT-list item, or None."""
    if not (expression or "").strip():
        return "it is empty"
    for token, what in FORBIDDEN.items():
        if token in expression:
            return f"it contains {what}"
    for pattern, what in SUSPECT:
        if pattern.search(expression):
            return f"it contains {what}, which a measure expression has no use for"
    return None


def requests_post(*args, **kwargs):
    import requests

    return requests.post(*args, **kwargs)


def check_connection():
    """The measure-check user's connection, or None when it is not configured.

    Host, port and TLS are the main connection's; the user and password are
    its own. There is no fallback to the main user."""
    settings = frappe.get_single("EPM Settings")
    user = (settings.clickhouse_check_user or "").strip()
    password = settings.get_password("clickhouse_check_password", raise_exception=False) or ""
    if not user or not password:
        return None
    from konsol.clickhouse import get_connection

    conn = dict(get_connection())
    conn.update(user=user, password=password)
    return conn


def checker():
    """A ``run(sql, params)`` that sends as the check user; CannotCheck when
    that user is not configured."""
    conn = check_connection()
    if conn is None:
        raise CannotCheck(NOT_CONFIGURED)
    import requests

    from konsol.clickhouse import connection_url

    url = connection_url(conn)

    def run(sql, params=None):
        resp = requests_post(url, params=dict(params or {}), data=sql.encode("utf-8"),
                             auth=(conn["user"], conn["password"]), timeout=HTTP_TIMEOUT,
                             verify=conn.get("verify", True))
        if resp.status_code >= 400:
            raise requests.HTTPError(
                f"{resp.status_code} from ClickHouse: {resp.text.strip()[:500]}", response=resp)
        return resp.text.strip()

    return run


def dry_run_sql(name, expression):
    """The statement the check runs: the expression as dbt writes it."""
    return (
        f"SELECT {', '.join(GRAIN)}, {expression} as `{name}` "
        f"FROM {SOURCE_TABLE} WHERE 0 GROUP BY {', '.join(GRAIN)}"
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
    # The last: ClickHouse echoes the query, which may contain "(NAME)" itself.
    return found[-1] if found else None


def _first_line(text):
    text = (text or "").strip()
    text = text.split(" from ClickHouse: ", 1)[-1]
    return text.splitlines()[0][:400] if text else ""


def _columns(run):
    try:
        out = run(f"DESCRIBE TABLE {SOURCE_TABLE} FORMAT TabSeparated", SETTINGS)
    except Exception:
        return []
    return [line.split("\t", 1)[0] for line in (out or "").splitlines() if line.strip()]


def probe(run):
    """Whether the check user can reach SOURCE_TABLE. Raise CannotCheck if not."""
    import requests

    try:
        exists = run(f"EXISTS TABLE {SOURCE_TABLE}", SETTINGS)
    except requests.HTTPError as exc:
        raise CannotCheck(f"ClickHouse refused the check user ({_first_line(str(exc))})") from exc
    except requests.RequestException as exc:
        raise CannotCheck(f"ClickHouse is unreachable ({type(exc).__name__})") from exc
    if (exists or "").strip() != "1":
        raise CannotCheck(f"{SOURCE_TABLE} is not built yet")


def dry_run(name, expression, run=None):
    """Run the expression over SOURCE_TABLE as the check user. Raise
    frappe.ValidationError when ClickHouse refuses it; raise CannotCheck when
    the probe shows ClickHouse cannot answer."""
    import requests

    if run is None:
        run = checker()
    probe(run)
    try:
        run(dry_run_sql(name, expression), SETTINGS)
    except requests.HTTPError as exc:
        text = str(exc)
    except requests.Timeout:
        text = f"no answer within {HTTP_TIMEOUT} seconds"
    except requests.RequestException as exc:
        # The probe answered a moment ago; the connection, not the expression, failed.
        raise CannotCheck(f"ClickHouse is unreachable ({type(exc).__name__})") from exc
    else:
        return
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


def check_measure(name, expression, publishing, compiled=None, run=None):
    """Refuse an expression that cannot work; dry-run it when it is being published
    into a measure dbt compiles.

    Returns None when nothing was run (not publishing), else
    ``{"status": "checked" | "not checked" | "not compiled", "line": ...}``;
    ``line`` is what the Measure's Expression Check field records."""
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
        return {"status": "not compiled",
                "line": "Not compiled: dbt does not compile this measure, so it was not run."}
    try:
        dry_run(name, expression, run=run)
    except CannotCheck as exc:
        frappe.msgprint(
            f"Measure {name} was saved but its expression was not checked: {exc}. "
            f"If it is wrong, the next dbt build will fail. Publish it again once "
            f"this is fixed to check it.",
            title="Expression not checked",
            indicator="orange",
        )
        return {"status": "not checked", "line": f"Not checked: {exc}."}
    return {"status": "checked",
            "line": f"Checked: ClickHouse ran it over {SOURCE_TABLE} as dbt will."}


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
