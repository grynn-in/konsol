// Konsol Setup (konsol#407 phase 4; modelled on tapr_compliance P72's ISMS Setup).
// A read-only checklist from konsol.setup.status(): each step is done, partial or not done,
// with a sentence, a count and a link to the list or form where it is done.
// Every server string is put in the page with .text(), never as HTML.
const KONSOL_SETUP_STATES = {
	done: { mark: "✓", indicator: "green", label: __("Done") },
	partial: { mark: "◐", indicator: "orange", label: __("Partly done") },
	not_done: { mark: "○", indicator: "red", label: __("Not done") },
};

frappe.pages["konsol-setup"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Konsol Setup"),
		single_column: true,
	});
	wrapper.konsol_setup = new KonsolSetup(page);
};

frappe.pages["konsol-setup"].refresh = function (wrapper) {
	wrapper.konsol_setup && wrapper.konsol_setup.refresh();
};

class KonsolSetup {
	constructor(page) {
		this.page = page;
		this.$summary = $('<p class="text-muted konsol-setup-summary"></p>').appendTo(page.main);
		this.$list = $('<ul class="list-unstyled konsol-setup-checklist"></ul>').appendTo(page.main);
		this.page.set_secondary_action(__("Refresh"), () => this.refresh());
		// No refresh() here: Frappe calls frappe.pages["konsol-setup"].refresh when the page is shown,
		// the first time included, so a call here would read status() twice.
	}

	refresh() {
		frappe.call({ method: "konsol.setup.status", type: "GET" }).then((r) => this.render(r.message || {}));
	}

	render(status) {
		const steps = status.steps || [];
		this.$summary.text(__("{0} of {1} steps done", [status.done || 0, status.total || steps.length]));
		this.$list.empty();
		steps.forEach((step) => this.$list.append(this.row(step)));
	}

	row(step) {
		const state = KONSOL_SETUP_STATES[step.state] || KONSOL_SETUP_STATES.not_done;
		const $li = $('<li class="konsol-setup-step" style="margin-bottom: 14px;"></li>');
		const $head = $("<div></div>").appendTo($li);
		$('<span class="indicator-pill"></span>')
			.addClass(state.indicator)
			.text(state.mark + " " + state.label)
			.appendTo($head);
		// A step with no route (the caller cannot open its list) shows its label without a link.
		const $label = Array.isArray(step.route) && step.route.length
			? $('<a class="bold" style="margin-left: 8px;"></a>').attr("href", "#").on("click", (e) => {
				e.preventDefault();
				this.open(step);
			})
			: $('<span class="bold" style="margin-left: 8px;"></span>');
		$label.text(step.label || "").appendTo($head);
		if (step.count !== null && step.count !== undefined) {
			$('<span class="text-muted" style="margin-left: 6px;"></span>').text("(" + step.count + ")").appendTo($head);
		}
		$('<div class="text-muted small" style="margin-left: 4px; margin-top: 2px;"></div>')
			.text(step.detail || "")
			.appendTo($li);
		return $li;
	}

	open(step) {
		const route = Array.isArray(step.route) ? step.route : [];
		if (!route.length) return;
		if (step.route_options) frappe.route_options = step.route_options;
		frappe.set_route(...route);
	}
}
