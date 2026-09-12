/**
 * Pure helpers for the bulk trial balance upload page.
 *
 * An upload's report has one row per entity-period in the file:
 * {entity, fiscal_year, fiscal_period, rows, total_debit, total_credit,
 *  errors[], ok, loaded?, load_error?}.
 */

export const TERMINAL = new Set(["Loaded", "Partly Loaded", "Failed"]);

export function summarize(report) {
	const rows = report || [];
	const ready = rows.filter((r) => r.ok).length;
	return {
		groups: rows.length,
		ready,
		problems: rows.length - ready,
		lines: rows.reduce((n, r) => n + (r.rows || 0), 0),
		entities: new Set(rows.map((r) => r.entity)).size,
		loaded: rows.filter((r) => r.loaded).length,
		failed: rows.filter((r) => r.load_error).length,
	};
}

/** One report row as a status for StatusBadge, plus what to say about it. */
export function rowStatus(r) {
	if (r.loaded) return { state: "done", label: "Loaded", note: r.loaded };
	if (r.load_error) return { state: "error", label: "Load failed", note: r.load_error };
	if (!r.ok) return { state: "error", label: "Problem", note: (r.errors || []).join(" · ") };
	return { state: "ready", label: "Ready", note: "" };
}

export function visibleRows(report, problemsOnly) {
	const rows = report || [];
	return problemsOnly ? rows.filter((r) => !r.ok || r.load_error) : rows;
}

/** The load button's words, or null when nothing can load. */
export function loadLabel(summary) {
	if (!summary || !summary.ready) return null;
	const n = `${summary.ready} trial balance${summary.ready === 1 ? "" : "s"}`;
	return summary.problems ? `Load ${n}, skip ${summary.problems}` : `Load ${n}`;
}

/** Share of the ready entity-periods done so far, 0 to 100. */
export function progress(upload) {
	const total = upload?.valid_count || 0;
	if (!total) return 0;
	return Math.min(100, Math.round((((upload.loaded_count || 0) + (upload.failed_count || 0)) / total) * 100));
}

export function periodText(r) {
	return `FY${r.fiscal_year} P${String(r.fiscal_period).padStart(2, "0")}`;
}

export function money(n) {
	return Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
