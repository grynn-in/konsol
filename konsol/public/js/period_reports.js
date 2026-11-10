// One entry point for the Desk period reports (Consolidation Worksheet,
// Consolidated Statement, Statement Drill): each report's onload is
//     onload: (report) => konsol.period_reports.setup(report, { keys: [...] }),
// and setup does the rest (Deepak Pai, 10 Nov 2026):
//   1. start values: a link (route options) carrying any of `keys` is shown
//      as given; otherwise the user's saved filters, else the latest period
//      with data, applied in ONE set_filter_value, only the values that differ;
//   2. the Year and Period strips above the filters;
//   3. the filters saved after each report run (`keys` only).
// Browser: konsol.period_reports. Node: module.exports (create(deps) for tests).
//
// Frappe's QueryReport.refresh_report runs set_route_filters (which clears
// frappe.route_options), then onload with _no_refresh true, then refresh().
// So by onload the route options are gone from frappe.route_options; the
// router has mirrored them into the URL query (router.js set_route), which is
// where they are read. setup returns a promise; run_serially waits for it, so
// the report's first run already has the start values.
(function () {
	const node = typeof module !== "undefined" && module.exports;
	const INFO_METHOD = "konsol.close.period_strip_api.period_strip_info";

	function is_empty(v) {
		return v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length);
	}

	function same(a, b) {
		if (is_empty(a) && is_empty(b)) return true;
		return JSON.stringify(a) === JSON.stringify(b);
	}

	function create(deps = {}) {
		const get_frappe = () => deps.frappe || (typeof window !== "undefined" ? window.frappe : undefined);
		const strip = () => deps.strip || (node ? require("./period_strip.js") : window.konsol.period_strip);
		const search = deps.search || (() => (typeof window !== "undefined" ? window.location.search : ""));
		let memory_lib = null;
		const memory = () => {
			if (!memory_lib) {
				const lib = node ? require("./report_filter_memory.js") : window.konsol.report_filter_memory;
				memory_lib = deps.frappe ? lib.create(deps.frappe) : lib;
			}
			return memory_lib;
		};
		const registry = {}; // report name -> keys it remembers
		let info_promise = null;

		function strip_info() {
			if (!info_promise) {
				// GET: the endpoint only reads (whitelisted methods=["GET"]).
				info_promise = Promise.resolve(get_frappe().call({ method: INFO_METHOD, type: "GET" }))
					.then((r) => (r && r.message) || null);
				info_promise.catch(() => { info_promise = null; }); // retry on the next setup
			}
			return info_promise;
		}

		function route_options() {
			const route = Object.assign({}, get_frappe().route_options || {});
			const params = new URLSearchParams(search() || "");
			params.forEach((v, k) => { if (!(k in route)) route[k] = v; });
			return route;
		}

		function current_values(report, keys) {
			const out = {};
			for (const k of keys) out[k] = report.get_filter_value(k, false);
			return out;
		}

		function detach(report) {
			const h = report.__konsol_period_strips;
			if (h && h.container && h.container.parentNode) h.container.parentNode.removeChild(h.container);
			delete report.__konsol_period_strips;
		}

		function install(report) {
			// frappe.query_report is one object for every query report: wrap once,
			// and act only for a report that registered through setup.
			if (report.__konsol_period_reports) return;
			report.__konsol_period_reports = true;
			const refresh = report.refresh;
			report.refresh = function (...args) {
				const result = refresh.apply(this, args); // a throw ("Filter missing") propagates
				const name = this.report_name;
				const keys = registry[name];
				if (keys) {
					const values = this.get_filter_values(false);
					Promise.resolve(result).then(() => {
						if (this.report_name !== name) return;
						memory().save(name, values, keys);
						const h = this.__konsol_period_strips;
						if (h) h.refresh();
					}, () => {});
				}
				return result;
			};
			const refresh_report = report.refresh_report;
			report.refresh_report = function (...args) {
				// Rebuilt filters (and possibly another report): the strips go,
				// and the report's own setup puts them back.
				detach(this);
				return refresh_report.apply(this, args);
			};
		}

		function settle() {
			// Link filters validate over ajax: wait for that before the report's
			// own refresh reads the filters.
			const f = get_frappe();
			return new Promise((r) => setTimeout(r, 0))
				.then(() => (f.after_ajax ? f.after_ajax() : undefined))
				.then(() => new Promise((r) => setTimeout(r, 0)));
		}

		async function apply(report, values) {
			const held = report._no_refresh;
			// ONE call: set_filter_value clears _no_refresh before the last key;
			// inside onload Frappe refreshes right after, so the flag is put back
			// before any filter's onchange runs (they run asynchronously).
			report.set_filter_value(values);
			if (held) report._no_refresh = held;
			await settle();
		}

		async function setup(report, { keys } = {}) {
			keys = keys || [];
			const name = report.report_name;
			registry[name] = keys;
			install(report);
			const route = route_options();
			const has_route = keys.some((k) => !is_empty(route[k]));

			let info = null;
			try {
				info = await strip_info();
			} catch (e) {
				console.warn("period reports: strip info failed", e);
			}
			try {
				const current = current_values(report, keys);
				const { values } = await memory().start_values({
					report_name: name, route_options: route,
					latest: info && info.latest, defaults: current, keys,
				});
				if (!has_route && report.report_name === name) {
					const changed = {};
					for (const k of keys) {
						if (k in values && !same(values[k], current[k])) changed[k] = values[k];
					}
					if (Object.keys(changed).length) await apply(report, changed);
				}
			} catch (e) {
				console.warn("period reports: start values failed", e);
			}
			if (info && report.report_name === name) strip().attach(report, info);
		}

		return { setup };
	}

	const api = Object.assign({ create }, create());
	if (typeof window !== "undefined") {
		window.konsol = window.konsol || {};
		window.konsol.period_reports = api;
	}
	if (node) module.exports = api;
})();
