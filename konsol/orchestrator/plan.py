"""Run-plan resolution (PRD-2).

Turns a pipeline *definition* (a list of :class:`Step` templates) plus a run's
*parameters* (fiscal year/period, scope, full_refresh, skip_sync) into the
concrete, ordered list of steps to execute. Pure-python (no frappe).

Parameters become step params rather than special code paths:
- ``skip_sync``     drops ``airbyte_sync`` steps and rewires dependents
- ``full_refresh``  sets ``full_refresh`` on incremental dbt steps (run/build)
- ``scope``         becomes ``select`` on dbt transform steps (run/build/test)
- ``fiscal_year`` / ``fiscal_period`` become dbt ``vars`` on dbt + close steps
"""
from __future__ import annotations

import copy
from typing import Dict, List

from konsol.orchestrator.dag import Step

# step types
DBT_TYPES = {"dbt_seed", "dbt_run", "dbt_build", "dbt_test"}
DBT_TRANSFORM_TYPES = {"dbt_run", "dbt_build", "dbt_test"}
DBT_INCREMENTAL_TYPES = {"dbt_run", "dbt_build"}
VARS_TYPES = DBT_TYPES | {"close_assertions"}

# The canonical Group Close pipeline. A definition is just a list of Steps;
# P2 will let users author these as doctypes instead of this constant.
DEFAULT_DEFINITION: List[Step] = [
    Step(id="extract", type="airbyte_sync"),
    Step(id="seed", type="dbt_seed", depends_on=["extract"]),
    Step(id="silver", type="dbt_run", depends_on=["seed"]),
    Step(id="gold", type="dbt_run", depends_on=["silver"]),
    Step(id="assertions", type="close_assertions", depends_on=["gold"]),
    Step(id="signoff", type="signoff", depends_on=["assertions"]),
]


def _drop_steps(steps: List[Step], drop_ids: set) -> List[Step]:
    """Remove steps in ``drop_ids`` and strip them from remaining depends_on."""
    kept = [s for s in steps if s.id not in drop_ids]
    for s in kept:
        s.depends_on = [d for d in s.depends_on if d not in drop_ids]
    return kept


def build_plan(definition: List[Step], params: Dict) -> List[Step]:
    """Resolve a definition + run params into a concrete list of steps.

    Returns fresh :class:`Step` objects; the input definition is never mutated.
    """
    params = params or {}
    steps = [copy.deepcopy(s) for s in definition]

    if params.get("skip_sync"):
        drop = {s.id for s in steps if s.type == "airbyte_sync"}
        steps = _drop_steps(steps, drop)

    full_refresh = bool(params.get("full_refresh"))
    scope = params.get("scope")
    fiscal_year = params.get("fiscal_year")
    fiscal_period = params.get("fiscal_period")

    dbt_vars = {}
    if fiscal_year is not None:
        dbt_vars["fiscal_year"] = fiscal_year
    if fiscal_period is not None:
        dbt_vars["fiscal_period"] = fiscal_period

    for s in steps:
        if full_refresh and s.type in DBT_INCREMENTAL_TYPES:
            s.params["full_refresh"] = True
        if scope and s.type in DBT_TRANSFORM_TYPES:
            s.params["select"] = scope
        if dbt_vars and s.type in VARS_TYPES:
            s.params["vars"] = {**s.params.get("vars", {}), **dbt_vars}

    return steps
