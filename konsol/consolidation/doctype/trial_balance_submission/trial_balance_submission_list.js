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
		// konsol#283: after a site restore or a lost ClickHouse volume, the
		// submitted documents and the warehouse rows can disagree. This first
		// shows what differs (nothing is written), then queues the re-land on
		// confirm. Orphan claims are removed only when the box is ticked.
		// Every name, reason and error is escaped: they come from documents
		// and exceptions, and the dialog renders HTML (PR #381 review).
		listview.page.add_actions_menu_item(
			__("Rebuild Warehouse Rows…"),
			() => {
				const esc = frappe.utils.escape_html;
				const method =
					"konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission.reland_submitted";
				const indent = (text) => "&nbsp;&nbsp;" + text;
				frappe.call({
					method: method,
					type: "POST",
					args: {},
					freeze: true,
					freeze_message: __("Comparing the submitted trial balances with the warehouse…"),
					callback: (r) => {
						const out = r.message || {};
						const reland = out.reland || [];
						const orphans = out.orphans || [];
						const failed = out.failed || [];
						const kept = out.kept || [];
						if (!reland.length && !orphans.length) {
							frappe.msgprint(
								__("All {0} submitted trial balances match the warehouse. Nothing to rebuild.", [out.matched || 0])
							);
							return;
						}
						const lines = [
							__("{0} submitted trial balances match the warehouse.", [out.matched || 0]),
							__("{0} will be re-landed from their attached files:", [reland.length]),
							...reland.map((x) => indent(__("{0}: {1}", [esc(x[0]), esc(x[1])]))),
						];
						if (failed.length) {
							lines.push(
								__("{0} of them cannot be re-landed, and keep what the warehouse has:", [failed.length]),
								...failed.map((f) => indent(__("{0}: {1}", [esc(f[0]), esc(f[1])])))
							);
						}
						lines.push(
							__("{0} warehouse claims have no submitted trial balance:", [orphans.length]),
							...orphans.map((o) =>
								indent(
									__("{0} ({1}, {2} P{3})", [
										esc(o.submission_name) || esc(o.batch_id),
										esc(o.data_area_id),
										esc(String(o.fiscal_year)),
										esc(String(o.fiscal_period)),
									])
								)
							)
						);
						if (kept.length) {
							lines.push(
								__("{0} claims are left alone: their document exists.", [kept.length]),
								...kept.map((k) => indent(__("{0}: {1}", [esc(k[0]), esc(k[1])])))
							);
						}
						const d = new frappe.ui.Dialog({
							title: __("Rebuild Warehouse Rows"),
							fields: [
								{ fieldtype: "HTML", fieldname: "summary", options: `<p>${lines.join("<br>")}</p>` },
								{
									fieldtype: "Check",
									fieldname: "remove_orphans",
									label: __("Also remove the claims with no submitted trial balance"),
									description: __("Their numbers leave consolidation, as if cancelled. Each period must be open."),
								},
							],
							primary_action_label: __("Rebuild"),
							primary_action(values) {
								d.hide();
								frappe.call({
									method: method,
									type: "POST",
									args: { apply: 1, remove_orphans: values.remove_orphans ? 1 : 0 },
									callback: () => {
										frappe.msgprint(
											__(
												"The rebuild is queued and runs in the background. Its result comes as a notification, and each document it re-lands gets a Comment."
											),
											__("Rebuild Warehouse Rows")
										);
									},
								});
							},
						});
						d.show();
					},
				});
			},
			false
		);
	},
};
