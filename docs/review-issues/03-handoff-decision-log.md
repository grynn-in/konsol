# HANDOFF.md is a session log, not a decision log — unresolved decisions sit in changelogs

## Summary

HANDOFF.md is ~147 KB of nightly-refreshed session notes. It is valuable as archaeology, but it is not a decision record: open items and contradictions sit inline without resolution, e.g. the first-close-period question where live says FY2024 P12 while a #303 note says FY2025 P7 — with no traced decision on which is correct.

## Why it matters

- Someone onboarding (human or AI) reads HANDOFF.md as the source of truth and inherits unresolved contradictions as if they were settled.
- Decisions made in long AI-assisted sessions get buried in chronological notes instead of being captured as explicit, dated, reasoned entries. When the next session starts, it re-litigates or worse, silently picks one.
- The scar-documentation culture in this repo is genuinely good (issue numbers cited, rejected alternatives recorded). The decision-capture culture is the gap.

## Suggested direction

- Split HANDOFF.md into a short current-state summary plus a DECISIONS.md (or docs/decisions/) with one entry per decision: date, context, options considered, choice, rationale, links to issues/PRs.
- Migrate the highest-stakes open items (first close period, writeback assumptions, permission defaults) into that log with an explicit open/closed status.
- Keep HANDOFF.md for session archaeology if useful, but stop treating it as the operational source of truth.

## Acceptance criteria

- [ ] A DECISIONS.md (or equivalent) exists with at least the top 5 open items from HANDOFF.md migrated in, each with status.
- [ ] New decisions get an entry at the time they are made, not reconstructed later.
- [ ] HANDOFF.md header points readers at the decision log for anything marked open.

## Context

Found during a source review. Not a code defect — a process/documentation gap that will compound as the project grows.
