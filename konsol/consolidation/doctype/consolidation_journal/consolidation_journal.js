// konsol#353 option C, Desk follow-up: the journal's amounts are typed and
// shown in its currency's ISO minor unit, not the site's number format.
//
// The Desk is the only product UI (Deepak Pai, 5 Nov 2026). A Currency field
// with no precision takes the site's number format (8069: #'###.##, 2 places)
// and the form rounds what is typed to it, so a BHD 10.125 reached the server
// as 10.13. Here the precision of the line amounts and the totals is the
// journal currency's ISO Currency Minor Unit (USD 2, JPY 0, BHD 3), the value
// a site declared under "Minor Unit Not Set by ISO 4217" included: it is the
// stored Minor Unit either way. Per form only: no site setting changes.
//
// The journal's currency is the root group's reporting currency (the
// controller sets it on save). Until it is known, or when its ISO Currency
// has no Minor Unit, the precision is the warehouse's 4 places
// (journal_model.STORED_PLACES): nothing the warehouse keeps is rounded away,
// and the server's rule (journal_model.currency_problems) refuses, with a
// sentence, any place the currency does not have.

const KONSOL_JOURNAL_STORED_PLACES = 4;
const KONSOL_JOURNAL_LINE_AMOUNTS = ["debit_amount", "credit_amount"];
const KONSOL_JOURNAL_TOTALS = ["total_debit", "total_credit"];

async function konsol_journal_currency(frm) {
	if (frm.doc.currency) {
		return frm.doc.currency;
	}
	if (!frm.doc.consolidation_group) {
		return null;
	}
	const r = await frappe.db.get_value(
		"Consolidation Group",
		{ consolidation_group: frm.doc.consolidation_group, data_area_id: ["is", "not set"] },
		"reporting_currency",
	);
	return (r && r.message && r.message.reporting_currency) || null;
}

async function konsol_journal_places(currency) {
	if (!currency) {
		return KONSOL_JOURNAL_STORED_PLACES;
	}
	const r = await frappe.db.get_value("ISO Currency", currency, ["minor_unit", "minor_unit_declared"]);
	const row = r && r.message;
	if (!row || row.minor_unit === null || row.minor_unit === undefined || row.minor_unit === "") {
		return KONSOL_JOURNAL_STORED_PLACES;
	}
	return Number(row.minor_unit);
}

async function konsol_set_journal_precision(frm) {
	const places = await konsol_journal_places(await konsol_journal_currency(frm));
	for (const fieldname of KONSOL_JOURNAL_TOTALS) {
		frm.set_df_property(fieldname, "precision", places);
	}
	const grid = frm.fields_dict.lines && frm.fields_dict.lines.grid;
	if (grid) {
		for (const fieldname of KONSOL_JOURNAL_LINE_AMOUNTS) {
			grid.update_docfield_property(fieldname, "precision", places);
		}
	}
	frm.refresh_field("lines");
	return places;
}

frappe.ui.form.on("Consolidation Journal", {
	onload(frm) {
		return konsol_set_journal_precision(frm);
	},
	refresh(frm) {
		return konsol_set_journal_precision(frm);
	},
	consolidation_group(frm) {
		frm.doc.currency = null;
		return konsol_set_journal_precision(frm);
	},
});
