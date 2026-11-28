// Cost Allocation Rule Set form: the Rates section follows the rules' basis.
//
// The Rates section and table show when any rule is Declared Rate OR the
// table still has rows (rv L1: a rule switched back to Share of Pool must not
// hide rows that the save still refuses). Both are conditions on a child
// table. Frappe re-evaluates a form's depends_on when one of the parent's own
// fields changes, not when a child row does (form.js watches child tables
// only to dirty the form and trigger scripts). So the section would change
// only after a save or reload. Refreshing the rates field re-runs the
// layout's dependency check at once.
frappe.ui.form.on("Cost Allocation Rule", {
	basis(frm) {
		frm.refresh_field("rates");
	},
	rules_remove(frm) {
		frm.refresh_field("rates");
	},
});

frappe.ui.form.on("Cost Allocation Rule Set", {
	rates_remove(frm) {
		// deleting the last stale row hides the section when no rule needs it
		frm.refresh_field("rates");
	},
});
