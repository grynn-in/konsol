// konsol#415: the site's Brand Colour (Close Settings) as the Desk's accent.
// frappe.boot.konsol_brand is the palette konsol/brand.py derived; null keeps
// Frappe's own colours. Only accent variables and the selected states change:
// icons, body text and every other surface keep Frappe's colours.
(function () {
	function apply() {
		const p = window.frappe && frappe.boot && frappe.boot.konsol_brand;
		if (!p || document.getElementById("konsol-brand")) return;
		const hex = /^#[0-9A-F]{6}$/;
		for (const k of ["primary", "strong", "tint", "strong_dark", "tint_dark"]) {
			if (!hex.test(p[k] || "")) return;
		}
		const style = document.createElement("style");
		style.id = "konsol-brand";
		style.textContent = `
:root {
	--konsol-brand: ${p.primary};
	--konsol-brand-strong: ${p.strong};
	--konsol-brand-tint: ${p.tint};
	--primary: var(--konsol-brand);
	--btn-primary: var(--konsol-brand);
}
:root[data-theme="dark"] {
	--konsol-brand-strong: ${p.strong_dark};
	--konsol-brand-tint: ${p.tint_dark};
}
.btn.btn-primary, .btn-primary {
	background-color: var(--konsol-brand);
	border-color: var(--konsol-brand);
	color: #fff;
}
.btn.btn-primary:hover, .btn.btn-primary:focus, .btn-primary:hover, .btn-primary:focus {
	background-color: ${p.strong};
	border-color: ${p.strong};
	color: #fff;
}
.es-button:not([data-theme]):not(.btn-danger) {
	--bt-solid-bg: var(--konsol-brand);
	--bt-solid-bg-hover: ${p.strong};
	--bt-solid-bg-active: ${p.strong};
	--bt-solid-fg: #fff;
}
.body-sidebar .standard-sidebar-item.active-sidebar {
	background-color: var(--konsol-brand-tint);
}
.body-sidebar .standard-sidebar-item.active-sidebar .item-anchor,
.body-sidebar .standard-sidebar-item.active-sidebar .sidebar-item-icon {
	color: var(--konsol-brand-strong);
	font-weight: 600;
}`;
		document.head.append(style);
	}
	if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
	else apply();
	if (window.$) $(document).on("app_ready", apply);
})();
