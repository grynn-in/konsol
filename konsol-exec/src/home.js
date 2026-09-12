/**
 * Pure helpers for the Konsol workspace shell (F7, 12 Sep 2026).
 *
 * The shell answers "where am I?" with a path (FY2026 / P09 · Sep 2026 /
 * Close) and a fiscal navigator, never a period dropdown. The period lives in
 * the URL, /konsol-exec/2026/9, so a link pasted into chat carries it.
 *
 * Period vocabulary mirrors konsol/home_model.py: OPN is period 0, CLS is 13,
 * and period P of FY Y is the month starting Y-P-01.
 */

export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Lane stages that open an existing konsol-exec step instead of the desk. */
export const STAGE_STEP = { consolidate: "consolidation", assertions: "assertions", signoff: "signoff" };

/** Period Status → frappe-ui Badge theme. */
export const PERIOD_THEME = { Open: "blue", Closed: "green", Locked: "gray" };

export function periodCode(p) {
	if (p === 0) return "OPN";
	if (p === 13) return "CLS";
	return `P${String(p).padStart(2, "0")}`;
}

export function periodLabel(year, p) {
	if (p === 0) return "Opening balances";
	if (p === 13) return "Year-end close";
	return `${MONTHS[p - 1]} ${year}`;
}

/** Route params → {year, period} as numbers, or null when not a real period. */
export function parsePeriodRoute(params) {
	const year = Number(params?.year);
	const period = Number(params?.period);
	if (!Number.isInteger(year) || !Number.isInteger(period)) return null;
	if (year <= 1900 || year >= 3000 || period < 0 || period > 13) return null;
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
 * The path in the title bar. `where` is {name, year, period, stepLabel}; the
 * fiscal year is not a page, so it carries no link.
 */
export function crumbsFor(where) {
	const { name, year, period, stepLabel } = where || {};
	const hasPeriod = Number.isInteger(year) && Number.isInteger(period);
	const base = hasPeriod
		? [{ label: `FY${year}` }, { label: `${periodCode(period)} · ${periodLabel(year, period)}`, to: monthPath(year, period) }]
		: [];
	if (name === "month" && hasPeriod) return [...base, { label: "Close" }];
	if ((name === "step" || name === "step-tab") && hasPeriod) return [...base, { label: stepLabel || "Step" }];
	if (name === "close" && hasPeriod) return [...base, { label: "Close checklist" }];
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

/** "2026-06-21 20:23:35.837" → "21 Jun 20:23"; anything else passes through. */
export function shortTime(value) {
	const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(value || ""));
	if (!m) return value || "";
	return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[4]}:${m[5]}`;
}
