// konsol#592 T14: the Rate Sheet form. "Pre-fill from ERP" proposes the
// period's missing rows on a Statutory draft (it creates the draft when
// there is none, from the list's New button); "Duplicate previous" drafts a
// sheet from the previous approved one. Both write, so both POST (a GET
// rolls back). Nothing is submitted: a person approves the draft.
const KONSOL_RATE_WRITERS = ["EPM Analyst", "EPM Admin", "System Manager"];

// The quote columns each basis uses: a Statutory sheet quotes Average and
// Closing, a Constant sheet carries one rate.
const KONSOL_QUOTE_COLUMNS = {
	Statutory: { "average_quote": 1, "closing_quote": 1, "rate": 0 },
	Constant: { "average_quote": 0, "closing_quote": 0, "rate": 1 },
};

function konsol_rate_sheet_columns(frm) {
	const shown = KONSOL_QUOTE_COLUMNS[frm.doc.rate_basis];
	if (!shown) return;
	const grid = frm.fields_dict.rates && frm.fields_dict.rates.grid;
	if (!grid) return;
	Object.keys(shown).forEach((field) => {
		grid.update_docfield_property(field, "hidden", shown[field] ? 0 : 1);
		grid.update_docfield_property(field, "in_list_view", shown[field]);
	});
	grid.reset_grid();
}

function konsol_rate_sheet_summary(title, out, lines) {
	frappe.msgprint(lines.filter(Boolean).join("<br>"), title);
}

frappe.ui.form.on("Rate Sheet", {
	refresh(frm) {
		konsol_rate_sheet_columns(frm);
		if (!frappe.user.has_role(KONSOL_RATE_WRITERS)) return;
		if (frm.doc.rate_basis === "Statutory" && frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Pre-fill from ERP"), () => {
				frappe.call({
					method: "konsol.group_rates.prefill_from_erp",
					type: "POST",
					args: { fiscal_year: frm.doc.fiscal_year, fiscal_period: frm.doc.fiscal_period },
					freeze: true,
					callback: (r) => {
						const out = r.message || {};
						const lines = [
							__("{0} row(s) proposed.", [(out.created || []).length]),
							__("{0} already on the sheet.", [(out.existing || []).length]),
						];
						(out.no_quote || []).forEach((k) => lines.push(__("No ERP quote: {0}", [k])));
						(out.refused || []).forEach((k) => lines.push(__("Refused: {0}", [k])));
						(out.warned || []).forEach((k) =>
							lines.push(__("Drafted, needs a Reason for Change before approval: {0}", [k]))
						);
						konsol_rate_sheet_summary(__("Pre-fill from ERP"), out, lines);
						frm.reload_doc();
					},
				});
			});
		}
		if (!frm.is_new()) {
			frm.add_custom_button(__("Duplicate previous"), () => {
				frappe.prompt(
					[
						{ fieldname: "rate_basis", fieldtype: "Select", label: __("Rate Basis"), reqd: 1,
							options: "Statutory\nConstant", default: frm.doc.rate_basis },
						{ fieldname: "fiscal_year", fieldtype: "Int", label: __("Fiscal Year"), reqd: 1,
							default: frm.doc.fiscal_year },
						{ fieldname: "fiscal_period", fieldtype: "Int", label: __("Fiscal Period"),
							description: __("Statutory only."), depends_on: "eval:doc.rate_basis == 'Statutory'" },
					],
					(values) => {
						frappe.call({
							method: "konsol.group_rates.duplicate_previous",
							type: "POST",
							args: values,
							freeze: true,
							callback: (r) => {
								const out = r.message || {};
								konsol_rate_sheet_summary(__("Duplicate previous"), out, [
									__("Draft {0} created from {1}.", [out.name, out.copied_from]),
								]);
								if (out.name) frappe.set_route("Form", "Rate Sheet", out.name);
							},
						});
					},
					__("Draft a sheet from the previous approved one"),
					__("Duplicate")
				);
			});
		}
	},
	rate_basis(frm) {
		konsol_rate_sheet_columns(frm);
	},
});
