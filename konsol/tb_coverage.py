"""Which entity-periods hold a trial balance, and the refusal of an actuals
read of one that holds none (konsol#401).

An actuals read of a period with no trial balance used to return 0, the same
as a loaded period where the account is zero: a monthly P&L sheet over an
annual (P12-only) file showed eleven believable zeros. The warehouse's own
record of what is loaded is ``epm_gold.gold_trial_balance``, the table the
actuals Dataset reads: an (entity, fiscal_year, fiscal_period) is loaded when
it holds at least one row there. The balance-sheet reroute
(``gold_balance_sheet``) is built from it and does not roll a balance forward
into an unloaded period, so the same record covers both.

The rule, for one cell:

- a named entity is refused when NONE of the cell's periods is loaded for it.
  A range (Q1, FY) with at least one loaded period reads as before: an annual
  file read as FY is the year.
- a wildcard (all-entity) read is refused when no entity in the reader's
  scope has any of the cell's periods loaded.

Pure: no frappe, no ClickHouse. The caller runs ``coverage_query`` once per
call and passes the parsed set in.
"""

#: The table whose rows say a trial balance is loaded.
TB_TABLE = "epm_gold.gold_trial_balance"

#: The scenario this rule applies to: the Dataset scenario_key and the
#: hierarchy scenario. Budget reads are not checked: a missing budget is
#: budget zero (decision of 22 Sep 2026).
ACTUALS = "actuals"

#: The ``error_codes`` entry of a refused cell; the add-in shows it as #N/A.
NO_DATA = "no_data"


def coverage_query(years, table=TB_TABLE, entities=None):
    """One statement listing every loaded (entity, year, period) in ``years``.

    ``table`` is the table the read itself queries: the flat path passes its
    actuals Dataset's ``clickhouse_table`` (validated by the caller), so a
    site that points the Dataset elsewhere is judged against what it reads.
    ``entities``, when given, narrows the scan to the named entities; None
    (a wildcard read) means every entity.

    Returns (sql, params) in the ``{name:Type}`` / ``param_<name>`` form
    api._clickhouse_query takes. The rows are entities x periods per year,
    so the reply stays small however many cells asked.
    """
    params = {}
    placeholders = []
    for i, year in enumerate(sorted({int(y) for y in years})):
        placeholders.append(f"{{cy{i}:Int32}}")
        params[f"param_cy{i}"] = str(year)
    sql = (
        "SELECT DISTINCT data_area_id, fiscal_year, fiscal_period "
        f"FROM {table} "
        f"WHERE fiscal_year IN ({', '.join(placeholders)})"
    )
    if entities is not None:
        ent = []
        for i, entity in enumerate(sorted(set(entities))):
            ent.append(f"{{ce{i}:String}}")
            params[f"param_ce{i}"] = entity
        # An empty list must still be valid SQL: '' matches no loaded entity.
        sql += " AND data_area_id IN (" + (", ".join(ent) or "''") + ")"
    return sql, params


def parse_coverage(raw):
    """{(entity, year, period)} from the TabSeparated reply."""
    loaded = set()
    for line in (raw or "").split("\n"):
        if not line:
            continue
        entity, year, period = line.split("\t")
        loaded.add((entity, int(year), int(period)))
    return loaded


def period_label(periods):
    """``P5``, ``P1-P3`` for a run, ``P1, P3`` otherwise."""
    ps = sorted({int(p) for p in periods})
    if not ps:
        # A token the calendar resolves to nothing (e.g. FY of a year that
        # declares no Regular period): refused for its own row, never an
        # IndexError that fails the whole batch.
        return "(no periods)"
    if len(ps) == 1:
        return f"P{ps[0]}"
    if ps == list(range(ps[0], ps[-1] + 1)):
        return f"P{ps[0]}-P{ps[-1]}"
    return ", ".join(f"P{p}" for p in ps)


def no_tb_error(entity, year, periods, loaded):
    """Why a named entity's cell cannot be read, or None."""
    year = int(year)
    if any((entity, year, int(p)) in loaded for p in periods):
        return None
    return f"No trial balance for {entity} {year} {period_label(periods)}"


def no_tb_error_in_scope(year, periods, loaded, scope):
    """Why a wildcard cell cannot be read, or None. ``scope`` is the entities
    the reader may read; None means every entity."""
    year = int(year)
    wanted = {int(p) for p in periods}
    allowed = None if scope is None else set(scope)
    for entity, y, p in loaded:
        if y == year and p in wanted and (allowed is None or entity in allowed):
            return None
    return f"No trial balance for any entity in {year} {period_label(periods)}"
