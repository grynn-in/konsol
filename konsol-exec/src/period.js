/**
 * Period arithmetic for the close spine.
 *
 * A period is one thing — a fiscal period inside a fiscal year — so it gets one
 * control, and stepping forwards or backwards has to roll over the year
 * boundary. That is real logic, so it lives here as pure functions with tests
 * rather than inside a component.
 *
 * Both lists come from `orchestrator.api.launch_options`: `fiscal_years` is an
 * ordered list of year strings, `fiscal_periods` an ordered list of
 * `{value, label}`. Neither is assumed to be calendar months — a fiscal
 * calendar may have 12, 13 or any number of periods, and their labels are the
 * backend's vocabulary, not ours.
 */

function years(options) {
	return (options?.fiscal_years || []).map(String);
}

/** `fiscal_years` oldest-first, regardless of the order the server sent it
 *  in — the server (`orchestrator.api.launch_options`) actually sends it
 *  newest-first, and nothing here may assume a position means an age. */
function chronologicalYears(options) {
	return years(options)
		.slice()
		.sort((a, b) => Number(a) - Number(b));
}

function periods(options) {
	return (options?.fiscal_periods || []).map((p) => ({ ...p, value: String(p.value) }));
}

/**
 * A real accounting period, as opposed to an adjustment period.
 *
 * A fiscal calendar carries more than the periods you close: there is an
 * opening period (type Opening) and closing/adjustment periods (type Closing
 * or Adjustment) that exist to hold brought-forward balances and year-end
 * journals. They are selectable — a controller does sometimes need to run
 * against CLS — but they are never what "close September" means, so they
 * must not be the default.
 *
 * The declared `period_type` decides this, never the period number: a
 * calendar isn't guaranteed to stop at 12, and a period numbered 13 can be a
 * real Regular period on some calendars. `type` is the field name the
 * backend sends for each period (`orchestrator.api.launch_options`,
 * `home_api.period_tree`), sourced from EPM Fiscal Year Period's
 * `period_type`.
 */
export function isAccountingPeriod(p) {
	return p?.type === "Regular";
}

/** Display label, e.g. "Sep FY2026". Falls back to the year alone when a whole
 *  year is selected, which is a legitimate scope for some processes. */
export function formatPeriod(period, options) {
	if (!period?.year) return "";
	const match = periods(options).find((p) => p.value === String(period.period));
	return match ? `${match.label} FY${period.year}` : `FY${period.year}`;
}

/**
 * Move `delta` periods forward (+1) or back (-1), rolling into the adjacent
 * fiscal year at the ends. Returns null when the move would leave the range the
 * backend reported, so the caller can disable the control rather than silently
 * clamping — clamping makes a dead button look alive.
 */
export function stepPeriod(period, options, delta) {
	const ys = chronologicalYears(options);
	const ps = periods(options);
	if (!period?.year || !ys.length || !ps.length) return null;

	const yi = ys.indexOf(String(period.year));
	const pi = ps.findIndex((p) => p.value === String(period.period));
	if (yi === -1 || pi === -1) return null;

	const next = pi + delta;

	if (next >= 0 && next < ps.length) {
		return { year: period.year, period: ps[next].value };
	}
	// rolled off the end of the year — step the year, wrap the period
	const nextYear = yi + delta;
	if (nextYear < 0 || nextYear >= ys.length) return null;
	return {
		year: ys[nextYear],
		period: delta > 0 ? ps[0].value : ps[ps.length - 1].value,
	};
}

/** True when `stepPeriod` in that direction would land somewhere real. */
export function canStep(period, options, delta) {
	return stepPeriod(period, options, delta) !== null;
}

/** The years offered by the picker, newest first — finance looks backwards far
 *  more often than forwards. */
export function yearChoices(options) {
	return chronologicalYears(options).reverse();
}

export function periodChoices(options) {
	return periods(options);
}

/** The twelve you actually close. */
export function accountingPeriods(options) {
	return periods(options).filter(isAccountingPeriod);
}

/** OPN / CLS and anything else outside 1..12 — shown apart, not hidden. */
export function adjustmentPeriods(options) {
	return periods(options).filter((p) => !isAccountingPeriod(p));
}
