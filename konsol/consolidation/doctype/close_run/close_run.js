// Close Run — live log streaming + trigger button (Press-style build view)
frappe.ui.form.on("Close Run", {
	refresh(frm) {
		const in_progress = ["Queued", "Running"].includes(frm.doc.status);
		if (!frm.is_new() && !in_progress) {
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
		add_signoff_buttons(frm);
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

function add_signoff_buttons(frm) {
	const terminal = ["Green", "Red", "Error"].includes(frm.doc.status);
	const signed = ["Signed Off", "Overridden"].includes(frm.doc.signoff_status);
	if (frm.is_new() || !terminal || signed) return;

	if (frm.doc.status === "Green") {
		frm.add_custom_button(__("Sign Off"), () => {
			frappe.confirm(__("Sign off this reconciled close?"), () => call_signoff(frm));
		}).addClass("btn-primary");
	} else if (frappe.user.has_role("EPM Admin") || frappe.user.has_role("System Manager")) {
		// Red / Error — gated override (EPM Admin only, reason required). Only
		// show the button to users the server would actually let override.
		frm.add_custom_button(__("Override Sign-off"), () => {
			frappe.prompt(
				[{ fieldname: "reason", fieldtype: "Small Text", label: __("Override reason"), reqd: 1 }],
				(v) => call_signoff(frm, v.reason),
				__("Override a non-reconciled close (audited)"), __("Override"));
		});
	}
}

function call_signoff(frm, override_reason) {
	frappe.call({
		method: "konsol.consolidation.doctype.close_run.close_run.sign_off_close",
		args: { close_run: frm.doc.name, override_reason },
		freeze: true,
		freeze_message: __("Signing off..."),
		callback: () => frm.reload_doc(),
	});
}

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
