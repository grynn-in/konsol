// konsol#353 option C, Desk: a journal's amounts are typed at the warehouse's
// 4 places and shown in the journal currency's ISO minor unit.
//
// The Desk is the only product UI (Deepak Pai, 5 Nov 2026). A Currency field
// with no precision takes the site's number format (8069: #'###.##, 2 places),
// and the form rounds what is typed to it, so a BHD 10.125 reached the server
// as 10.13.
//
// Typing: the line amounts and the totals take the warehouse's 4 places
// (journal_model.STORED_PLACES) on this form, whatever the currency. Frappe
// rounds input to the field's precision, so a precision of the currency's
// minor unit would itself change a number silently (JPY 10.5 -> 11, BHD
// 10.1255 -> 10.126). At 4 places what is typed reaches the server as typed,
// and the server refuses a place the currency does not have, with its
// sentence (journal_model.currency_problems: never silently change a number;
// refuse with a reason, Deepak Pai).
//
// Display: Frappe keeps one precision per field for input and display, so the
// display is separated with a formatter on the four docfields. It shows an
// amount in the journal currency's ISO Currency Minor Unit (USD 2, JPY 0,
// BHD 3), the value a site declared under "Minor Unit Not Set by ISO 4217"
// included. The formatter passes the precision as a number, so 0 shows 0
// places (Frappe's get_field_precision would read 0 as unset and show 2).
// Until the currency's minor unit is known, or when it has none, the display
// is 4 places, never fewer than the warehouse keeps. The formatter applies in
// the form; list and report views keep the site's number format. No
// site-wide setting changes.

const KONSOL_JOURNAL_STORED_PLACES = 4;
const KONSOL_JOURNAL_FIELDS = {
	"Consolidation Journal": ["total_debit", "total_credit"],
	"Consolidation Journal Line": ["debit_amount", "credit_amount"],
};
//: ISO Currency code -> minor unit, filled as journals are opened.
const konsol_journal_minor_units = {};

function konsol_journal_currency_of(doc) {
	if (!doc) {
		return null;
	}
	let journal = doc;
	if (doc.doctype === "Consolidation Journal Line") {
		journal = doc.parent && locals["Consolidation Journal"] && locals["Consolidation Journal"][doc.parent];
	}
	return (journal && (journal.currency || journal.__konsol_currency)) || null;
}

function konsol_journal_display_places(doc) {
	const currency = konsol_journal_currency_of(doc);
	const places = currency ? konsol_journal_minor_units[currency] : undefined;
	return places === undefined ? KONSOL_JOURNAL_STORED_PLACES : places;
}

function konsol_journal_format(value, df, options, doc) {
	const shown = Object.assign({}, df, { precision: konsol_journal_display_places(doc) });
	return frappe.form.formatters.Currency(value, shown, options, doc);
}

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

async function konsol_journal_learn_minor_unit(currency) {
	if (!currency || currency in konsol_journal_minor_units) {
		return;
	}
	const r = await frappe.db.get_value("ISO Currency", currency, ["minor_unit", "minor_unit_declared"]);
	const row = r && r.message;
	if (row && row.minor_unit !== null && row.minor_unit !== undefined && row.minor_unit !== "") {
		konsol_journal_minor_units[currency] = Number(row.minor_unit);
	}
}

async function konsol_set_journal_precision(frm) {
	for (const [doctype, fieldnames] of Object.entries(KONSOL_JOURNAL_FIELDS)) {
		for (const fieldname of fieldnames) {
			frappe.meta.get_docfield(doctype, fieldname).formatter = konsol_journal_format;
		}
	}
	for (const fieldname of KONSOL_JOURNAL_FIELDS["Consolidation Journal"]) {
		frm.set_df_property(fieldname, "precision", KONSOL_JOURNAL_STORED_PLACES);
	}
	const grid = frm.fields_dict.lines && frm.fields_dict.lines.grid;
	if (grid) {
		for (const fieldname of KONSOL_JOURNAL_FIELDS["Consolidation Journal Line"]) {
			grid.update_docfield_property(fieldname, "precision", KONSOL_JOURNAL_STORED_PLACES);
		}
	}
	const currency = await konsol_journal_currency(frm);
	frm.doc.__konsol_currency = currency;
	await konsol_journal_learn_minor_unit(currency);
	frm.refresh_field("lines");
	frm.refresh_field("total_debit");
	frm.refresh_field("total_credit");
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
