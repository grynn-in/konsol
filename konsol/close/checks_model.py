"""Check failures by domain and cause, and the stale flag. Pure (konsol#305 A13; stories 7.2, 7.4).

Imports nothing from frappe or konsol; the caller passes every input:

- ``steps``: Assertion Step rows as dicts with ``assertion``, ``dimension``,
  ``status`` (Pass|Fail|Warn|Error), ``rows_failed``, ``severity`` and
  ``message`` (written by assertion_run._parse_results).
- ``descriptions``: ``{assertion_name: text}``, declared in the dbt project.
- ``run``: the latest Assertion Run as a dict with ``name``, ``status`` and
  ``completed_at``, or None.
- ``as_of``: when the consolidated numbers were last built
  (freshness_model ``as_of``), or None when they never were.

Rules:

- Pass steps are left out. Domains follow ``DOMAINS`` (the order of
  assertion_run._classify); a domain with nothing to show is not listed.
- One cause per assertion. Fail and Error are ``failures``; Warn is kept apart
  in ``warnings``.
- A missing or blank description is ``None`` with ``description_missing``
  True. No text is invented (konsol#305 P4); the screen prints
  "No description declared for <name>".
- Nothing is dropped silently: an unknown domain or status, the same
  assertion twice, or a finished run with no ``completed_at`` raises
  ValueError.
"""

DOMAINS = ("FX", "Ownership", "Consolidation", "Data Quality", "Other")
PASS = "Pass"
WARN = "Warn"
FAILING = ("Fail", "Error")
STATUSES = (PASS, WARN) + FAILING
IN_FLIGHT = ("Queued", "Running")


def _title(name):
    base = name[len("assert_"):] if name.startswith("assert_") else name
    text = base.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _cause(step, descriptions):
    name = step["assertion"]
    text = descriptions.get(name)
    text = text.strip() if isinstance(text, str) else None
    return {
        "assertion": name,
        "title": _title(name),
        "status": step["status"],
        "rows_failed": step.get("rows_failed") or 0,
        "severity": step.get("severity"),
        "message": step.get("message") or "",
        "description": text or None,
        "description_missing": not text,
    }


def by_cause(steps, descriptions):
    seen = set()
    grouped = {d: {"failures": [], "warnings": []} for d in DOMAINS}
    for step in steps:
        name = step["assertion"]
        status = step["status"]
        domain = step["dimension"]
        if status not in STATUSES:
            raise ValueError(f"{name} has status {status}; expected one of {', '.join(STATUSES)}.")
        if domain not in grouped:
            raise ValueError(
                f"{name} is in domain {domain}; expected one of {', '.join(DOMAINS)} "
                "(assertion_run._classify)."
            )
        if name in seen:
            raise ValueError(f"{name} appears twice in one run; one cause per assertion.")
        seen.add(name)
        if status == PASS:
            continue
        key = "warnings" if status == WARN else "failures"
        grouped[domain][key].append(_cause(step, descriptions))

    domains = []
    for d in DOMAINS:
        failures = sorted(grouped[d]["failures"], key=lambda c: c["assertion"])
        warnings = sorted(grouped[d]["warnings"], key=lambda c: c["assertion"])
        if not failures and not warnings:
            continue
        domains.append({
            "domain": d,
            "count": len(failures),
            "warn_count": len(warnings),
            "failures": failures,
            "warnings": warnings,
        })
    return {
        "domains": domains,
        "failures": sum(d["count"] for d in domains),
        "warnings": sum(d["warn_count"] for d in domains),
    }


def staleness(run, as_of):
    if run is None:
        return {"state": "not_run", "note": None}
    if run.get("status") in IN_FLIGHT:
        return {"state": "running", "note": None}
    completed_at = run.get("completed_at")
    if completed_at is None:
        raise ValueError(
            f"Assertion Run {run.get('name')} is {run.get('status')} but has no completed_at; "
            "set it before staleness can be judged."
        )
    if as_of is None:
        return {"state": "current", "note": "numbers never built"}
    if completed_at < as_of:
        return {"state": "stale", "note": None}
    return {"state": "current", "note": None}
