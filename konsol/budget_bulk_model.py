"""Bulk budget files: the header contract (konsol#348 M1). Pure; host-tested.

A budget file is a trial balance file (konsol.tb_bulk_model) with two more
required columns, `scenario` and `layer`, and no partner column:

    scenario, layer, data_area_id, fiscal_year, fiscal_period, main_account, debit, credit, currency[, description][, amount_basis]

Everything the trial balance reader does, this one does by calling it
(tb_bulk_model.split_by_contract), not by copying it: the case-, space- and
alias-forgiving header (ALIASES is the TB's), an unknown header refused by
name, debit and credit as two non-signed columns (Q3-a), the currency column
(judged per group later), the optional amount_basis column and its one value
per group, and the line errors reported together.

What differs:

- `scenario` and `layer` are required, and a blank cell in either is a line
  error. A row is grouped by (scenario, fiscal_year, data_area_id, layer,
  fiscal_period): one Budget Entry per (Cycle, entity, layer, period), where
  the Cycle is the (scenario, fiscal_year) pair (Q1-b2). Whether the scenario,
  layer and period are declared is the per-group check's business, not the
  header's.
- `partner_data_area_id` (or any TB alias of it) is refused by name: a budget
  carries no intercompany partner until #427 decides how one would (Q2-c).
- a dim_* column is accepted only for a Dimension that is Published and ticked
  ``in_budget``; ``in_trial_balance`` does not count (konsol.tb_dimension_model).

The per-group check (check_group, M2) judges one Budget Entry against facts
the caller reads: the period must be a declared Regular period of its year
(Opening, Closing and Adjustment are refused by name, BUD-10-1) whatever its
close status, because the Cycle is what freezes a plan (Q4-b); the layer must
be a declared Budget Layer, and a site with none is told to create one
(Q8-a); the scenario must be declared and not an actual.
"""
import importlib.util as _importlib_util
import os as _os
from collections import namedtuple

from konsol.tb_balance_model import NotFinite, place_problems, valid_minor_unit
from konsol.tb_basis_model import AMOUNT_BASES, COLUMN as BASIS, canonical as canonical_basis
from konsol.tb_currency_model import code as currency_code, currency_problems, declared_currency
from konsol.period_read_model import REGULAR
from konsol.tb_bulk_model import (
    ALIASES, MAX_LINE_ERRORS, PARTNER, REQUIRED as TB_REQUIRED, Contract,
    group_basis, period_name, resolve_basis, split_by_contract, table_from_csv,
)

def _load_tb_model():
    """konsol/close/tb_model.py loaded by path (pure): reachable under the host
    tests' stub ``konsol.close`` package, as tb_bulk_model does for period_name."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "close", "tb_model.py")
    spec = _importlib_util.spec_from_file_location("konsol_budget_close_tb_model", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_tb_model = _load_tb_model()
#: The row problems a budget judges per line: the TB's account and sign rules.
_ROW_RULES = frozenset({_tb_model.UNKNOWN_ACCOUNT, _tb_model.HEADING_ACCOUNT, _tb_model.CLOSED_ACCOUNT,
                        _tb_model.NEGATIVE_AMOUNT})

#: The bases a budget file may declare (Q15-c): movements only. A period-end
#: balance needs a declared opening, so it is refused by name.
MOVEMENT, YEAR_TO_DATE, PERIOD_END = AMOUNT_BASES
BUDGET_BASES = (MOVEMENT, YEAR_TO_DATE)
PERIOD_END_REFUSAL = (
    f'Amount basis "{PERIOD_END}" is not supported yet: balance budgets need a declared '
    f'opening balance. Give "{MOVEMENT}" or "{YEAR_TO_DATE}".'
)

__all__ = ("ALIASES", "MAX_LINE_ERRORS", "BudgetKey", "REQUIRED", "OPTIONAL", "ACCEPTED", "HEADER_HELP",
           "DIMENSION_FLAG", "PARTNER_REFUSAL", "CONTRACT", "split_table", "table_from_csv",
           "group_basis", "resolve_basis", "check_group", "ACTUAL", "BUDGET_BASES", "PERIOD_END_REFUSAL")

SCENARIO = "scenario"
#: The Scenario.scenario_type a budget file may not load into.
ACTUAL = "actual"
LAYER = "layer"
REQUIRED = (SCENARIO, LAYER) + TB_REQUIRED
#: Optional columns a file may carry, beyond REQUIRED. No partner (Q2-c).
OPTIONAL = ("description", BASIS)
ACCEPTED = frozenset(REQUIRED + OPTIONAL)
#: The header as the refusals spell it.
HEADER_HELP = ("scenario, layer, data_area_id, fiscal_year, fiscal_period, main_account, debit, credit, "
               "currency[, description][, amount_basis]")
#: The Dimension flag a budget file's dim_* column must carry.
DIMENSION_FLAG = "in_budget"
PARTNER_REFUSAL = (
    f"Column {PARTNER} is not accepted on a budget file: a budget carries no intercompany "
    "partner (konsol#427 decides how one would). Remove the column."
)

#: One Budget Entry's rows: the Cycle is (scenario, fiscal_year).
BudgetKey = namedtuple("BudgetKey", "scenario fiscal_year data_area_id layer fiscal_period")

CONTRACT = Contract(
    required=REQUIRED, accepted=ACCEPTED, header_help=HEADER_HELP,
    text_keys=(SCENARIO, LAYER, "data_area_id"), whole_keys=("fiscal_year", "fiscal_period"),
    make_key=lambda v: BudgetKey(v[SCENARIO], v["fiscal_year"], v["data_area_id"], v[LAYER],
                                 v["fiscal_period"]),
    row_text=("description",), refused={PARTNER: PARTNER_REFUSAL}, flag=DIMENSION_FLAG,
    group_noun="budget entry",
)


def split_table(table, declared_dimensions):
    """Header + rows (lists of cell values) -> {BudgetKey: [rows]}, keys in
    the order they first appear in the file.

    Each row is {main_account, debit, credit, currency, description,
    amount_basis, <accepted dim_*>, line}, read exactly as the trial balance
    reads it (tb_bulk_model.split_table). Raises ValueError listing every
    structural problem so a file can be fixed in one pass.

    `declared_dimensions` are the site's Dimension rows (dimension_name,
    status, in_budget). It has no default: a caller that means "no
    dimensions" passes ().
    """
    return split_by_contract(table, declared_dimensions, CONTRACT)


def check_group(key, rows, *, period, layers, scenario, chart, functional_currency, minor_unit, form_basis,
                from_file):
    """Everything that would stop one Budget Entry loading (M2 and M3), as
    one report row {scenario, fiscal_year, entity, layer, fiscal_period, rows,
    errors, ok}. Every problem is reported together.

    The facts come from the caller and have no defaults:
    - `period`: None when (fiscal_year, fiscal_period) is not declared, else a
      dict with at least `type` (Opening/Regular/Closing/Adjustment). Its
      `status` is not read: a Closed Regular period is accepted (Q4-b).
    - `layers`: the site's declared Budget Layer codes; empty on a site that
      has none (Q8-a: a fresh site starts empty).
    - `scenario`: None when the Scenario is not declared, else a dict with
      `scenario_type`.
    - `chart`: the group chart, konsol.group_chart.chart_accounts(); empty
      means none is published (refused, naming the fix).
    - `functional_currency`: the entity's, as stored ('' or None when it has
      none: refused by name, never skipped). The rows' currency is judged by
      konsol.tb_currency_model, the rule the trial balance applies.
    - `minor_unit`: that currency's ISO Currency.minor_unit as stored (None
      when there is none: refused, no default). No line may have more decimal
      places than it has.
    - `form_basis`: the upload's Amount Basis ('' or None when it has none).
      The file's amount_basis column, when given, must agree with it. Only
      Movement and Year-to-date are accepted; Period-end balance is refused by
      name (Q15-c). A group with no basis anywhere is refused, never defaulted.
    - `from_file`: True when the rows are a file's lines (the upload), False
      for an Excel cell, an API row or a Budget Entry saved on the Desk. The
      currency and basis sentences say "the file" and "the upload" only to a
      file.

    A budget need not balance: there is no balance rule. Rows with a negative
    amount are refused, as on the trial balance.
    """
    errors = []
    label = period_name(key.fiscal_year, key.fiscal_period)
    if period is None:
        errors.append(f"{label} is not declared: a budget goes into the declared Regular periods of its year")
    elif period["type"] != REGULAR:
        kind = period["type"]
        article = "an" if kind[:1].upper() in "AEIOU" else "a"
        errors.append(f"{label} is {article} {kind} period: a budget goes into the declared Regular "
                      "periods of its year")
    errors.extend(layer_problems(key.layer, layers))
    errors.extend(scenario_problems(key.scenario, scenario))
    errors.extend(_row_problems(rows, chart))
    errors.extend(_currency_problems(key.data_area_id, functional_currency, minor_unit, rows, from_file))
    errors.extend(_basis_problems(rows, form_basis, from_file))
    return {
        "scenario": key.scenario, "fiscal_year": key.fiscal_year, "entity": key.data_area_id,
        "layer": key.layer, "fiscal_period": key.fiscal_period, "rows": len(rows),
        "errors": errors, "ok": not errors,
    }


def layer_problems(layer, layers):
    """Why `layer` is refused: it is not one of the declared Budget Layer codes
    (compared exactly). Shared by the file and the annual figure."""
    declared = list(layers)
    if layer in declared:
        return []
    if declared:
        return [f"Layer {layer} is not declared. Declared layers: {', '.join(declared)}. "
                f"Use one of them, or create {layer} as a Budget Layer"]
    return [f"Layer {layer} is not declared: this site has no Budget Layer yet. "
            f"Create {layer} (or the layers you plan in) as a Budget Layer first"]


def scenario_problems(name, scenario):
    """Why Scenario `name` is refused. `scenario` is None when it does not
    exist, else a dict with `scenario_type` and `is_active`. An inactive one is
    named inactive, not undeclared. A dict without `is_active` counts as active."""
    if scenario is None:
        return [f"Scenario {name} is not declared: create it as a Scenario first"]
    if not scenario.get("is_active", 1):
        return [f"Scenario {name} is inactive: activate it as a Scenario, or use an active one"]
    if scenario["scenario_type"] == ACTUAL:
        return [f"Scenario {name} is an actual scenario: actuals come from the trial balance, "
                "not a budget file. Use a budget, forecast or other plan scenario"]
    return []


def _row_problems(rows, chart):
    """The TB's account and sign rules (konsol.close.tb_model.check_rows), one
    sentence per problem, naming its line (nothing when the row's line is
    None: the caller has none). No basis, partner or duplicate rule."""
    result = _tb_model.check_rows(rows, chart, None, None, None)
    out = [p for p in result["file_problems"] if p == _tb_model.NO_CHART]
    for row in result["rows"]:
        where = f"Line {row['line']}: " if row["line"] is not None else ""
        out.extend(f"{where}{p['message']}" for p in row["problems"] if p["code"] in _ROW_RULES)
    return out


def _currency_problems(entity, functional_currency, minor_unit, rows, from_file):
    """The TB's currency rule, with the budget's own sentence for a currency
    that is not the entity's (it names the file only to a file, and a budget
    is not a trial balance)."""
    declared = [(r.get("line"), r.get("currency")) for r in rows]
    given, expected = declared_currency(declared), currency_code(functional_currency)
    if given and expected and given != expected:
        # Judged as if it matched, for the blank-row problems; then the budget's sentence.
        subject = f"The file declares {given}" if from_file else f"The currency given is {given}"
        return currency_problems(entity, given, declared) + [
            f"{subject} but Entity {entity}'s Functional Currency is {expected}: give the budget in "
            f"{expected}, or correct the Entity's Functional Currency."]
    problems = currency_problems(entity, functional_currency, declared)
    if problems:
        return problems
    currency = currency_code(functional_currency)
    if not valid_minor_unit(minor_unit):
        return [f"ISO Currency {currency} has no usable Minor Unit ({minor_unit!r}), so the decimals of "
                f"this budget cannot be judged. Set its Minor Unit to the ISO 4217 number of decimal "
                f"places for {currency}, then check again."]
    try:
        return place_problems(currency, minor_unit, rows)
    except (NotFinite, ValueError) as error:
        return [f"An amount cannot be read: {error}"]


def _basis_problems(rows, form_basis, from_file):
    """One basis per Budget Entry, from the column or the upload, never
    defaulted: Movement or Year-to-date; a Period-end balance is refused by name.
    A writer that is not a file is told what to give, not about a file."""
    given = group_basis(rows)
    form = canonical_basis(form_basis)
    if given and form and canonical_basis(given) not in (None, form):
        if from_file:
            return [f'The file says "{canonical_basis(given)}" but the upload says "{form}"; '
                    "one file holds one amount basis, so fix the upload or the file."]
        return [f'The rows say "{canonical_basis(given)}" but the amount basis given is "{form}"; '
                "one Budget Entry holds one amount basis."]
    basis, problem = resolve_basis(given, form_basis)
    if problem and not from_file and not (form_basis and str(form_basis).strip()):
        allowed = " or ".join(f'"{b}"' for b in BUDGET_BASES)
        return [f"Amount Basis is required: give the amount basis, {allowed}. It is never defaulted."]
    if problem:
        return [problem]
    if basis == PERIOD_END:
        return [PERIOD_END_REFUSAL]
    return []


def cycle_closed_to_writes(cycle):
    """Whether a Cycle, as konsol.budget_intake.lock_cycles returns it ({name, docstatus,
    workflow_state}), refuses budget writes.

    Today: an approved Cycle (docstatus 1). A cancelled Cycle does not (19-b:
    under an amended pair it is the audit trail), and a draft accepts writes.
    The seam for V2 (Q6-b, Q17-b): a draft Cycle outside the installed
    workflow's first state (Pending Approval) refuses too. V2 decides that
    here, from ``workflow_state``, which the lock has already read; every
    writer that asks this gets the rule at once. Never read a lock status
    field: the Cycle has none since S2-C W2a."""
    return int(cycle["docstatus"]) == 1
