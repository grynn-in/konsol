// Approvals — the Close app's approvals queue in Desk.
// Frappe v16 runs report JS as an IIFE: assign the report explicitly.
//
// Approve and Reject post to approval_api.approve / reject with the Close
// app's own arguments (close-ui/src/approvals.js, rates.js approveBody):
// {doctype, name} for a plain approve, {doctype, name, reason} for an
// "Allowed with reason" self-approval and for every reject. Only rows the
// server marked can_act (approve mode direct or reason, not a Desk-only deal,
// not sent back) are offered. A server refusal (e.g. self-approval) is shown
// by frappe.call as the server wrote it.

// rates.js NO_REASON_ERROR and approval_api.reject's own sentence.
const KONSOL_NO_APPROVE_REASON = "Give a reason: Close Settings allows self-approval only with a reason.";
const KONSOL_NO_REJECT_REASON = "A rejection needs a reason.";

function konsol_approvals_label(row) {
	return `${row.doctype} ${row.name}: ${row.title}`;
}

/** Ask which actionable row to act on, then call `then(row)`. */
function konsol_approvals_pick(title, then) {
	const rows = (frappe.query_report.data || []).filter((row) => row.can_act);
	if (!rows.length) {
		frappe.msgprint(__("Nothing waiting here that you can approve or reject."));
		return;
	}
	const labels = rows.map(konsol_approvals_label);
	frappe.prompt(
		[
			{
				fieldname: "document",
				fieldtype: "Select",
				label: __("Document"),
				options: labels,
				default: labels[0],
				reqd: 1,
			},
		],
		(values) => then(rows[labels.indexOf(values.document)]),
		title,
		__("Next"),
	);
}

function konsol_approvals_done(message) {
	frappe.show_alert({ message, indicator: "green" }, 5);
	frappe.query_report.refresh();
}

function konsol_approvals_post_approve(row, reason) {
	const args = { doctype: row.doctype, name: row.name };
	if (reason !== undefined) {
		args.reason = reason;
	}
	frappe.call({
		method: "konsol.close.approval_api.approve",
		type: "POST",
		args,
		freeze: true,
		callback(r) {
			const self = r.message && r.message.self_approved ? __(" (self-approved)") : "";
			konsol_approvals_done(__("Approved {0} {1}", [row.doctype, row.name]) + self);
		},
	});
}

function konsol_approvals_approve(row) {
	if (row.approve_mode === "reason") {
		frappe.prompt(
			[
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason for approving your own document"),
					description: row.approve_message,
					reqd: 1,
				},
			],
			(values) => {
				const reason = (values.reason || "").trim();
				if (!reason) {
					frappe.msgprint(KONSOL_NO_APPROVE_REASON);
					return;
				}
				konsol_approvals_post_approve(row, reason);
			},
			__("Approve {0} {1}", [row.doctype, row.name]),
			__("Approve"),
		);
		return;
	}
	const what = [row.doctype, row.name, row.title].map((s) => frappe.utils.escape_html(s || ""));
	frappe.confirm(__("Approve {0} {1}: {2}?", what), () =>
		konsol_approvals_post_approve(row),
	);
}

function konsol_approvals_reject(row) {
	frappe.prompt(
		[
			{
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Reason for rejecting"),
				reqd: 1,
			},
		],
		(values) => {
			const reason = (values.reason || "").trim();
			if (!reason) {
				frappe.msgprint(KONSOL_NO_REJECT_REASON);
				return;
			}
			frappe.call({
				method: "konsol.close.approval_api.reject",
				type: "POST",
				args: { doctype: row.doctype, name: row.name, reason },
				freeze: true,
				callback() {
					konsol_approvals_done(__("Rejected {0} {1}", [row.doctype, row.name]));
				},
			});
		},
		__("Reject {0} {1}", [row.doctype, row.name]),
		__("Reject"),
	);
}

frappe.query_reports["Approvals"] = {
	filters: [],

	onload(report) {
		report.page.add_inner_button(__("Approve"), () =>
			konsol_approvals_pick(__("Approve"), konsol_approvals_approve),
		);
		report.page.add_inner_button(__("Reject"), () =>
			konsol_approvals_pick(__("Reject"), konsol_approvals_reject),
		);
	},

	formatter(value, row, column, data, default_formatter) {
		if (data && column.fieldname === "name" && data.desk) {
			return `<a href="${frappe.utils.escape_html(data.desk)}">${frappe.utils.escape_html(data.name)}</a>`;
		}
		value = default_formatter(value, row, column, data);
		if (data && column.fieldname === "status" && data.status === "Sent back") {
			value = `<span class="indicator-pill orange">${value}</span>`;
		}
		return value;
	},
};
