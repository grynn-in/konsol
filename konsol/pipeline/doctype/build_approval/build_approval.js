// Build Approval: a button opens the record that asked for this build.
//
// It used to append a "Trigger" group to the connections panel on every refresh
// (re-initialising the panel each time); on Frappe v16 those groups piled up
// ("Runs / Trigger / ..." repeated, a deployed site, BAPR-00044, 5 Nov
// 2026). The panel now shows only the dashboard's own Runs; the trigger
// fields stay Data (see test_doctype_connections.py for why not a link).
frappe.ui.form.on("Build Approval", {
	refresh(frm) {
		const { trigger_doctype, trigger_docname } = frm.doc;
		if (frm.is_new() || !trigger_doctype || !trigger_docname) return;
		frm.add_custom_button(__("Open trigger ({0})", [__(trigger_doctype)]), () =>
			frappe.set_route("Form", trigger_doctype, trigger_docname)
		);
	},
});
