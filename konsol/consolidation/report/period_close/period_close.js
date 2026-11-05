// Period Close — run the close checks, sign off, reject, close and reopen a
// period from Desk. Every button POSTs to the close app's own endpoint
// (konsol/close/checks_api.py, signoff_api.py); the server holds every rule
// and its refusal is shown as it wrote it (frappe.call does that). Which
// periods a button offers comes from the row's `allow`, the payload's own
// flags (period_close_report_model.py). The wording of each confirmation is
// the close app's (SignOff.vue, SignOffReject.vue, SignOffPeriodActions.vue)
// where it has one.
// Frappe v16 runs report JS as an IIFE: assign the report explicitly.
(function () {
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

	function checkedRow(report) {
		const dt = report.datatable;
		if (!dt || !dt.rowmanager) return null;
		const checked = dt.rowmanager.getCheckedRows();
		return checked.length ? report.data[Number(checked[0])] : null;
	}

	// Step 1: which period. Only the rows that allow the action are offered;
	// the checked row is the default when it is one of them.
	function choosePeriod(report, allowKey, verb, then) {
		const rows = (report.data || []).filter((r) => r && r.allow && r.allow[allowKey]);
		if (!rows.length) {
			frappe.msgprint(
				__("No period in this report can be {0} now. Next Step and Blocks Sign-off say why.", [verb]),
			);
			return;
		}
		const checked = checkedRow(report);
		const dflt = rows.find((r) => checked && r.period === checked.period) || rows[0];
		if (rows.length === 1) {
			then(rows[0]);
			return;
		}
		frappe.prompt(
			[
				{
					fieldname: "period",
					label: __("Period"),
					fieldtype: "Select",
					options: rows.map((r) => r.period).join("\n"),
					default: dflt.period,
					reqd: 1,
				},
			],
			(v) => then(rows.find((r) => r.period === v.period)),
			__("Choose the period"),
			__("Next"),
		);
	}

	// Step 2: say exactly what will happen, take any text the endpoint needs,
	// then POST.
	function confirmAndPost(report, opts) {
		const fields = [{ fieldname: "what", fieldtype: "HTML", options: `<p>${opts.text}</p>` }];
		if (opts.field) fields.push(opts.field);
		const d = new frappe.ui.Dialog({
			title: opts.title,
			fields,
			primary_action_label: opts.primary,
			primary_action(values) {
				d.hide();
				frappe.call({
					method: opts.method,
					type: "POST",
					args: opts.args(values),
					freeze: true,
					freeze_message: opts.busy,
					callback(r) {
						frappe.show_alert({ message: opts.done(r.message), indicator: "green" }, 7);
						report.refresh();
					},
				});
			},
		});
		d.show();
	}

	const key = (row) => ({ fiscal_year: row.fiscal_year, fiscal_period: row.fiscal_period });

	function runChecks(report) {
		choosePeriod(report, "run_checks", __("checked"), (row) =>
			confirmAndPost(report, {
				title: __("Run checks for {0}", [esc(row.period)]),
				text: __(
					"The close checks for {0} start now and run in the background. When they finish, the new run is the one the sign-off reads, and it starts Not signed. Refresh this report to see the result.",
					[esc(row.period)],
				),
				primary: __("Run checks"),
				busy: __("Starting the checks…"),
				method: "konsol.close.checks_api.run_checks",
				args: () => key(row),
				done: (name) => __("Checks started for {0}: {1}", [esc(row.period), esc(name)]),
			}),
		);
	}

	// The summary's action decides the text the sign needs (signoffMachine):
	// sign: none; acknowledge: the typed acknowledgement; override: the reason.
	const SIGN_FIELDS = {
		acknowledge: {
			fieldname: "text",
			fieldtype: "Small Text",
			reqd: 1,
			label: __("Acknowledge the warnings: say why the period can be signed with them"),
		},
		override: {
			fieldname: "text",
			fieldtype: "Small Text",
			reqd: 1,
			label: __("Override reason: why the period can be signed although checks failed"),
		},
	};
	const SIGN_PRIMARY = {
		sign: __("Sign off"),
		acknowledge: __("Acknowledge and sign off"),
		override: __("Override and sign off"),
	};

	function signOff(report) {
		choosePeriod(report, "sign", __("signed off"), (row) =>
			confirmAndPost(report, {
				title: __("Sign off {0}", [esc(row.period)]),
				text: __("You sign off {0} on checks run {1} ({2}).", [
					esc(row.period),
					esc(row.run),
					esc(row.checks),
				]),
				field: SIGN_FIELDS[row.action],
				primary: SIGN_PRIMARY[row.action],
				busy: __("Sending the sign-off to the server…"),
				method: "konsol.close.signoff_api.sign",
				args: (v) =>
					Object.assign(key(row), {
						run: row.run,
						acknowledgement: row.action === "acknowledge" ? v.text : null,
						override_reason: row.action === "override" ? v.text : null,
					}),
				done: (r) => __("{0}: {1}", [esc(row.period), esc(r && r.signoff_status)]),
			}),
		);
	}

	function reject(report) {
		choosePeriod(report, "reject", __("rejected"), (row) =>
			confirmAndPost(report, {
				title: __("Reject the sign-off of {0}", [esc(row.period)]),
				text: __(
					"{0} goes back to Not signed. The person who ran the checks sees your reason in My work until the period is signed again.",
					[esc(row.period)],
				),
				field: { fieldname: "reason", fieldtype: "Small Text", reqd: 1, label: __("Reason for rejecting (required)") },
				primary: __("Reject sign-off"),
				busy: __("Sending the reject…"),
				method: "konsol.close.signoff_api.reject",
				args: (v) => Object.assign(key(row), { run: row.run, reason: v.reason }),
				done: (r) => __("{0}: {1}", [esc(row.period), esc(r && r.signoff_status)]),
			}),
		);
	}

	function closePeriod(report) {
		choosePeriod(report, "close", __("closed"), (row) =>
			confirmAndPost(report, {
				title: __("Close {0}", [esc(row.period)]),
				text: __(
					"{0} becomes Closed. No checks are run on it and its sign-off cannot be changed until it is reopened.",
					[esc(row.period)],
				),
				field: { fieldname: "note", fieldtype: "Small Text", label: __("Closing note (optional)") },
				primary: __("Close {0}", [esc(row.period)]),
				busy: __("Closing {0}…", [esc(row.period)]),
				method: "konsol.close.signoff_api.close_period",
				args: (v) => Object.assign(key(row), { note: v.note || null }),
				done: (r) => __("{0} is {1}", [esc(row.period), esc(r && r.status)]),
			}),
		);
	}

	function reopenPeriod(report) {
		choosePeriod(report, "reopen", __("reopened"), (row) =>
			confirmAndPost(report, {
				title: __("Reopen {0}", [esc(row.period)]),
				text: __(
					"Later signed periods will need re-signing: their sign-off stops counting until the checks run again and they are signed off again.",
				),
				field: { fieldname: "reason", fieldtype: "Small Text", reqd: 1, label: __("Reason for reopening (required)") },
				primary: __("Reopen {0}", [esc(row.period)]),
				busy: __("Reopening {0}…", [esc(row.period)]),
				method: "konsol.close.signoff_api.reopen_period",
				args: (v) => Object.assign(key(row), { reason: v.reason }),
				done: (r) => __("{0} is {1}", [esc(row.period), esc(r && r.status)]),
			}),
		);
	}

	frappe.query_reports["Period Close"] = {
		filters: [
			{
				fieldname: "fiscal_year",
				label: __("Fiscal Year"),
				fieldtype: "Int",
				default: new Date().getFullYear(),
				description: __("Leave blank for every Open period."),
			},
			{
				fieldname: "consolidation_group",
				label: __("Consolidation Group"),
				fieldtype: "Link",
				options: "Consolidation Group",
				description: __("A root group, for the Commentary column. Leave blank for every group."),
				// Root groups only (statement_api._ROOT_FILTER: no data_area_id).
				get_query() {
					return { filters: { data_area_id: ["is", "not set"] } };
				},
			},
		],

		// A checked row is the default period for the buttons.
		get_datatable_options(options) {
			return Object.assign(options, { checkboxColumn: true });
		},

		onload(report) {
			const group = __("Actions");
			report.page.add_inner_button(__("Run checks"), () => runChecks(report), group);
			report.page.add_inner_button(__("Sign off"), () => signOff(report), group);
			report.page.add_inner_button(__("Reject sign-off"), () => reject(report), group);
			report.page.add_inner_button(__("Close period"), () => closePeriod(report), group);
			report.page.add_inner_button(__("Reopen period"), () => reopenPeriod(report), group);
		},
	};
})();
