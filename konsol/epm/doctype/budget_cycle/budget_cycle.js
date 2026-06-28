frappe.ui.form.on("Budget Cycle", {
    onload(frm) {
        // A budget cycle locks authored plan data; actuals are GL-derived and
        // never entered/locked here, so the scenario picker hides 'actual'
        // scenarios. The server-side validate() enforces this regardless of
        // the client.
        frm.set_query("scenario_id", function () {
            return { filters: { scenario_type: ["!=", "actual"] } };
        });
    }
});
