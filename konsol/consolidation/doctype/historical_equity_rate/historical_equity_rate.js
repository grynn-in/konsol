// Historical Equity Rate: say what the rate converts, right under the rate
// (Deepak Pai, 5 Nov 2026: "1 INR = 0.018800 USD at 01-12-2011, for account
// 3000 of IN_NALCO" next to or below Rate). from_currency and to_currency are
// virtual fields computed by the controller; the rate reads 1 From = rate To.
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
});
