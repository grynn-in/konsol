// konsolidat#93: the Tree view's "New" dialog shows only its `fields` plus the
// DocType's reqd fields (frappe treeview.js prepare_fields). reporting_currency
// and data_area_id are mandatory only for some nodes, so they are listed here,
// with the server's rule for a group node: is_group, or no entity
// (ConsolidationGroup._is_group_node). Frappe loads this file as the
// DocType's __tree_js (frappe/desk/form/meta.py).
frappe.treeview_settings["Consolidation Group"] = {
	fields: [
		{
			fieldtype: "Check",
			fieldname: "is_group",
			label: __("Is Group"),
			description: __("Further sub-groups can only be created under records marked as 'Group'"),
		},
		{
			fieldtype: "Link",
			fieldname: "data_area_id",
			label: __("Entity"),
			options: "Entity",
			depends_on: "eval:!doc.is_group",
			mandatory_depends_on: "eval:!doc.is_group",
		},
		{
			fieldtype: "Link",
			fieldname: "reporting_currency",
			label: __("Reporting Currency"),
			options: "ISO Currency",
			depends_on: "eval:doc.is_group || !doc.data_area_id",
			mandatory_depends_on: "eval:doc.is_group || !doc.data_area_id",
			description: __(
				"The currency this group presents its consolidated statements in. Every entity below it is translated directly into it."
			),
		},
	],
};
