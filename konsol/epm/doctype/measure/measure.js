frappe.ui.form.on("Measure", {
    refresh(frm) {
        if (frm.is_new()) return;

        // konsol#229: which Dataset declares this measure, and whether dbt
        // compiles its expression (and so which columns it may use).
        const compilation = (frm.doc.__onload || {}).compilation;
        if (compilation) {
            frm.set_intro(compilation, "blue");
        }

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
