// Approve and Reject in the record's own Actions menu, as in the compliance
// app (Deepak Pai, 5 Nov 2026; konsol/tests/test_desk_approval_actions.py).
//
// hooks.py doctype_js loads this file on every approval doctype's form
// (close_policy_model.APPROVAL_DOCTYPES). It decides nothing:
// - approval_api.approve_mode says what the signed-in user may do (Approve at
//   once, Approve with a self-approval reason, or why not);
// - approval_api.approve / reject do it, and the before_submit hook judges
//   the self-approval again.
// A workflow doctype already has Approve and Reject in its Actions menu; they
// are intercepted here so a reason can be asked for (the native Reject is
// refused without one, approval_api.reject). Any other doctype gets the two
// items itself, in place of Submit.
(() => {
	if (window.konsol_approval_actions_loaded) return; // one copy per doctype bundle
	window.konsol_approval_actions_loaded = true;

	const DOCTYPES = [
		"Consolidation Journal",
		"Business Combination",
		"Business Disposal",
		"Group Exchange Rate",
		"Ownership Period",
		"Historical Equity Rate",
		"IC Balance",
	];
	const WORKFLOW_DOCTYPES = ["Consolidation Journal", "Business Combination", "Business Disposal"];
	const METHODS = {
		approve_mode: "konsol.close.approval_api.approve_mode",
		approve: "konsol.close.approval_api.approve",
		reject: "konsol.close.approval_api.reject",
	};
	const PENDING_STATE = "Pending Approval";

	const is_workflow = (frm) => WORKFLOW_DOCTYPES.includes(frm.doctype);

	function is_waiting(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 0) return false;
		return is_workflow(frm) ? frm.doc.status === PENDING_STATE : true;
	}

	function mode_of(frm) {
		// GET: approve_mode is GET-only, and xcall defaults to POST (403).
		return frappe.xcall(METHODS.approve_mode, { doctype: frm.doctype, name: frm.doc.name }, "GET");
	}

	function ask_reason(title, message, primary) {
		return new Promise((resolve) => {
			const d = new frappe.ui.Dialog({
				title,
				fields: [
					{ fieldtype: "HTML", options: message ? `<p>${frappe.utils.escape_html(message)}</p>` : "" },
					{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
				],
				primary_action_label: primary,
				primary_action(values) {
					d.hide();
					resolve(values.reason);
				},
			});
			d.show();
		});
	}

	function post(frm, method, reason, done_message) {
		return frappe.call({
			method: METHODS[method],
			type: "POST",
			args: { doctype: frm.doctype, name: frm.doc.name, reason: reason || null },
			freeze: true,
			callback() {
				frappe.show_alert({ message: done_message, indicator: "green" });
				frm.reload_doc();
			},
		});
	}

	function approve(frm) {
		return mode_of(frm).then((m) => {
			if (m.mode === "direct") return post(frm, "approve", null, __("Approved"));
			if (m.mode === "reason") {
				return ask_reason(__("Approve your own work"), m.message, __("Approve")).then((reason) =>
					post(frm, "approve", reason, __("Approved (self-approved)"))
				);
			}
			frappe.msgprint({ title: __("Cannot approve"), message: m.message, indicator: "orange" });
		});
	}

	function reject(frm) {
		return ask_reason(
			__("Reject"),
			__("The preparer sees this reason."),
			__("Reject")
		).then((reason) => post(frm, "reject", reason, __("Rejected")));
	}

	const handlers = {
		refresh(frm) {
			if (is_workflow(frm) || !is_waiting(frm)) return;
			mode_of(frm).then((m) => {
				if (m.mode === "sent_back") {
					frm.page.set_indicator(__("Sent back"), "red");
					frm.set_intro(m.message, "red");
					return;
				}
				frm.page.set_indicator(__("Awaiting approval"), "orange");
				if (m.mode === "not_approver") return;
				// Submit is the approval, but it cannot carry a reason: replace it.
				if (!frm.is_dirty()) frm.page.clear_primary_action();
				frm.add_custom_button(__("Approve"), () => approve(frm), __("Actions"));
				frm.add_custom_button(__("Reject"), () => reject(frm), __("Actions"));
			});
		},
		before_workflow_action(frm) {
			const action = frm.selected_workflow_action;
			if (action !== "Approve" && action !== "Reject") return;
			// workflow.js froze the page and runs apply_workflow when this
			// resolves; approval_api does the transition instead, so this never
			// resolves and the native call never runs.
			frappe.dom.unfreeze();
			frm.selected_workflow_action = null;
			(action === "Approve" ? approve(frm) : reject(frm));
			return new Promise(() => {});
		},
	};

	DOCTYPES.forEach((doctype) => frappe.ui.form.on(doctype, handlers));
})();
