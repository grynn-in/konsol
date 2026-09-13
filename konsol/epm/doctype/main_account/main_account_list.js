// konsol#182: load the group chart from one file (konsol.chart_upload), not
// Data Import: a tree needs parents first, the cross-row rules need the whole
// file, and Published rows must reach the warehouse. Check reports, Load
// writes all or nothing, Publish chart publishes every Draft of one chart with
// one rebuild. The Close Lead (EPM Admin) only.
(() => {
	const esc = (s) => frappe.utils.escape_html(String(s == null ? "" : s));
	const list = (title, items, fmt = esc) =>
		items && items.length
			? `<p><b>${esc(title)} (${items.length})</b></p><ul>${items
					.slice(0, 50)
					.map((i) => `<li>${fmt(i)}</li>`)
					.join("")}${items.length > 50 ? "<li>…</li>" : ""}</ul>`
			: "";

	function report_html(r) {
		const change = (c) =>
			`${esc(c.main_account)}: ${Object.entries(c.fields)
				.map(([f, [a, b]]) => `${esc(f)} ${esc(a) || "(blank)"} → ${esc(b) || "(blank)"}`)
				.join("; ")}`;
		const ready = (n) => `${esc(n.main_account)}: ${esc(n.problems.join("; "))}`;
		return [
			r.note ? `<p class="text-warning">${esc(r.note)}</p>` : "",
			`<p>${esc(r.file_name)}: ${esc(r.rows)} account(s), chart <b>${esc(r.chart_of_accounts)}</b></p>`,
			list(__("Problems: nothing will be loaded"), r.errors),
			list(__("New, as Draft"), r.insert),
			list(__("Draft accounts updated"), r.update),
			list(__("Published accounts changed (they stay Published)"), r.published_changes, change),
			list(__("Inactive accounts updated (they stay Inactive)"), r.inactive),
			list(__("Unchanged"), r.unchanged),
			list(__("In konsol but not in the file (never deleted)"), r.not_in_file),
			list(__("Drafts not ready to publish yet"), r.not_ready, ready),
		].join("");
	}

	function load(listview, file_url) {
		frappe.call({
			method: "konsol.chart_upload.load_chart",
			type: "POST",
			args: { file_url },
			freeze: true,
			callback: ({ message: r }) => {
				frappe.msgprint({
					title: r.loaded ? __("Chart loaded") : __("Nothing was loaded"),
					message: report_html(r),
					indicator: r.loaded ? "green" : "red",
				});
				listview.refresh();
			},
		});
	}

	function check(listview, file_url) {
		frappe.call({
			method: "konsol.chart_upload.check_chart_file",
			type: "POST",
			args: { file_url },
			freeze: true,
			callback: ({ message: r }) => {
				const d = new frappe.ui.Dialog({
					title: __("Chart file check"),
					size: "large",
					fields: [{ fieldtype: "HTML", fieldname: "report" }],
					primary_action_label: r.ok ? __("Load") : __("Close"),
					primary_action() {
						d.hide();
						if (r.ok) load(listview, file_url);
					},
				});
				d.fields_dict.report.$wrapper.html(report_html(r));
				d.show();
			},
		});
	}

	function publish(listview) {
		frappe.prompt(
			[{ fieldname: "chart_of_accounts", fieldtype: "Data", label: __("Chart of Accounts"), reqd: 1 }],
			({ chart_of_accounts }) =>
				frappe.call({
					method: "konsol.chart_upload.publish_chart",
					type: "POST",
					args: { chart_of_accounts },
					freeze: true,
					callback: ({ message: r }) => {
						frappe.msgprint(
							(r.published.length
								? __("{0} account(s) published; build request {1}.", [r.published.length, esc(r.build)])
								: __("No Draft accounts in chart {0}.", [esc(chart_of_accounts)])) +
								(r.note ? "<br><br>" + esc(r.note) : "")
						);
						listview.refresh();
					},
				}),
			__("Publish every Draft account of one chart"),
			__("Publish")
		);
	}

	frappe.listview_settings["Main Account"] = {
		onload(listview) {
			if (!frappe.user.has_role(["EPM Admin", "System Manager"])) return;
			listview.page.add_inner_button(__("Upload chart"), () => {
				new frappe.ui.FileUploader({
					allow_multiple: false,
					restrictions: { allowed_file_types: [".csv", ".xlsx"] },
					on_success: (file) => check(listview, file.file_url),
				});
			});
			listview.page.add_inner_button(__("Publish chart"), () => publish(listview));
		},
	};
})();
