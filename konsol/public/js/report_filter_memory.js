// Remembers each user's last filters on a Desk query report, in Frappe user
// settings (server side, so it follows the user across browsers).
// Key scheme: user-settings "doctype" = "konsol report filters - <report>",
// inside it the key "filters". The doctype slot is free text (no DocType is
// looked up), but must not contain "::" (sync_user_settings splits on it).
// Browser: konsol.report_filter_memory. Node: module.exports.
(function () {
	const KEY = "filters";

	function model() {
		if (typeof window !== "undefined" && window.konsol && window.konsol.period_strip_model) {
			return window.konsol.period_strip_model;
		}
		return require("./period_strip_model.js");
	}

	function slot(report_name) {
		return "konsol report filters - " + String(report_name).replace(/::/g, ":");
	}

	function create(frappe_like) {
		const last_saved = {};
		const get_frappe = () =>
			frappe_like || (typeof window !== "undefined" ? window.frappe : undefined);
		const us = () => {
			const f = get_frappe();
			return f && f.model && f.model.user_settings;
		};

		function load(report_name) {
			try {
				const s = us();
				if (!s || !s.get) return Promise.resolve({});
				return Promise.resolve(s.get(slot(report_name))).then(
					(all) => {
						const saved = (all && all[KEY]) || {};
						last_saved[report_name] = JSON.stringify(saved);
						return saved;
					},
					(e) => {
						console.warn("report filter memory: load failed", e);
						return {};
					}
				);
			} catch (e) {
				console.warn("report filter memory: load failed", e);
				return Promise.resolve({});
			}
		}

		function save(report_name, values, keys) {
			try {
				const s = us();
				if (!s || !s.update) return Promise.resolve();
				const value = model().to_save(values, keys);
				const json = JSON.stringify(value);
				if (last_saved[report_name] === json) return Promise.resolve();
				last_saved[report_name] = json;
				// update() replaces the "filters" key; save() would deep-merge and
				// resurrect a filter the user has cleared.
				return Promise.resolve(s.update(slot(report_name), { [KEY]: value })).then(
					() => undefined,
					(e) => {
						delete last_saved[report_name]; // retry on the next change
						console.warn("report filter memory: save failed", e);
					}
				);
			} catch (e) {
				console.warn("report filter memory: save failed", e);
				return Promise.resolve();
			}
		}

		function start_values({ report_name, route_options, latest, defaults, keys } = {}) {
			const route = route_options || {};
			const has_key = (keys || []).some(
				(k) => route[k] !== undefined && route[k] !== null && route[k] !== ""
			);
			// A link means exactly that view: the saved filters are not consulted.
			const saved_p = has_key ? Promise.resolve({}) : load(report_name);
			return saved_p.then((saved) =>
				model().initial_filters({ route, saved, latest, defaults })
			);
		}

		return { load, save, start_values };
	}

	const api = Object.assign({ create }, create());
	if (typeof window !== "undefined") {
		window.konsol = window.konsol || {};
		window.konsol.report_filter_memory = api;
	}
	if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
