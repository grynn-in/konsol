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

// --- orchestrator exec plane (PRD-10 whitelisted API) --------------------

/** Create + enqueue a Pipeline Run; resolves to the new run name. */
export function startRun(definition, params) {
	return frappeCall("konsol.orchestrator.api.start_run", { definition, params });
}

/** Fetch a run snapshot `{name, status, steps:[...]}` (normalise via runModel). */
export function getRun(name) {
	return frappeCall("konsol.orchestrator.api.get_run", { run_name: name });
}

/** Re-arm a failed step (and its descendants) and re-enqueue the run. */
export function retryStep(name, stepId) {
	return frappeCall("konsol.orchestrator.api.retry_step", {
		run_name: name,
		step_id: stepId,
	});
}

/** Restart a finished run from `stepId` downward. */
export function resumeRun(name, stepId) {
	return frappeCall("konsol.orchestrator.api.resume_run", {
		run_name: name,
		step_id: stepId,
	});
}

/** Request cancellation of a run. */
export function cancelRun(name) {
	return frappeCall("konsol.orchestrator.api.cancel_run", { run_name: name });
}

/**
 * Option lists for the launch form's dropdowns:
 * `{definitions:[name], fiscal_years:[yr], fiscal_periods:[{value,label}],
 * scopes:[{value,label}]}`. Lets the 4 launch fields be selects, not free text.
 */
export function getLaunchOptions() {
	return frappeCall("konsol.orchestrator.api.launch_options");
}

/**
 * Subscribe to live per-step updates. Returns an unsubscribe fn (no-op when
 * the realtime layer is absent, e.g. outside a Frappe session).
 */
export function onRunStep(cb) {
	const realtime = typeof frappe !== "undefined" ? frappe.realtime : undefined;
	realtime?.on?.("orchestrator_step", cb);
	return () => realtime?.off?.("orchestrator_step", cb);
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