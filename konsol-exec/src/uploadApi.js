/** Bulk trial balance upload: konsol.tb_bulk endpoints and Frappe's file upload. */
import { errorFrom } from "./homeApi.js";

async function parse(res) {
	const data = await res.json().catch(() => ({}));
	if (!res.ok || data.exc) throw errorFrom(res.status, data);
	return data.message;
}

function csrfHeaders() {
	return window.csrf_token ? { "X-Frappe-CSRF-Token": window.csrf_token } : {};
}

async function post(method, args) {
	const res = await fetch(`/api/method/${method}`, {
		method: "POST",
		credentials: "include",
		headers: { "Content-Type": "application/json", Accept: "application/json", ...csrfHeaders() },
		body: JSON.stringify(args || {}),
	});
	return parse(res);
}

async function get(method, params) {
	const qs = params ? `?${new URLSearchParams(params)}` : "";
	const res = await fetch(`/api/method/${method}${qs}`, { credentials: "include", headers: { Accept: "application/json" } });
	return parse(res);
}

/** Upload a browser File as a private Frappe File; resolves to its file_url. */
export async function uploadFile(file) {
	const form = new FormData();
	form.append("file", file, file.name);
	form.append("is_private", "1");
	const res = await fetch("/api/method/upload_file", {
		method: "POST",
		credentials: "include",
		headers: { Accept: "application/json", ...csrfHeaders() },
		body: form,
	});
	const doc = await parse(res);
	return doc.file_url;
}

export const checkFile = (fileUrl) => post("konsol.tb_bulk.check_file", { file_url: fileUrl });
export const loadUpload = (name, skipInvalid) => post("konsol.tb_bulk.load", { name, skip_invalid: skipInvalid ? 1 : 0 });
export const getUpload = (name) => get("konsol.tb_bulk.get_upload", { name });
export const recentUploads = () => get("konsol.tb_bulk.recent_uploads");
