frappe.pages["konsol-control"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		single_column: true,
	});
};

frappe.pages["konsol-control"].on_page_show = function (wrapper) {
	load_konsol_control_page(wrapper);
};

function load_konsol_control_page(wrapper) {
	const $wrapper = $(wrapper);
	const $parent = $wrapper.find(".layout-main-section");
	$wrapper.addClass("konsol-control-page");
	$wrapper.find(".page-head").hide();
	$parent.removeClass("frappe-card").addClass("konsol-control-bleed").empty();

	frappe.require("konsol_control.bundle.jsx").then(() => {
		if (frappe.konsol_control?.destroy) {
			frappe.konsol_control.destroy();
		}
		frappe.konsol_control = new frappe.ui.KonsolControl({
			wrapper: $parent,
			page: wrapper.page,
		});
	});
}