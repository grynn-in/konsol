const SINGLES = new Set(["EPM Settings"]);

/** Match frappe.desk.utils.slug — desk URLs use lowercase hyphenated doctype. */
export function doctypeSlug(doctype) {
	return (doctype || "").toLowerCase().replace(/ /g, "-");
}

async function frappeCall(method, args = {}) {
	const payload = { ...args };
	if (window.csrf_token) {
		payload.csrf_token = window.csrf_token;
	}

	const res = await fetch(`/api/method/${method}`, {
		method: "POST",
		headers: { "Content-Type": "application/json", Accept: "application/json" },
		credentials: "include",
		body: JSON.stringify(payload),
	});
	const data = await res.json();
	if (!res.ok || data.exc) {
		let message = data.message || data._server_messages || `API error (${res.status})`;
		if (typeof message === "string" && message.startsWith("[")) {
			try {
				const parsed = JSON.parse(message);
				message = parsed.map((row) => JSON.parse(row).message).join(" ");
			} catch {
				/* keep raw */
			}
		}
		throw new Error(message);
	}
	return data.message;
}

export function getSnapshot() {
	return fetch("/api/method/konsol.control_api.get_snapshot", {
		method: "GET",
		credentials: "include",
		headers: { Accept: "application/json" },
	})
		.then(async (res) => {
			const data = await res.json();
			if (!res.ok || data.exc) {
				throw new Error(data.message || data.exception || `API error (${res.status})`);
			}
			return data.message;
		});
}

export function getRunDetail(processId, kind, runId) {
	const params = new URLSearchParams({
		process_id: processId,
		kind,
		run_id: runId,
	});
	return fetch(`/api/method/konsol.control_api.get_run_detail?${params}`, {
		method: "GET",
		credentials: "include",
		headers: { Accept: "application/json" },
	}).then(async (res) => {
		const data = await res.json();
		if (!res.ok || data.exc) {
			throw new Error(data.message || data.exception || `API error (${res.status})`);
		}
		return data.message;
	});
}

export function startProcess(processId) {
	return frappeCall("konsol.control_api.start_process", { process_id: processId });
}

export function sendReminder(owner, item) {
	return frappeCall("konsol.control_api.send_reminder", { owner, item });
}

export function openDoctype(doctype) {
	if (!doctype) return;
	window.open(`/app/${doctypeSlug(doctype)}`, "_blank");
}

export function openDoc(doctype, name) {
	if (!doctype) return;
	const slug = doctypeSlug(doctype);
	if (name) {
		window.open(`/app/${slug}/${encodeURIComponent(name)}`, "_blank");
		return;
	}
	openDoctype(doctype);
}