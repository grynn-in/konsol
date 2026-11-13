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
"""
from collections import namedtuple

from konsol.tb_basis_model import COLUMN as BASIS
from konsol.tb_bulk_model import (
    ALIASES, MAX_LINE_ERRORS, PARTNER, REQUIRED as TB_REQUIRED, Contract,
    group_basis, resolve_basis, split_by_contract, table_from_csv,
)

__all__ = ("ALIASES", "MAX_LINE_ERRORS", "BudgetKey", "REQUIRED", "OPTIONAL", "ACCEPTED", "HEADER_HELP",
           "DIMENSION_FLAG", "PARTNER_REFUSAL", "CONTRACT", "split_table", "table_from_csv",
           "group_basis", "resolve_basis")

SCENARIO = "scenario"
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
