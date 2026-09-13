// konsolidat#93: the Tree view's "New" dialog shows only its `fields` plus the
// DocType's reqd fields (frappe treeview.js prepare_fields). reporting_currency
// and data_area_id are mandatory only for some nodes, so they are listed here,
// with the server's rule for a group node: is_group, or no entity
// (ConsolidationGroup._is_group_node). Frappe loads this file as the
// DocType's __tree_js (frappe/desk/form/meta.py).
frappe.treeview_settings["Consolidation Group"] = {
	fields: [
		// The node's own fields first; Frappe would otherwise append the reqd
		// ones after everything listed here.
		{ fieldtype: "Data", fieldname: "consolidation_group", label: __("Consolidation Group"), reqd: 1 },
		{ fieldtype: "Data", fieldname: "entity_name", label: __("Entity Name"), reqd: 1 },
		{
			fieldtype: "Check",
			fieldname: "is_group",
			label: __("Is Group"),
			description: __("Further sub-groups can only be created under records marked as 'Group'"),
			// A hidden field still submits its value: clear the entity when the
			// node becomes a group, so a group is never saved with an entity.
			onchange() {
				if (this.get_value() && this.layout) this.layout.set_value("data_area_id", "");
			},
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
				"The currency this group presents its consolidated statements in. Every entity below it is translated directly into it. Not needed once an Entity is chosen for a leaf."
			),
		},
	],
};
