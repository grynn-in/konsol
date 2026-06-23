const SINGLES = new Set(["EPM Settings"]);

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

export function startProcess(processId) {
	return frappeCall("konsol.control_api.start_process", { process_id: processId });
}

export function sendReminder(owner, item) {
	return frappeCall("konsol.control_api.send_reminder", { owner, item });
}

export function openDoctype(doctype) {
	if (!doctype) return;
	const route = SINGLES.has(doctype) ? "Form" : "List";
	window.open(`/app/${route}/${encodeURIComponent(doctype)}`, "_blank");
}