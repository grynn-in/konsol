// Historical Equity Rate: head the record with the pair it converts (5 Nov
// 2026). from_currency and to_currency are virtual fields computed by the
// controller; the rate reads 1 From = rate To.
frappe.ui.form.on("Historical Equity Rate", {
	refresh(frm) {
		const d = frm.doc;
		if (frm.is_new() || !d.from_currency || !d.to_currency) return;
		if (d.from_currency === d.to_currency) {
			// The warehouse translates a same-currency entity at 1.0 before it
			// looks at a historical rate (#373 review).
			frm.set_intro(
				__("This rate is not used: {0} reports in {1}, the group's currency, so it translates at 1.", [
					d.data_area_id,
					d.to_currency,
				]),
				"orange"
			);
			return;
		}
		frm.set_intro(
			__("1 {0} = {1} {2} at {3}, for account {4} of {5}.", [
				d.from_currency,
				format_number(d.historical_rate, null, 6),
				d.to_currency,
				frappe.datetime.str_to_user(d.rate_date),
				d.main_account,
				d.data_area_id,
			]),
			"blue"
		);
	},
});
