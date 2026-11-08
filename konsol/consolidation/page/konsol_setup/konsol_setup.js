// Konsol Setup (konsol#407 phase 4; modelled on tapr_compliance P72's ISMS Setup).
// A read-only checklist from konsol.setup.status(): each step is done, partial or not done,
// with a sentence, a count and a link to the list or form where it is done.
//
// Look (Deepak Pai, 7 Nov 2026, picked from a sketch): a progress bar, then two groups,
// "Needs you" (partial and not done, server order kept; open) and "Done (N)" (folded).
// Each step is one compact <details> row, closed on load: its summary is the mark, the name,
// the count and "Open →"; the detail sentence opens beneath it. A page action opens or
// closes every row and both groups, and names what it will do. Styles: konsol_setup.css.
//
// Every server string is put in the page with .text(), never as HTML.
const KONSOL_SETUP_STATES = {
	done: { mark: "✓", tone: "ks-done", label: __("Done") },
	partial: { mark: "◐", tone: "ks-partial", label: __("Partly done") },
	not_done: { mark: "○", tone: "ks-not-done", label: __("Not done") },
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
		// Every rule in konsol_setup.css is scoped to this class: Frappe keeps the page's
		// <style> in the document after the page is left.
		this.$root = $("<div></div>").addClass("konsol-setup").appendTo(page.main);
		this.$toggle = page.add_button(__("Expand all"), () => this.toggle_all());
		this.page.set_secondary_action(__("Refresh"), () => this.refresh());
		// A row or group opened by hand changes what the toggle will do. toggle does not
		// bubble, so listen in the capture phase.
		this.$root[0].addEventListener("toggle", () => this.label_toggle(), true);
		// No refresh() here: Frappe calls frappe.pages["konsol-setup"].refresh when the page is shown,
		// the first time included, so a call here would read status() twice.
	}

	refresh() {
		frappe.call({ method: "konsol.setup.status", type: "GET" }).then((r) => this.render(r.message || {}));
	}

	all_details() {
		return this.$root.find("details");
	}

	any_closed() {
		return this.all_details().toArray().some((d) => !d.open);
	}

	toggle_all() {
		const open = this.any_closed();
		this.all_details().prop("open", open);
		this.label_toggle();
	}

	label_toggle() {
		this.$toggle.text(this.any_closed() ? __("Expand all") : __("Collapse all"));
	}

	render(status) {
		const steps = status.steps || [];
		const total = status.total || steps.length;
		const done = status.done || 0;
		const needs = steps.filter((s) => s.state !== "done");
		const finished = steps.filter((s) => s.state === "done");

		const $progress = $("<div></div>").addClass("ks-progress");
		$("<progress></progress>").addClass("ks-bar").attr({ max: total, value: done }).appendTo($progress);
		$("<span></span>").addClass("ks-progress-text").text(__("{0} of {1} done", [done, total])).appendTo($progress);

		const nodes = [$progress];
		if (needs.length) {
			nodes.push(this.group("ks-group-needs", __("Needs you"), needs, true));
		} else {
			nodes.push($("<p></p>").addClass("ks-complete").text(__("Setup is complete")));
		}
		if (finished.length) nodes.push(this.group("ks-group-done", __("Done"), finished, false));
		this.$root.empty().append(nodes);
		this.label_toggle();
	}

	group(cls, title, steps, open) {
		const $group = $("<details></details>").addClass("ks-group " + cls).prop("open", open);
		$("<summary></summary>").addClass("ks-group-head").text(title + " (" + steps.length + ")").appendTo($group);
		const $rows = $("<ul></ul>").addClass("ks-rows").appendTo($group);
		steps.forEach((step) => $("<li></li>").append(this.row(step)).appendTo($rows));
		return $group;
	}

	row(step) {
		const state = KONSOL_SETUP_STATES[step.state] || KONSOL_SETUP_STATES.not_done;
		const $row = $("<details></details>").addClass("ks-step " + state.tone);
		const $head = $("<summary></summary>").addClass("ks-step-head").appendTo($row);
		$("<span></span>").addClass("ks-mark").attr("title", state.label).text(state.mark).appendTo($head);
		$("<span></span>").addClass("ks-label").text(step.label || "").appendTo($head);
		const $count = $("<span></span>").addClass("ks-count").appendTo($head);
		if (step.count !== null && step.count !== undefined) $count.text(step.count);
		// A step with no route (the caller cannot open its list) shows no link. The click is
		// the link's: preventDefault stops it opening the row as well.
		if (Array.isArray(step.route) && step.route.length) {
			$("<a></a>")
				.addClass("ks-open")
				.attr("href", "#")
				.text(__("Open →"))
				.on("click", (e) => {
					e.preventDefault();
					this.open(step);
				})
				.appendTo($head);
		}
		$("<p></p>").addClass("ks-detail").text(step.detail || "").appendTo($row);
		return $row;
	}

	open(step) {
		const route = Array.isArray(step.route) ? step.route : [];
		if (!route.length) return;
		if (step.route_options) frappe.route_options = step.route_options;
		frappe.set_route(...route);
	}
}
