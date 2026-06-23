frappe.pages["konsol-control"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		single_column: true,
	});

	const ASSET_BASE = "/assets/konsol/konsol-control";

	function ensureStylesheet(id, href) {
		if (document.getElementById(id)) {
			return Promise.resolve();
		}
		return new Promise((resolve) => {
			const link = document.createElement("link");
			link.id = id;
			link.rel = "stylesheet";
			link.href = href;
			link.onload = resolve;
			link.onerror = resolve;
			document.head.appendChild(link);
		});
	}

	$(page.main)
		.closest(".layout-main-section")
		.addClass("konsol-control-bleed");
	$(wrapper).addClass("konsol-control-page");

	page.main.html('<div id="konsol-control-root"></div>');

	const version =
		frappe.boot.developer_mode || window.dev_server
			? Date.now()
			: window._version_number;

	Promise.all([
		ensureStylesheet(
			"konsol-control-fonts",
			"https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
		),
		ensureStylesheet(
			"konsol-control-css",
			`${ASSET_BASE}/app.css?v=${version}`
		),
	]).then(() => {
		frappe.require(`${ASSET_BASE}/app.js?v=${version}`, () => {
			window.konsol_control_init("#konsol-control-root");
		});
	});
};