/**
 * Pure helpers for the Konsol workspace shell (F7, 12 Sep 2026).
 *
 * The shell answers "where am I?" with a path (FY2026 / P09 · Sep 2026 /
 * Close) and a fiscal navigator, never a period dropdown. The period lives in
 * the URL, /konsol-exec/2026/9, so a link pasted into chat carries it.
 *
 * The server owns the calendar: only it knows which periods a fiscal year
 * declares and what each is called (`code`, `label`, from
 * konsol/home_api.py's `period_tree` and `month`). Nothing here guesses a
 * period's code, label or meaning from its number.
 */

export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Lane stages that open an existing konsol-exec step instead of the desk. */
export const STAGE_STEP = { consolidate: "consolidation", assertions: "assertions", signoff: "signoff" };

/** Period Status → frappe-ui Badge theme. */
export const PERIOD_THEME = { Open: "blue", Closed: "green", Locked: "gray" };

/**
 * Route params → {year, period} as numbers, or null when not shaped like a
 * period. A period number is 0..255, as the server declares it (a
 * 13-period year's close is 14); whether a given (year, period) actually
 * exists is for the server to say — an undeclared one is refused there.
 */
export function parsePeriodRoute(params) {
	const year = Number(params?.year);
	const period = Number(params?.period);
	if (!Number.isInteger(year) || !Number.isInteger(period)) return null;
	if (year <= 1900 || year >= 3000 || period < 0 || period > 255) return null;
	return { year, period };
}

export function monthPath(year, period) {
	return `/${year}/${period}`;
}

/** Where the app opens: this calendar month, which is period M of FY Y. */
export function currentMonthPath(date = new Date()) {
	return monthPath(date.getFullYear(), date.getMonth() + 1);
}

/** A stage is "yours" when one of your roles owns it. */
export function isMine(stage, roles) {
	const have = new Set(roles || []);
	return (stage?.owners || []).some((r) => have.has(r));
}

/** Clicking a lane stage: an existing konsol-exec step, or the desk list for it. */
export function stageTarget(stage, year, period) {
	if (STAGE_STEP[stage.id]) return { step: STAGE_STEP[stage.id] };
	const q = new URLSearchParams({ fiscal_year: String(year), fiscal_period: String(period) }).toString();
	const desk = {
		source: "/app/connector",
		trial_balances: `/app/trial-balance-submission?${q}`,
		ownership: "/app/ownership-period",
		intercompany: `/app/ic-balance?${q}`,
		adjustments: `/app/consolidation-adjustment?${q}`,
	};
	return { href: desk[stage.id] || "/app" };
}

/**
 * The period's `code`/`label` from the home tree (`period_tree`'s `years`
 * array, each with a `periods` row per declared period). A period missing
 * from the tree — not yet loaded, or genuinely undeclared — gets a crumb
 * that says so, never an invented month name and never `undefined`.
 */
export function periodCrumb(tree, year, period) {
	const yr = (tree?.years || []).find((y) => y.fiscal_year === year);
	const row = (yr?.periods || []).find((p) => p.fiscal_period === period);
	if (row) return { code: row.code, label: row.label };
	return { code: `Period ${period}`, label: "not declared" };
}

/**
 * The path in the title bar. `where` is {name, year, period, code, label,
 * stepLabel} — `code`/`label` are the server's for that period (e.g. "P09",
 * "Sep 2026", from `periodCrumb`), shown as-is; the fiscal year is not a
 * page, so it carries no link.
 */
export function crumbsFor(where) {
	const { name, year, period, code, label, stepLabel } = where || {};
	const hasPeriod = Number.isInteger(year) && Number.isInteger(period);
	const base = hasPeriod
		? [{ label: `FY${year}` }, { label: `${code} · ${label}`, to: monthPath(year, period) }]
		: [];
	if (name === "month" && hasPeriod) return [...base, { label: "Close" }];
	if ((name === "step" || name === "step-tab") && hasPeriod) return [...base, { label: stepLabel || "Step" }];
	if (name === "close" && hasPeriod) return [...base, { label: "Close checklist" }];
	if (name === "uploads") return [{ label: "Group" }, { label: "Upload trial balances" }];
	return [{ label: "Konsol" }];
}

/** Years open in the navigator by default: the current one and the selected one. */
export function defaultExpanded(tree, selected) {
	const out = new Set();
	const current = tree?.current?.fiscal_year;
	if (current) out.add(current);
	if (selected?.year) out.add(selected.year);
	return out;
}

/** Items still needing action (done rows are shown, not counted). */
export function openCount(items) {
	return (items || []).filter((i) => i.state !== "done").length;
}

/** What the detail panel shows before anything is clicked. */
export function firstOpenItem(month) {
	const all = [...(month?.mine || []), ...(month?.waiting || [])];
	return all.find((i) => i.state !== "done") || all[0] || null;
}

/**
 * The line shown with a queue action: why it is disabled, or, for an allowed
 * one, what it can't complete yet (an approval in a closed period).
 */
export function actionHint(action) {
	if (!action) return "";
	return (action.allowed ? action.note : action.reason) || "";
}

/** "2026-06-21 20:23:35.837" → "21 Jun 20:23"; anything else passes through. */
export function shortTime(value) {
	const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(value || ""));
	if (!m) return value || "";
	return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[4]}:${m[5]}`;
}
