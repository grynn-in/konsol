// konsol#182: the Tree view's "New" dialog shows only its `fields` plus the
// DocType's reqd fields (frappe treeview.js prepare_fields), modelled on
// consolidation_group_tree.js. is_posting is listed too, hidden, so a heading
// is created with it cleared: its default is 1, and the server refuses a
// heading that is posted to. Frappe loads this file as the DocType's
// __tree_js (frappe/desk/form/meta.py).
frappe.treeview_settings["Main Account"] = {
	breadcrumb: "EPM",
	title: __("Group Chart of Accounts"),
	fields: [
		{ fieldtype: "Data", fieldname: "main_account", label: __("Account Code"), reqd: 1 },
		{ fieldtype: "Data", fieldname: "account_name", label: __("Account Name"), reqd: 1 },
		{
			fieldtype: "Check",
			fieldname: "is_group",
			label: __("Is Group"),
			description: __("A heading: accounts sit under it and it is never posted to"),
			// A hidden field still submits its value.
			onchange() {
				if (this.layout) this.layout.set_value("is_posting", this.get_value() ? 0 : 1);
			},
		},
		{ fieldtype: "Check", fieldname: "is_posting", label: __("Is Posting"), default: 1, hidden: 1 },
		{
			fieldtype: "Data",
			fieldname: "chart_of_accounts",
			label: __("Chart of Accounts"),
			reqd: 1,
			description: __("The same chart as the heading it sits under, e.g. GROUP_GAAP"),
		},
	],
};
