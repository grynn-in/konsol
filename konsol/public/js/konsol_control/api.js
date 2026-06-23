export function getSnapshot() {
	return new Promise((resolve, reject) => {
		frappe.call({
			method: "konsol.control_api.get_snapshot",
			callback: (r) => resolve(r.message),
			error: (r) => reject(r),
		});
	});
}

export function startProcess(processId) {
	return new Promise((resolve, reject) => {
		frappe.call({
			method: "konsol.control_api.start_process",
			args: { process_id: processId },
			callback: (r) => resolve(r.message),
			error: (r) => reject(r),
		});
	});
}

export function sendReminder(owner, item) {
	return new Promise((resolve, reject) => {
		frappe.call({
			method: "konsol.control_api.send_reminder",
			args: { owner, item },
			callback: (r) => resolve(r.message),
			error: (r) => reject(r),
		});
	});
}

export function openDoctype(doctype) {
	if (!doctype) return;
	frappe.model.with_doctype(doctype, () => {
		const meta = frappe.get_meta(doctype);
		if (meta?.issingle) frappe.set_route("Form", doctype);
		else frappe.set_route("List", doctype);
	});
}