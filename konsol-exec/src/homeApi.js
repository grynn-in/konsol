/** konsol.home_api: three read-only GET endpoints for the workspace shell. */

async function get(method, params) {
	const qs = params ? `?${new URLSearchParams(params)}` : "";
	const res = await fetch(`/api/method/${method}${qs}`, {
		method: "GET",
		credentials: "include",
		headers: { Accept: "application/json" },
	});
	const data = await res.json().catch(() => ({}));
	if (!res.ok || data.exc) {
		throw new Error(data.message || data.exception || `API error (${res.status})`);
	}
	return data.message;
}

/** Roles, job titles and entity scope of the session user. */
export const whoami = () => get("konsol.home_api.whoami");

/** Fiscal years with their fourteen periods and close state. */
export const periodTree = () => get("konsol.home_api.period_tree");

/** One period: eight stages, the viewer's queue, and system health. */
export const getMonth = (year, period) =>
	get("konsol.home_api.month", { fiscal_year: String(year), fiscal_period: String(period) });
