// Assertion Run — live log streaming + trigger button (Press-style build view)
frappe.ui.form.on("Assertion Run", {
	refresh(frm) {
		const in_progress = ["Queued", "Running"].includes(frm.doc.status);
		if (!frm.is_new() && !in_progress) {
			frm.add_custom_button(__("Run Suite"), () => {
				frappe.call({
					method: "konsol.consolidation.doctype.assertion_run.assertion_run.trigger_close_run",
					args: { fiscal_year: frm.doc.fiscal_year, fiscal_period: frm.doc.fiscal_period },
					freeze: true,
					freeze_message: __("Queuing assertion suite..."),
					callback: (r) => r.message && frappe.set_route("Form", "Assertion Run", r.message),
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
	const terminal = ["Green", "Amber", "Red", "Error"].includes(frm.doc.status);
	const signed = ["Signed Off", "Acknowledged", "Overridden"].includes(frm.doc.signoff_status);
	if (frm.is_new() || !terminal || signed) return;

	if (frm.doc.status === "Green") {
		frm.add_custom_button(__("Sign Off"), () => {
			frappe.confirm(__("Sign off this reconciled close?"), () => call_signoff(frm));
		}).addClass("btn-primary");
	} else if (frm.doc.status === "Amber") {
		// konsol#265: warnings do not block the close and do not need the
		// override role — but they must be acknowledged in writing, so the
		// close record says why it was signed with warnings outstanding.
		frm.add_custom_button(__("Acknowledge & Sign Off"), () => {
			frappe.prompt(
				[{
					fieldname: "acknowledgement", fieldtype: "Small Text", reqd: 1,
					label: __("Acknowledge {0} warning(s)", [frm.doc.warned]),
					description: __("Recorded with your signature on this close."),
				}],
				(v) => call_signoff(frm, null, v.acknowledgement),
				__("Sign off a close with warnings outstanding"), __("Sign Off"));
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

function call_signoff(frm, override_reason, acknowledgement) {
	frappe.call({
		method: "konsol.consolidation.doctype.assertion_run.assertion_run.sign_off_close",
		args: { close_run: frm.doc.name, override_reason, acknowledgement },
		freeze: true,
		freeze_message: __("Signing off..."),
		callback: () => frm.reload_doc(),
	});
}

function render_status_banner(frm) {
	const map = { Green: "green", Amber: "yellow", Red: "red", Running: "orange",
		Error: "red", Queued: "gray" };
	const color = map[frm.doc.status] || "gray";
	let msg;
	if (frm.doc.status === "Green") {
		msg = __("All {0} assertions passed", [frm.doc.total]);
	} else if (frm.doc.status === "Amber") {
		// konsol#265: name the warnings — the rows are on each warned step.
		msg = __("{0} warning(s), nothing failed — review before signing off (of {1})",
			[frm.doc.warned, frm.doc.total]);
	} else {
		msg = __("{0} passed · {1} failed · {2} errored · {3} warned (of {4})",
			[frm.doc.passed, frm.doc.failed, frm.doc.errored, frm.doc.warned, frm.doc.total]);
	}
	frm.dashboard.clear_headline();
	frm.dashboard.set_headline_alert(`<span class="indicator ${color}">${msg}</span>`);
}
