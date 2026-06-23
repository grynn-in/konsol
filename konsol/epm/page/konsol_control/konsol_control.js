frappe.pages["konsol-control"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		single_column: true,
	});
};

frappe.pages["konsol-control"].on_page_show = function (wrapper) {
	load_konsol_control_page(wrapper);
};

if (!frappe.pages["konsol-control"]._route_hook) {
	frappe.pages["konsol-control"]._route_hook = true;
	frappe.router.on("change", () => {
		if (frappe.get_route_str() !== "konsol-control") {
			document.body.classList.remove(
				"konsol-control-active",
				"konsol-control-dark"
			);
		}
	});
}

function ensure_konsol_control_fonts() {
	if (document.getElementById("konsol-control-fonts")) return;
	const link = document.createElement("link");
	link.id = "konsol-control-fonts";
	link.rel = "stylesheet";
	link.href =
		"https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap";
	document.head.appendChild(link);
}

function tag_konsol_control_shell($wrapper) {
	$wrapper.addClass("konsol-control-page");
	$wrapper.find(".page-head").hide();
	$wrapper.find(".container.page-body").addClass("konsol-control-body");
	$wrapper
		.find(".page-wrapper, .page-content, .layout-main")
		.addClass("konsol-control-shell");
	$wrapper.find(".layout-main-section-wrapper").addClass("konsol-control-bleed-wrap");
	$wrapper
		.find(".layout-main-section")
		.removeClass("frappe-card")
		.addClass("konsol-control-bleed");
	document.body.classList.add("konsol-control-active");
}

function load_konsol_control_page(wrapper) {
	const $wrapper = $(wrapper);
	const $parent = $wrapper.find(".layout-main-section");

	ensure_konsol_control_fonts();
	tag_konsol_control_shell($wrapper);
	$parent.empty();

	Promise.all([
		frappe.require("konsol_control.bundle.css"),
		frappe.require("konsol_control.bundle.jsx"),
	]).then(() => {
		if (frappe.konsol_control?.destroy) {
			frappe.konsol_control.destroy();
		}
		frappe.konsol_control = new frappe.ui.KonsolControl({
			wrapper: $parent,
			page: wrapper.page,
		});
	});
}