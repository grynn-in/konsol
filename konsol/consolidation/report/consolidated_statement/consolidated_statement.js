// Consolidated Statement — the Numbers statement (P&L and Balance Sheet) in Desk.
// A heading opens Statement Drill for that heading with the same filters;
// "Export to Excel" downloads the statement through
// statement_api.export_statement (a form POST in a new tab: open_url_post);
// "Add commentary" saves a heading's commentary through
// commentary_api.save_commentary (the Close app's own call).
// Frappe v16 runs report JS as an IIFE: assign the report explicitly.
const ADD_COMMENTARY = __("Add commentary");
const EXPORT_TO_EXCEL = __("Export to Excel");

function drill_url(data) {
	// The heading row carries the group the statement was built for
	// (statement_report_model: drill_group), so a blank group filter (the
	// only group) still opens the right drill. On the entity basis the row
	// carries drill_entity instead: open the entity drill.
	const params = new URLSearchParams({
		fiscal_year: frappe.query_report.get_filter_value("fiscal_year"),
		fiscal_period: frappe.query_report.get_filter_value("fiscal_period"),
		heading: data.code,
	});
	if (data.drill_entity) {
		params.set("basis", "Entity");
		params.set("entity", data.drill_entity);
	} else {
		params.set("consolidation_group", data.drill_group || "");
	}
	return `${frappe.router.make_url(["query-report", "Statement Drill"])}?${params}`;
}

function commentable_headings() {
	// The rows' can_comment is get_statement's own (role and Open period);
	// the report never re-decides it.
	return (frappe.query_report.data || []).filter((row) => row.code && row.can_comment);
}

function has_statement() {
	// An ok statement has heading rows carrying the group (or entity) the
	// statement was built for (statement_report_model: drill_group /
	// drill_entity); a refusal or a not-built state has none, so no export.
	return (frappe.query_report.data || []).some(
		(row) => row.code && (row.drill_group || row.drill_entity),
	);
}

// The Frappe query report checks only `reqd` before a run (conditional-mandatory
// settings are ignored), so the Basis filter's on_change sets `reqd` on the filter the
// basis needs, then refreshes: a blank one shows Frappe's inline "Please set
// filters", never a server error dialog. Frappe calls on_change INSTEAD of
// its own refresh, so this refreshes itself.
function require_filter(fieldname, required) {
	const filter = frappe.query_report.get_filter(fieldname);
	filter.df.reqd = required ? 1 : 0;
	filter.refresh();
}

function on_basis_change() {
	require_filter("entity", frappe.query_report.get_filter_value("basis") === "Entity");
	frappe.query_report.refresh();
}

function export_to_excel() {
	// A form POST (open_url_post adds the CSRF token); values are sent as
	// strings, as a blank filter is "", never "null" or "undefined".
	const value = (name) => frappe.query_report.get_filter_value(name) || "";
	// Posted to the method's own URL: a "cmd" posted to "/" is deprecated in
	// Frappe v16 and removed in v17. new_window (true) opens a blank tab, so a
	// refusal (JSON) lands there and the Desk tab keeps the statement.
	open_url_post(
		"/api/method/konsol.close.statement_api.export_statement",
		{
			fiscal_year: value("fiscal_year"),
			fiscal_period: value("fiscal_period"),
			basis: value("basis"),
			consolidation_group: value("consolidation_group"),
			entity: value("entity"),
		},
		true,
	);
}

function add_commentary() {
	const headings = commentable_headings();
	if (!headings.length) {
		frappe.msgprint(__("Commentary cannot be added for this period."));
		return;
	}
	const by_code = Object.fromEntries(headings.map((row) => [row.code, row]));
	const dialog = new frappe.ui.Dialog({
		title: ADD_COMMENTARY,
		fields: [
			{
				fieldname: "heading",
				label: __("Heading"),
				fieldtype: "Select",
				reqd: 1,
				options: headings.map((row) => ({ value: row.code, label: `${row.code} ${row.label}` })),
				default: headings[0].code,
				onchange() {
					const row = by_code[dialog.get_value("heading")];
					dialog.set_value("text", (row && row.commentary_text) || "");
				},
			},
			{
				fieldname: "text",
				label: __("Commentary"),
				fieldtype: "Small Text",
				default: headings[0].commentary_text || "",
				description: __("Saving a blank commentary clears it."),
			},
		],
		primary_action_label: __("Save"),
		primary_action(values) {
			const row = by_code[values.heading];
			// The same six parameters the Close app sends (numbers.js
			// commentaryBody): the group code, and the stale-edit token last read.
			frappe.call({
				method: "konsol.close.commentary_api.save_commentary",
				type: "POST",
				args: {
					fiscal_year: frappe.query_report.get_filter_value("fiscal_year"),
					fiscal_period: frappe.query_report.get_filter_value("fiscal_period"),
					consolidation_group: row.consolidation_group,
					heading: values.heading,
					text: values.text || "",
					modified: row.commentary_modified || null,
				},
				freeze: true,
				callback() {
					dialog.hide();
					frappe.show_alert({ message: __("Commentary saved"), indicator: "green" });
					frappe.query_report.refresh();
				},
			});
		},
	});
	dialog.show();
}

frappe.query_reports["Consolidated Statement"] = {
	filters: [
		{
			fieldname: "fiscal_year",
			label: __("Fiscal Year"),
			fieldtype: "Int",
			reqd: 1,
			default: new Date().getFullYear(),
		},
		{
			fieldname: "fiscal_period",
			label: __("Fiscal Period"),
			fieldtype: "Int",
			reqd: 1,
			default: new Date().getMonth() + 1,
		},
		{
			fieldname: "basis",
			label: __("Basis"),
			fieldtype: "Select",
			options: "Group\nEntity",
			default: "Group",
			description: __("Entity: one entity's own trial balance in its functional currency."),
			on_change: on_basis_change,
		},
		{
			fieldname: "entity",
			label: __("Entity"),
			fieldtype: "Link",
			options: "Entity",
			depends_on: 'eval:doc.basis=="Entity"',
			get_query() {
				return { filters: { is_group: 0 } };
			},
		},
		{
			fieldname: "consolidation_group",
			label: __("Consolidation Group"),
			fieldtype: "Link",
			options: "Consolidation Group",
			depends_on: 'eval:doc.basis!="Entity"',
			description: __("A root group. Leave blank when there is only one."),
			// Root groups only (statement_api._ROOT_FILTER: no data_area_id).
			get_query() {
				return { filters: { data_area_id: ["is", "not set"] } };
			},
		},
	],

	formatter(value, row, column, data, default_formatter) {
		if (data && column.fieldname === "commentary") {
			// Free text: escaped, the full text on hover.
			const shown = frappe.utils.escape_html(data.commentary || "");
			const full = frappe.utils.escape_html(data.commentary_text || "");
			return shown ? `<span title="${full}">${shown}</span>` : "";
		}
		value = default_formatter(value, row, column, data);
		if (!data) {
			return value;
		}
		if (column.fieldname === "label" && data.code && (data.drill_group || data.drill_entity)) {
			value = `<a href="${drill_url(data)}" title="${__("Open the drill for this heading")}">${value}</a>`;
		}
		if (column.fieldname === "label" && data.indent) {
			value = `<span style="padding-left: ${data.indent * 2}em">${value}</span>`;
		}
		if (data.bold) {
			value = `<b>${value}</b>`;
		}
		return value;
	},

	after_datatable_render() {
		// Shown only when get_statement's can_comment allows it (on the rows).
		const page = frappe.query_report.page;
		page.remove_inner_button(ADD_COMMENTARY);
		page.remove_inner_button(EXPORT_TO_EXCEL);
		if (has_statement()) {
			page.add_inner_button(EXPORT_TO_EXCEL, export_to_excel);
		}
		if (commentable_headings().length) {
			page.add_inner_button(ADD_COMMENTARY, add_commentary);
		}
	},
};
