// konsolidat#199: batches claimed before the Amount Basis existed carry none,
// and the build preflight refuses them. "Set Amount Basis…" declares it on the
// checked submissions in one go (EPM Admin; open periods only). The server
// writes the field and re-claims each batch in ClickHouse: the newest claim
// wins, the landed rows are untouched, no cancel-amend-resubmit.
frappe.listview_settings["Trial Balance Submission"] = {
	onload(listview) {
		listview.page.add_actions_menu_item(
			__("Set Amount Basis…"),
			() => {
				const names = listview.get_checked_items(true);
				if (!names.length) {
					frappe.msgprint(__("Select the submitted trial balances first."));
					return;
				}
				frappe.prompt(
					[
						{
							fieldname: "amount_basis",
							fieldtype: "Select",
							label: __("Amount Basis"),
							options: ["Period movement", "Year-to-date movement", "Period-end balance"].join("\n"),
							reqd: 1,
							description: __(
								"What each row's debit and credit are in these files. Applies to every checked submission; drafts are skipped."
							),
						},
					],
					(values) => {
						frappe.call({
							method: "konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission.set_amount_basis",
							type: "POST",
							args: { names: names, amount_basis: values.amount_basis },
							freeze: true,
							freeze_message: __("Setting the amount basis…"),
							callback: (r) => {
								const out = r.message || {};
								const skipped = out.skipped || [];
								frappe.show_alert(
									{
										message: __("{0} updated, {1} skipped.", [out.updated || 0, skipped.length]),
										indicator: skipped.length ? "orange" : "green",
									},
									7
								);
								if (skipped.length) {
									frappe.msgprint(
										skipped.map((s) => __("{0}: {1}", [s[0], s[1]])).join("<br>"),
										__("Skipped")
									);
								}
								listview.refresh();
							},
						});
					},
					__("Set Amount Basis on {0} submission(s)", [names.length]),
					__("Set")
				);
			},
			false
		);
	},
};
