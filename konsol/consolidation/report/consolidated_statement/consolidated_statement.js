// Consolidated Statement — the Numbers statement (P&L and Balance Sheet) in Desk.
// A heading opens Statement Drill for that heading with the same filters;
// "Add commentary" saves a heading's commentary through
// commentary_api.save_commentary (the Close app's own call).
// Frappe v16 runs report JS as an IIFE: assign the report explicitly.
const ADD_COMMENTARY = __("Add commentary");

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
		},
		{
			fieldname: "entity",
			label: __("Entity"),
			fieldtype: "Link",
			options: "Entity",
			depends_on: 'eval:doc.basis=="Entity"',
			mandatory_depends_on: 'eval:doc.basis=="Entity"',
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
		if (commentable_headings().length) {
			page.add_inner_button(ADD_COMMENTARY, add_commentary);
		}
	},
};
