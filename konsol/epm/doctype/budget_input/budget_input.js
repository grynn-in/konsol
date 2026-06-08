frappe.ui.form.on("Budget Input", {
    refresh(frm) {
        if (frm.doc.spread_profile_id && !frm.is_new()) {
            frm.add_custom_button(__("Spread"), function () {
                frm.call("spread_annual").then(() => {
                    frm.refresh_fields();
                    frm.dirty();
                });
            });
        }
    },

    // Auto-compute annual_amount when child rows change
    periods_on_form_rendered(frm) {
        frm.fields_dict.periods.grid.wrapper.on("change", function () {
            let total = 0;
            (frm.doc.periods || []).forEach(function (row) {
                total += flt(row.amount);
            });
            frm.set_value("annual_amount", total);
        });
    }
});
