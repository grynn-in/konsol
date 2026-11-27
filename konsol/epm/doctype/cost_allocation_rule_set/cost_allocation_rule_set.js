// Cost Allocation Rule Set form: the Rates section follows the rules' basis.
//
// The Rates section and table depend on "any rule is Declared Rate", a
// condition on the child table. Frappe re-evaluates a form's depends_on when
// one of the parent's own fields changes, not when a child row does
// (form.js watches child tables only to dirty the form and trigger scripts).
// So the section would appear only after a save or reload. Refreshing the
// rates field re-runs the layout's dependency check at once.
frappe.ui.form.on("Cost Allocation Rule", {
	basis(frm) {
		frm.refresh_field("rates");
	},
	rules_remove(frm) {
		frm.refresh_field("rates");
	},
});
