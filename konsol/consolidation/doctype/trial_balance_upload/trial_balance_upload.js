// Trial Balance Upload — pre-fill Amount Basis from the site default (konsolidat#199)
//
// The field itself has no default: a guessed basis double-counts or halves
// every balance downstream. EPM Settings may hold a site-wide Default Amount
// Basis; it is copied onto a NEW upload only, where the user sees it and can
// change it before checking the file. Existing uploads are never touched, and
// an entity-period whose rows carry their own amount_basis column keeps that.
//
// konsol#332-2-D1 (Deepak Pai 12 Oct): the indicator is the upload as it
// stands now, derived by the server from the submissions that are still
// submitted (`status_now`, sent by the controller's onload). Frappe's own
// indicator is the stored "Job status", which still reads Loaded after every
// submission was cancelled. The stored field is never written here.
//
// Check file and Load (5 Nov 2026, konsol/tests/test_tb_upload_desk.py): the
// Desk form drives the same check and load as the Close app. Check file posts
// tb_bulk.check_upload; Load, offered only once the upload is Checked, posts
// tb_bulk.load, which re-checks and queues the load job.
function konsol_tb_report_html(out) {
	const report = out.report || [];
	const bad = report.filter((r) => !r.ok);
	const warned = report.filter((r) => r.ok && (r.warnings || []).length);
	const esc = frappe.utils.escape_html;
	const label = (r) => esc(`${r.entity} FY${r.fiscal_year} P${r.fiscal_period}`);
	let html = `<p>${__("{0} entity-periods, {1} rows: {2} ready, {3} with problems.", [
		report.length, out.total_rows || 0, report.length - bad.length, bad.length,
	])}</p>`;
	if (bad.length) {
		html += `<p><b>${__("Problems")}</b></p><ul>` +
			bad.map((r) => `<li>${label(r)}: ${esc((r.errors || []).join("; "))}</li>`).join("") + "</ul>";
	}
	if (warned.length) {
		html += `<p><b>${__("Warnings (these still load)")}</b></p><ul>` +
			warned.map((r) => `<li>${label(r)}: ${esc(r.warnings.join("; "))}</li>`).join("") + "</ul>";
	}
	// konsol#371-7: after a load the report also says what was loaded and
	// what failed to load (tb_bulk's `loaded` and `load_error` per row).
	const loaded = report.filter((r) => r.loaded);
	const failed = report.filter((r) => r.load_error);
	if (loaded.length) {
		html += `<p><b>${__("Loaded")}</b></p><ul>` +
			loaded.map((r) => `<li>${label(r)}: ${esc(r.loaded)}</li>`).join("") + "</ul>";
	}
	if (failed.length) {
		html += `<p><b>${__("Failed to load")}</b></p><ul>` +
			failed.map((r) => `<li>${label(r)}: ${esc(r.load_error)}</li>`).join("") + "</ul>";
	}
	return html;
}

// konsol#371-7: the stored report is JSON for the code; people read it
// rendered. The raw field is hidden on the form.
function konsol_tb_render_report(frm) {
	const field = frm.get_field("report_html");
	if (!field) return;
	let report = [];
	try {
		report = JSON.parse(frm.doc.report || "[]");
	} catch (e) {
		field.$wrapper.html(`<p>${__("The stored report could not be read.")}</p>`);
		return;
	}
	field.$wrapper.html(report.length ? konsol_tb_report_html({ report, total_rows: frm.doc.total_rows }) : "");
}

function konsol_tb_check(frm) {
	frappe.call({
		method: "konsol.tb_bulk.check_upload",
		type: "POST",
		args: { name: frm.doc.name, amount_basis: frm.doc.amount_basis || null },
		freeze: true,
		freeze_message: __("Checking the file…"),
		callback(r) {
			const out = r.message || {};
			frappe.msgprint({
				title: out.error ? __("The file could not be read") : __("Check result"),
				message: out.error ? frappe.utils.escape_html(out.error) : konsol_tb_report_html(out),
				indicator: out.error ? "red" : (out.valid_count === out.group_count ? "green" : "orange"),
			});
			frm.reload_doc();
		},
	});
}

function konsol_tb_load(frm, skip_invalid) {
	frappe.call({
		method: "konsol.tb_bulk.load",
		type: "POST",
		args: { name: frm.doc.name, skip_invalid: skip_invalid ? 1 : 0 },
		freeze: true,
		freeze_message: __("Starting the load…"),
		callback(r) {
			const out = r.message || {};
			if (out.refused && out.refused_kind !== "problems") {
				// Skipping cannot help: nothing is left to load, or the file is unreadable.
				frappe.msgprint({ title: __("Nothing loaded"), message: frappe.utils.escape_html(out.refused), indicator: "orange" });
			} else if (out.refused_kind === "problems") {
				frappe.confirm(
					`${frappe.utils.escape_html(out.refused)}<br><br>${__("Load only the entity-periods that are ready?")}`,
					() => konsol_tb_load(frm, true)
				);
			} else {
				frappe.show_alert({ message: __("Loading started. Reload this page to follow it."), indicator: "blue" });
			}
			frm.reload_doc();
		},
	});
}

frappe.ui.form.on("Trial Balance Upload", {
	refresh(frm) {
		konsol_tb_render_report(frm);
		const status = (frm.doc.__onload || {}).status_now;
		if (status && !frm.is_dirty()) {
			frm.page.set_indicator(__(status), frappe.utils.guess_colour(status));
		}
		if (frm.is_new() || frm.is_dirty() || !frm.doc.upload_file) return;
		if (["Draft", "Checked", "Failed"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Check file"), () => konsol_tb_check(frm));
		}
		if (frm.doc.status === "Checked" && frm.doc.valid_count) {
			frm.page.set_primary_action(__("Load"), () => konsol_tb_load(frm, false));
		}
		// tb_bulk.load also resumes a Partly Loaded or Failed upload, and a
		// Loading one whose job stalled (the server refuses while it runs).
		if (["Partly Loaded", "Failed"].includes(frm.doc.status) && frm.doc.report && frm.doc.report !== "[]") {
			frm.page.set_primary_action(__("Resume load"), () => konsol_tb_load(frm, false));
		}
		if (frm.doc.status === "Loading") {
			frm.add_custom_button(__("Refresh"), () => frm.reload_doc());
			frm.add_custom_button(__("Resume load"), () => konsol_tb_load(frm, false));
		}
	},
	onload(frm) {
		if (!frm.is_new() || frm.doc.amount_basis) return;
		frappe.db.get_single_value("EPM Settings", "default_amount_basis").then((value) => {
			if (value && !frm.doc.amount_basis) {
				frm.set_value("amount_basis", value);
			}
		});
	},
});
