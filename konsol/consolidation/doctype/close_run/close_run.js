// Close Run — live log streaming + trigger button (Press-style build view)
frappe.ui.form.on("Close Run", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Run Suite"), () => {
				frappe.call({
					method: "konsol.consolidation.doctype.close_run.close_run.trigger_close_run",
					args: { fiscal_year: frm.doc.fiscal_year, fiscal_period: frm.doc.fiscal_period },
					freeze: true,
					freeze_message: __("Queuing assertion suite..."),
					callback: (r) => r.message && frappe.set_route("Form", "Close Run", r.message),
				});
			});
		}
		frm._close_log = frm.doc.log || "";
		render_status_banner(frm);
	},

	onload(frm) {
		frappe.realtime.on("close_run_update", (data) => {
			if (!data || data.run !== frm.doc.name) return;
			if (data.line !== undefined) {
				frm._close_log = (frm._close_log || "") + data.line + "\n";
				frm.set_value("log", frm._close_log.slice(-20000));
			}
			if (data.done) {
				frm.reload_doc();
			}
		});
	},
});

function render_status_banner(frm) {
	const map = { Green: "green", Red: "red", Running: "orange", Error: "red", Queued: "gray" };
	const color = map[frm.doc.status] || "gray";
	const msg = frm.doc.status === "Green"
		? __("All {0} assertions passed", [frm.doc.total])
		: __("{0} passed · {1} failed · {2} errored (of {3})",
			[frm.doc.passed, frm.doc.failed, frm.doc.errored, frm.doc.total]);
	frm.dashboard.clear_headline();
	frm.dashboard.set_headline_alert(`<span class="indicator ${color}">${msg}</span>`);
}
