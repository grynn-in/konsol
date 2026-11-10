// Pure logic for the Year / Period slider strips on Desk query reports.
// No DOM, no frappe. Browser: konsol.period_strip_model. Node: module.exports.
(function () {
	const FISCAL = ["fiscal_year", "fiscal_period"];

	function is_empty(v) {
		return v === undefined || v === null || v === "";
	}

	function clamp(value, min, max) {
		return Math.min(max, Math.max(min, value));
	}

	function step(value, delta, min, max) {
		const n = is_empty(value) ? NaN : Math.round(Number(value));
		if (!Number.isFinite(n)) return min; // empty box: a step lands on the first value
		// Outside the range: never further out, never "back" across the strip.
		// Moving inward lands on the nearest end; moving outward changes nothing.
		if (n < min) return delta > 0 ? min : n;
		if (n > max) return delta < 0 ? max : n;
		return clamp(n + delta, min, max);
	}

	// Period 0 reads "Open" and 13 "Close"; any other value reads as itself.
	function period_label(value) {
		if (is_empty(value)) return "";
		const n = Number(value);
		if (n === 0) return "Open";
		if (n === 13) return "Close";
		return String(value);
	}

	function strip_position(value, min, max) {
		const n = Number(value);
		const shown = clamp(Number.isFinite(n) ? n : min, min, max);
		return { value: shown, out_of_range: shown !== n };
	}

	function year_marks(years, min, max) {
		const seen = Array.from(new Set((years || []).map(Number)))
			.filter((y) => Number.isInteger(y) && y >= min && y <= max)
			.sort((a, b) => a - b);
		return seen.map((year) => ({ year, percent: ((year - min) / (max - min)) * 100 }));
	}

	function initial_filters({ route, saved, latest, defaults } = {}) {
		route = route || {};
		saved = saved || {};
		latest = latest || {};
		defaults = defaults || {};
		// A route with a year or period means the saved and latest period are
		// not used at all: never mix a route year with another period.
		const route_period = FISCAL.some((k) => !is_empty(route[k]));
		const keys = new Set([...Object.keys(route), ...Object.keys(saved),
			...Object.keys(latest), ...Object.keys(defaults)]);
		const values = {};
		const source = {};
		for (const key of keys) {
			const fiscal = FISCAL.includes(key);
			const layers = [["route", route]];
			if (!(fiscal && route_period)) {
				layers.push(["saved", saved]);
				if (fiscal) layers.push(["latest", latest]);
			}
			layers.push(["defaults", defaults]);
			for (const [name, layer] of layers) {
				if (!is_empty(layer[key])) {
					values[key] = layer[key];
					source[key] = name;
					break;
				}
			}
		}
		return { values, source };
	}

	function to_save(filter_values, keys) {
		const out = {};
		for (const key of keys || []) {
			const v = (filter_values || {})[key];
			if (!is_empty(v)) out[key] = v;
		}
		return out;
	}

	const api = { clamp, step, period_label, strip_position, year_marks, initial_filters, to_save };
	if (typeof window !== "undefined") {
		window.konsol = window.konsol || {};
		window.konsol.period_strip_model = api;
	}
	if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
