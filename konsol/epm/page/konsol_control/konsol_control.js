frappe.pages["konsol-control"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Konsol Control",
		single_column: true,
	});

	page.main.html('<div id="konsol-control-root"></div>');

	frappe.require(
		[
			"/assets/konsol/konsol-control/app.css",
			"/assets/konsol/konsol-control/app.js",
		],
		function () {
			window.konsol_control_init("#konsol-control-root");
		}
	);
};