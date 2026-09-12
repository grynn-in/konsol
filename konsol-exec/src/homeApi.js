/** konsol.home_api: three read-only GET endpoints for the workspace shell. */

/**
 * An Error that says what the server said. Frappe puts a thrown message in
 * `_server_messages` (a JSON list of JSON objects); `exception` is only sent
 * when tracebacks are allowed, prefixed with the class name. The status and
 * exception type ride along, so callers decide by type, not by wording.
 */
export function errorFrom(status, data) {
	let message = "";
	try {
		const list = JSON.parse(data?._server_messages || "[]");
		message = list.map((m) => { try { return JSON.parse(m).message; } catch { return m; } }).filter(Boolean).join(" ");
	} catch { /* not JSON: fall through */ }
	if (!message && typeof data?.message === "string") message = data.message;
	if (!message && data?.exception) message = String(data.exception).replace(/^[\w.]+:\s*/, "");
	const err = new Error(message || `API error (${status})`);
	err.status = status;
	err.excType = data?.exc_type || null;
	return err;
}

/** A missing role is not something Retry can fix. */
export function isNoAccess(err) {
	return Boolean(err) && (err.status === 403 || err.excType === "PermissionError");
}

async function get(method, params) {
	const qs = params ? `?${new URLSearchParams(params)}` : "";
	const res = await fetch(`/api/method/${method}${qs}`, {
		method: "GET",
		credentials: "include",
		headers: { Accept: "application/json" },
	});
	const data = await res.json().catch(() => ({}));
	if (!res.ok || data.exc) throw errorFrom(res.status, data);
	return data.message;
}

/** Roles, job titles and entity scope of the session user. */
export const whoami = () => get("konsol.home_api.whoami");

/** Fiscal years with their fourteen periods and close state. */
export const periodTree = () => get("konsol.home_api.period_tree");

/** One period: eight stages, the viewer's queue, and system health. */
export const getMonth = (year, period) =>
	get("konsol.home_api.month", { fiscal_year: String(year), fiscal_period: String(period) });
