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

/** The URL a GET of `method` with `params` calls (blank params left out);
 * `get` and `download` both call it. */
export function methodUrl(method, params) {
	return `/api/method/${method}${buildQuery(params)}`;
}

/** GET `konsol.close.<...>`. Never writes; a query string carries params. */
export function get(method, params, { fetchImpl = fetch } = {}) {
	const url = methodUrl(method, params);
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

/** The file name in a `Content-Disposition` header (`filename="…"` or a
 * bare `filename=…`), or null. */
function dispositionFilename(header) {
	if (!header) return null;
	const m = header.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
	if (!m) return null;
	// A bare name may carry a literal "%" that is not an escape: decoding
	// it would throw URIError, so such a name is kept as the server sent it.
	try {
		return decodeURIComponent(m[1]);
	} catch {
		return m[1];
	}
}

/**
 * GET a file from `konsol.close.<...>` (konsol#305 8.5): `{blob, filename}`.
 * A refusal (a non-2xx reply) throws the server's own sentence, read from
 * its JSON body like `get`'s — never a file. A reply with no file name
 * throws too: the name is the server's, never made up here.
 */
export async function download(method, params, { fetchImpl = fetch } = {}) {
	const url = methodUrl(method, params);
	let res;
	try {
		res = await fetchImpl(url, { method: "GET", credentials: "include" });
	} catch (err) {
		throw new Error(`Network error: ${err.message}`);
	}
	if (!res.ok) {
		throw new Error(errorMessage(await parseJsonBody(res), res.status));
	}
	const filename = dispositionFilename(res.headers.get("Content-Disposition"));
	if (!filename) {
		throw new Error(`The reply to ${method} carried no file name.`);
	}
	return { blob: await res.blob(), filename };
}
