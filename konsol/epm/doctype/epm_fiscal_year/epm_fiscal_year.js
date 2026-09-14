function prompt_period(frm, rows, title, field, callback) {
    // `rows` are the eligible period rows (already filtered by status); the
    // Select shows their codes and the callback gets back the matching
    // fiscal_period number, since that is what the server methods key on.
    if (!rows.length) return;
    frappe.prompt(
        [
            {
                fieldname: "period_code",
                fieldtype: "Select",
                label: __("Period"),
                options: rows.map((r) => r.period_code),
                reqd: 1,
            },
            field,
        ],
        (values) => {
            const row = rows.find((r) => r.period_code === values.period_code);
            callback(row.fiscal_period, values[field.fieldname]);
        },
        title,
        __("Submit")
    );
}

function prompt_text(frm, title, field, callback) {
    frappe.prompt([field], (values) => callback(values[field.fieldname]), title, __("Submit"));
}

frappe.ui.form.on("EPM Fiscal Year", {
    refresh(frm) {
        if (frm.is_new()) return;

        const status = frm.doc.status;
        const periods = frm.doc.periods || [];
        const is_system_manager = frappe.user.has_role("System Manager");
        const note_field = { fieldname: "note", fieldtype: "Small Text", label: __("Note") };
        const reason_field = {
            fieldname: "reason",
            fieldtype: "Small Text",
            label: __("Reason"),
            reqd: 1,
        };

        if (status === "Open") {
            frm.add_custom_button(__("Generate Periods"), function () {
                const generate = () =>
                    frm.call({ method: "generate_periods", doc: frm.doc }).then(() => frm.reload_doc());

                if (periods.length) {
                    frappe.confirm(__("This replaces the current periods."), generate);
                } else {
                    generate();
                }
            }, __("Actions"));
        }

        // -- Year --------------------------------------------------------

        if (status === "Open") {
            frm.add_custom_button(__("Close Year"), function () {
                prompt_text(frm, __("Close Year"), note_field, (note) => {
                    frm.call({ method: "close_year", doc: frm.doc, args: { note } })
                        .then(() => frm.reload_doc());
                });
            }, __("Year"));
        }

        if (status === "Open" || status === "Closed") {
            frm.add_custom_button(__("Lock Year"), function () {
                prompt_text(frm, __("Lock Year"), note_field, (note) => {
                    frm.call({ method: "lock_year", doc: frm.doc, args: { note } })
                        .then(() => frm.reload_doc());
                });
            }, __("Year"));
        }

        if (status === "Closed" || (status === "Locked" && is_system_manager)) {
            frm.add_custom_button(__("Reopen Year"), function () {
                prompt_text(frm, __("Reopen Year"), reason_field, (reason) => {
                    frm.call({ method: "reopen_year", doc: frm.doc, args: { reason } })
                        .then(() => frm.reload_doc());
                });
            }, __("Year"));
        }

        // -- Period --------------------------------------------------------

        const open_periods = periods.filter((r) => r.status === "Open");
        if (open_periods.length) {
            frm.add_custom_button(__("Close Period"), function () {
                prompt_period(frm, open_periods, __("Close Period"), note_field, (fiscal_period, note) => {
                    frm.call({ method: "close_period", doc: frm.doc, args: { fiscal_period, note } })
                        .then(() => frm.reload_doc());
                });
            }, __("Period"));
        }

        const lockable_periods = periods.filter((r) => r.status === "Open" || r.status === "Closed");
        if (lockable_periods.length) {
            frm.add_custom_button(__("Lock Period"), function () {
                prompt_period(frm, lockable_periods, __("Lock Period"), note_field, (fiscal_period, note) => {
                    frm.call({ method: "lock_period", doc: frm.doc, args: { fiscal_period, note } })
                        .then(() => frm.reload_doc());
                });
            }, __("Period"));
        }

        // Reopening a period also needs the year Open (the server reopens
        // the year first otherwise), so only offer it then; a Locked period
        // needs System Manager, same as Reopen Year.
        const reopenable_periods = status === "Open"
            ? periods.filter((r) => r.status === "Closed" || (r.status === "Locked" && is_system_manager))
            : [];
        if (reopenable_periods.length) {
            frm.add_custom_button(__("Reopen Period"), function () {
                prompt_period(frm, reopenable_periods, __("Reopen Period"), reason_field, (fiscal_period, reason) => {
                    frm.call({ method: "reopen_period", doc: frm.doc, args: { fiscal_period, reason } })
                        .then(() => frm.reload_doc());
                });
            }, __("Period"));
        }
    }
});
