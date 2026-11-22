// Approved Budget form (konsol#348 S5; PR #554 review m5).
// Layer Policy "All layers" reads every layer, so the Layer field is hidden
// (depends_on) and a layer named under it is refused by validate (the server
// never clears it: on an API insert only the caller knows which of the two it
// meant). On the form the user has just chosen All layers, so the layer that
// choice hides is cleared here, and the save is not refused for a value the
// user can no longer see.
frappe.ui.form.on("Approved Budget", {
	layer_policy(frm) {
		if (frm.doc.layer_policy === "All layers" && frm.doc.layer) {
			frm.set_value("layer", null);
		}
	},
});
