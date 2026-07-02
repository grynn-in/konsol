"""Definition->plan loader (PRD-13).

Turns a PRD-12 **Pipeline** doctype payload (a plain dict — e.g. the
result of ``frappe.get_doc("Pipeline", name).as_dict()`` or a record
from the ``pipeline.json`` seed fixture) into ``dag.Step`` objects so
``plan.build_plan`` can consume user-authored definitions instead of the
hardcoded ``DEFAULT_DEFINITION`` constant.

Pure-python at module level (no top-level ``frappe`` import) so it imports and
unit-tests on host pytest. ``load_definition`` does the frappe lookup with a
**function-local** ``import frappe`` and then delegates to the pure converter.
"""
from __future__ import annotations

import json
from typing import Dict, List

from konsol.orchestrator.dag import Step


def _parse_depends_on(raw) -> List[str]:
    """Parse the comma-separated ``depends_on`` field into a list of step ids.

    Matches the Group Close fixture format: ``""`` -> ``[]``, ``"a, b"`` ->
    ``["a", "b"]`` (each id stripped, empties dropped). A list is passed through.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(d).strip() for d in raw if str(d).strip()]
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _parse_params(raw, step_id: str) -> Dict:
    """Parse the ``params`` field into a dict.

    ``None`` / ``""`` -> ``{}``; a dict passes through; a JSON string is decoded.
    A malformed JSON string raises a clear ``ValueError`` naming the step.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"invalid JSON in params for step {step_id!r}: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"params for step {step_id!r} must decode to an object, got {type(parsed).__name__}"
        )
    return parsed


def definition_to_steps(defn: Dict) -> List[Step]:
    """Convert a Pipeline payload into ordered :class:`Step` objects.

    Iterates ``defn["steps"]`` preserving order; parses each row's ``depends_on``
    (comma-split) and ``params`` (JSON string or dict) into a ``Step``. Pure.
    """
    steps: List[Step] = []
    for row in (defn or {}).get("steps", []) or []:
        step_id = row.get("step_id")
        steps.append(
            Step(
                id=step_id,
                type=row.get("step_type"),
                depends_on=_parse_depends_on(row.get("depends_on")),
                params=_parse_params(row.get("params"), step_id),
            )
        )
    return steps


def load_definition(name: str) -> List[Step]:
    """Load a Pipeline by name and convert it to ``Step`` objects.

    Frappe-bound: imports frappe locally so the module stays host-importable.
    """
    import frappe  # noqa: PLC0415 — function-local to keep module frappe-free

    doc = frappe.get_doc("Pipeline", name)
    return definition_to_steps(doc.as_dict())
