// Historical Equity Rate: say what the rate converts, right under the rate
// (Deepak Pai, 5 Nov 2026: "1 INR = 0.018800 USD at 01-12-2011, for account
// 3000 of IN_NALCO" next to or below Rate). from_currency is a virtual field
// computed by the controller; to_currency is stored and entered (konsol#374):
// the presentation currency this rate converts into. The rate reads
// 1 From = rate To.
frappe.ui.form.on("Historical Equity Rate", {
	refresh(frm) {
		const d = frm.doc;
		const base = (frappe.get_meta(frm.doctype).fields.find((f) => f.fieldname === "historical_rate") || {})
			.description || "";
		let sentence = "";
		if (!frm.is_new() && d.from_currency && d.to_currency) {
			if (d.from_currency === d.to_currency) {
				// The warehouse translates a same-currency entity at 1.0 before
				// it looks at a historical rate (#373 review).
				sentence = __("This rate is not used: {0} reports in {1}, the group's currency, so it translates at 1.", [
					d.data_area_id,
					d.to_currency,
				]);
			} else {
				sentence = __("1 {0} = {1} {2} at {3}, for account {4} of {5}.", [
					d.from_currency,
					format_number(d.historical_rate, null, 6),
					d.to_currency,
					frappe.datetime.str_to_user(d.rate_date),
					d.main_account,
					d.data_area_id,
				]);
			}
		}
		const text = sentence
			? `<b>${frappe.utils.escape_html(sentence)}</b><br>${frappe.utils.escape_html(base)}`
			: frappe.utils.escape_html(base);
		frm.set_df_property("historical_rate", "description", text);
	},

	consolidation_group(frm) {
		// A visible default, never a silent one (konsol#374): when the group is
		// set and To Currency is still blank, offer the owning group's own
		// reporting currency and say which groups above it present in what.
		// The field stays editable: a rate for the parent's currency is the
		// same form with another To Currency.
		const blank = !frm.doc.to_currency;
		if (!frm.doc.consolidation_group || !blank) {
			return;
		}
		frappe.call({
			method: "konsol.consolidation.doctype.historical_equity_rate.historical_equity_rate.presentation_currencies",
			args: { consolidation_group: frm.doc.consolidation_group },
			callback(r) {
				const chain = r.message || [];
				if (!chain.length || frm.doc.to_currency) {
					return;
				}
				frm.set_value("to_currency", chain[0][1]);
				if (chain.length > 1) {
					const others = chain.slice(1).map((g) => __("{0} ({1})", [g[0], g[1]])).join(", ");
					frm.set_df_property(
						"to_currency",
						"description",
						__("{0} presents in {1}. Groups above it: {2}. Each presentation currency needs its own rate.", [
							chain[0][0],
							chain[0][1],
							others,
						])
					);
				}
			},
		});
	},
});
