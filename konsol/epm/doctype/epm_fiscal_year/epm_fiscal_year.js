frappe.ui.form.on("EPM Fiscal Year", {
    refresh(frm) {
        if (frm.is_new() || frm.doc.status !== "Open") return;

        frm.add_custom_button(__("Generate Periods"), function () {
            const generate = () =>
                frm.call("generate_periods").then(() => frm.reload_doc());

            if ((frm.doc.periods || []).length) {
                frappe.confirm(__("This replaces the current periods."), generate);
            } else {
                generate();
            }
        }, __("Actions"));
    }
});
