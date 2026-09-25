// konsol#305 B06: api.js
//
// Pure API client for konsol/close/*_api.py. Mirrors
// konsol-exec/src/api.js:8-34 (CSRF in the POST body from window.csrf_token,
// credentials:"include") and konsol-exec/src/homeApi.js's error parsing
// (_server_messages is a JSON string of JSON-encoded message objects).
//
// `fetchImpl` is always injectable so tests never mock the global fetch or
// window. Every failure surfaces the server's own message: a Frappe error
// body, a non-JSON body, or a network error is never swallowed or replaced
// by a generic "Something went wrong".

function buildQuery(params) {
	if (!params) return "";
	const usp = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value === undefined || value === null) continue;
		usp.set(key, String(value));
	}
	const qs = usp.toString();
	return qs ? `?${qs}` : "";
}

/**
 * The message an API response carries. `payload` is the parsed JSON body,
 * or null/undefined when the body did not parse as JSON. Frappe puts a
 * thrown message in `_server_messages`, a JSON string of a JSON list of
 * JSON-encoded `{message: ...}` objects; `message` or `exc`/`exception`
 * carry it otherwise. When nothing usable is found, names the HTTP status
 * rather than inventing text.
 */
export function errorMessage(payload, status) {
	if (payload && typeof payload === "object") {
		if (typeof payload._server_messages === "string") {
			try {
				const list = JSON.parse(payload._server_messages);
				const joined = list
					.map((m) => {
						try {
							return JSON.parse(m).message;
						} catch {
							return m;
						}
					})
					.filter(Boolean)
					.join(" ");
				if (joined) return joined;
			} catch {
				/* _server_messages was not JSON: fall through */
			}
		}
		if (typeof payload.message === "string" && payload.message) {
			return payload.message;
		}
		if (payload.exception) {
			return String(payload.exception).replace(/^[\w.]+:\s*/, "");
		}
		if (typeof payload.exc === "string" && payload.exc) {
			return payload.exc;
		}
	}
	return `Unexpected response (${status})`;
}

async function parseJsonBody(res) {
	try {
		return await res.json();
	} catch {
		return null;
	}
}

async function request(method, url, opts, fetchImpl) {
	let res;
	try {
		res = await fetchImpl(url, opts);
	} catch (err) {
		throw new Error(`Network error: ${err.message}`);
	}
	const data = await parseJsonBody(res);
	if (!res.ok || (data && data.exc)) {
		throw new Error(errorMessage(data, res.status));
	}
	return data ? data.message : undefined;
}

/** GET `konsol.close.<...>`. Never writes; a query string carries params. */
export function get(method, params, { fetchImpl = fetch } = {}) {
	const url = `/api/method/${method}${buildQuery(params)}`;
	return request("GET", url, {
		method: "GET",
		credentials: "include",
		headers: { Accept: "application/json" },
	}, fetchImpl);
}

/** POST `konsol.close.<...>` with a JSON body, CSRF-protected. */
export function post(method, body, { fetchImpl = fetch } = {}) {
	const payload = { ...(body || {}) };
	if (typeof window !== "undefined" && window.csrf_token) {
		payload.csrf_token = window.csrf_token;
	}
	const url = `/api/method/${method}`;
	return request("POST", url, {
		method: "POST",
		credentials: "include",
		headers: { "Content-Type": "application/json", Accept: "application/json" },
		body: JSON.stringify(payload),
	}, fetchImpl);
}
