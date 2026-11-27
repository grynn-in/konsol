// Cost Allocation Driver form: Source and its source-specific fields agree.
//
// The controller refuses Fixed Weights rows, Account Set rows or a Value Sign
// filled for a source that does not read them, and the form hides those fields
// with depends_on. So a user who fills rows and then switches Source would be
// refused about fields they can no longer see. On a Source change this script
// says what the switch discards and asks; yes clears the hidden fields, no puts
// the previous source back. Nothing is asked when nothing would be lost.

// What the newly hidden fields hold, as the phrases of the question.
function konsol_ca_driver_losses(frm, new_source) {
	const losses = [];
	const n_fixed = (frm.doc.fixed_weights || []).length;
	const n_accounts = (frm.doc.accounts || []).length;
	if (new_source !== "Fixed Weights" && n_fixed) {
		losses.push(__("{0} fixed-weight row(s)", [n_fixed]));
	}
	if (new_source !== "Account Set") {
		if (n_accounts) losses.push(__("{0} account row(s)", [n_accounts]));
		if (frm.doc.value_sign) losses.push(__("value sign"));
	}
	return losses;
}

function konsol_ca_driver_clear_hidden(frm, new_source) {
	if (new_source !== "Fixed Weights") {
		frm.clear_table("fixed_weights");
		frm.refresh_field("fixed_weights");
	}
	if (new_source !== "Account Set") {
		frm.clear_table("accounts");
		frm.refresh_field("accounts");
		frm.set_value("value_sign", null);
	}
}

frappe.ui.form.on("Cost Allocation Driver", {
	onload(frm) {
		frm.__ca_prev_source = frm.doc.source;
	},

	refresh(frm) {
		frm.__ca_prev_source = frm.doc.source;
	},

	source(frm) {
		const prev = frm.__ca_prev_source;
		const next = frm.doc.source;
		if (!next || next === prev) {
			frm.__ca_prev_source = next;
			return;
		}
		const losses = konsol_ca_driver_losses(frm, next);
		if (!losses.length) {
			frm.__ca_prev_source = next;
			return;
		}
		frappe.confirm(
			__("Switching to {0} removes the {1} entered for {2}. Continue?", [
				__(next), losses.join(" / "), __(prev),
			]),
			() => {
				konsol_ca_driver_clear_hidden(frm, next);
				frm.__ca_prev_source = next;
			},
			() => {
				// re-enters this handler with next === prev, which returns above
				frm.set_value("source", frm.__ca_prev_source);
			}
		);
	},
});
