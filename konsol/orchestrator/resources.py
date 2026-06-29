"""Resource resolver — Airbyte / dbt / ClickHouse (PRD-17).

The orchestrator's steps need to know *where* to run: which Airbyte
workspace/connection/destination to trigger, which dbt project dir to invoke,
which ClickHouse host/db to read. This module resolves those connection configs
from two sources, **run params first, then EPM-Settings defaults**, validating
the required keys per kind and raising a precise error when any are missing.

The :func:`resolve` core is **pure-python** (no frappe at top level) so it
unit-tests on host pytest. The :func:`airbyte_resource` / :func:`dbt_resource` /
:func:`clickhouse_resource` getters and the read-only :func:`list_resources`
whitelisted API read the existing ``EPM Settings`` single + ``Connector``
doctype (function-local ``import frappe``) and are guarded in tests with
``importorskip``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:  # frappe only exists inside a bench; host pytest must still import this module
    import frappe

    whitelist = frappe.whitelist
except Exception:  # pragma: no cover - host import path (no bench)

    def whitelist(*dargs, **dkwargs):
        def deco(fn):
            return fn

        return deco


# Required config keys per resource kind. Resolution validates that every one of
# these is present (in params or settings) and non-empty.
REQUIRED_KEYS: Dict[str, tuple] = {
    "airbyte": ("workspace_id", "connection_id", "destination_id"),
    "dbt": ("project_dir",),
    "clickhouse": ("host", "db"),
}


@dataclass
class Resource:
    """A resolved connection target for one orchestrator concern."""

    kind: str
    name: str
    config: Dict = field(default_factory=dict)


def _pick(key: str, params: Dict, settings: Dict):
    """Return the value for ``key`` with params > settings precedence.

    Empty strings count as absent so a blank EPM-Settings field falls through.
    """
    val = params.get(key)
    if val in (None, ""):
        val = settings.get(key)
    if val in (None, ""):
        return None
    return val


def resolve(kind: str, params: Optional[Dict], settings: Optional[Dict]) -> Resource:
    """Resolve a :class:`Resource` for ``kind`` from ``params`` then ``settings``.

    Run ``params`` override the ``settings`` (EPM-Settings) defaults for the same
    key. Every key in :data:`REQUIRED_KEYS` for ``kind`` must resolve to a
    non-empty value, else a ``ValueError`` naming the kind and the missing
    key(s) is raised.
    """
    params = params or {}
    settings = settings or {}
    if kind not in REQUIRED_KEYS:
        raise ValueError(
            f"unknown resource kind: {kind!r} (expected one of "
            f"{', '.join(sorted(REQUIRED_KEYS))})"
        )

    config: Dict = {}
    missing: List[str] = []
    for key in REQUIRED_KEYS[kind]:
        val = _pick(key, params, settings)
        if val is None:
            missing.append(key)
        else:
            config[key] = val
    if missing:
        raise ValueError(
            f"{kind} resource missing required config: {', '.join(missing)}"
        )

    name = params.get("name") or settings.get("name") or kind
    return Resource(kind=kind, name=name, config=config)


# ---- frappe-bound getters (function-local import frappe) --------------------

def _epm_settings() -> Dict:
    """Read the connection defaults off the ``EPM Settings`` single.

    Returns a flat ``settings`` dict keyed by the resolver's required keys. Done
    here (not at module top) so the pure core imports on host without frappe.
    """
    import frappe

    s = frappe.get_single("EPM Settings")
    return {
        # airbyte
        "workspace_id": getattr(s, "airbyte_workspace_id", None),
        "connection_id": getattr(s, "airbyte_connection_id", None),
        "destination_id": getattr(s, "airbyte_destination_id", None),
        # dbt
        "project_dir": getattr(s, "dbt_project_path", None),
        # clickhouse
        "host": getattr(s, "clickhouse_host", None),
        "db": getattr(s, "airbyte_clickhouse_database", None),
    }


def airbyte_resource(params: Optional[Dict] = None) -> Resource:
    """Resolve the Airbyte resource from run params + EPM Settings defaults."""
    return resolve("airbyte", params or {}, _epm_settings())


def dbt_resource(params: Optional[Dict] = None) -> Resource:
    """Resolve the dbt resource from run params + EPM Settings defaults."""
    return resolve("dbt", params or {}, _epm_settings())


def clickhouse_resource(params: Optional[Dict] = None) -> Resource:
    """Resolve the ClickHouse resource from run params + EPM Settings defaults."""
    return resolve("clickhouse", params or {}, _epm_settings())


@whitelist()
def list_resources(params=None) -> List[Dict]:
    """Read-only: list the resolvable resources for the SPA.

    Returns one dict per kind ``{kind, name, config, error}``. A kind that fails
    to resolve (missing config) is reported with ``error`` set rather than
    raising, so the konsol-exec UI can surface what still needs configuring.
    """
    import frappe

    if isinstance(params, str):
        params = frappe.parse_json(params) or {}
    params = params or {}

    settings = _epm_settings()
    out: List[Dict] = []
    for kind in ("airbyte", "dbt", "clickhouse"):
        try:
            r = resolve(kind, params, settings)
            out.append({"kind": r.kind, "name": r.name, "config": r.config, "error": None})
        except ValueError as exc:
            out.append({"kind": kind, "name": kind, "config": {}, "error": str(exc)})
    return out
