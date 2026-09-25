// konsol#305 B13: checks.js
//
// Pure view model for the checks screen (E7; stories 7.2, 7.4). Turns A26's
// `get_checks` payload into what the screen shows: `{banner, domains,
// canRun}`. Takes no frappe/vue import.
//
// Parallel-run override (25 Sep 2026): A26's real payload shape is
// `{latest, staleness, staleness_note, as_of, results_run, domains,
// failures, warnings, can_run}`. Each cause is `{assertion, title, status,
// rows_failed, severity, message, description, description_missing}`
// (checks_model.by_cause). A missing description is never invented text —
// the screen says "No description declared for <assertion>" (Problems 4).
// When `results_run` (the latest *terminal* run, whose steps are shown)
// differs from `latest.name` (the newest run, possibly still in flight),
// the banner says the results are from an earlier run. An unknown
// staleness state is never silently shown as current: this module throws.
//
// konsol#305 B13b: a Warn cause must never look like a pass, and a Fail
// must never look like a Warn. Each cause keeps A13's `status`
// (Fail|Error|Warn) and `severity`, plus a `label` the screen renders as
// text (never colour alone). A domain keeps A13's `count` (how many fail:
// Fail + Error) and `warn_count` (how many warn) apart, as `count` and
// `warnCount`. An unknown status throws — it is never shown as a pass.

const BANNER_TEXT = {
  stale: "Checks are older than the numbers — run them again",
  running: "Checks running…",
  not_run: "No checks run for this period",
  current: null,
};

const EARLIER_RUN_NOTE = "These results are from an earlier run.";

const STATUS_LABEL = {
  Fail: "Failed",
  Error: "Error",
  Warn: "Warning",
};

function bannerFor({ staleness, latest, results_run }) {
  if (!Object.prototype.hasOwnProperty.call(BANNER_TEXT, staleness)) {
    throw new Error(`Unknown staleness state: ${staleness}`);
  }
  const text = BANNER_TEXT[staleness];
  const earlierRun = Boolean(latest) && Boolean(results_run) && results_run !== latest.name;
  if (!earlierRun) {
    return text;
  }
  return text ? `${text} ${EARLIER_RUN_NOTE}` : EARLIER_RUN_NOTE;
}

function causeView(cause) {
  const label = STATUS_LABEL[cause.status];
  if (!label) {
    throw new Error(`Unknown cause status: ${cause.status}`);
  }
  return {
    title: cause.title,
    status: cause.status,
    label,
    severity: cause.severity,
    text: cause.description_missing
      ? `No description declared for ${cause.assertion}`
      : cause.description,
    rows: cause.rows_failed,
  };
}

function domainView(domain) {
  const causes = [...domain.failures, ...domain.warnings].map(causeView);
  return { name: domain.domain, count: domain.count, warnCount: domain.warn_count, causes };
}

/**
 * A26's `get_checks` payload → `{banner, domains:[{name, count, warnCount,
 * causes:[{title, status, label, severity, text, rows}]}], canRun}`.
 */
export function checksView(payload) {
  return {
    banner: bannerFor(payload),
    domains: (payload.domains || []).map(domainView),
    canRun: Boolean(payload.can_run),
  };
}
