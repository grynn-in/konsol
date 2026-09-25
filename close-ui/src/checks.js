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

const BANNER_TEXT = {
  stale: "Checks are older than the numbers — run them again",
  running: "Checks running…",
  not_run: "No checks run for this period",
  current: null,
};

const EARLIER_RUN_NOTE = "These results are from an earlier run.";

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
  return {
    title: cause.title,
    text: cause.description_missing
      ? `No description declared for ${cause.assertion}`
      : cause.description,
    rows: cause.rows_failed,
  };
}

function domainView(domain) {
  const causes = [...domain.failures, ...domain.warnings].map(causeView);
  return { name: domain.domain, count: causes.length, causes };
}

/**
 * A26's `get_checks` payload → `{banner, domains:[{name, count,
 * causes:[{title, text, rows}]}], canRun}`.
 */
export function checksView(payload) {
  return {
    banner: bannerFor(payload),
    domains: (payload.domains || []).map(domainView),
    canRun: Boolean(payload.can_run),
  };
}
