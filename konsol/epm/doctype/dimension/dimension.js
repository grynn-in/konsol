frappe.ui.form.on("Dimension", {
    refresh(frm) {
        if (frm.is_new()) return;

        if (frm.doc.status === "Draft" || frm.doc.status === "Inactive") {
            frm.add_custom_button(__("Publish"), function () {
                frm.call("publish").then(() => {
                    frm.reload_doc();
                });
            }, __("Actions"));
        }

        if (frm.doc.status === "Published") {
            frm.add_custom_button(__("Unpublish"), function () {
                frm.call("unpublish").then(() => {
                    frm.reload_doc();
                });
            }, __("Actions"));
        }
    }
});
