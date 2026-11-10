// Year and Period slider strips above a Desk query report's filter row.
// Browser: konsol.period_strip.attach(report, info). Node: module.exports.
// The typed filter boxes stay; the strips follow them and change both
// filters in ONE set_filter_value call (a refresh between the two would run
// a half-changed period).
(function () {
	const model = typeof module !== "undefined" && module.exports
		? require("./period_strip_model.js")
		: window.konsol.period_strip_model;

	const CSS = `
.konsol-period-strips{margin:0 0 var(--margin-sm,8px);font-size:var(--text-sm,12px);color:var(--text-muted)}
.konsol-strip-row{display:flex;align-items:center;gap:6px;margin-bottom:2px}
.konsol-strip-name{width:48px}
.konsol-strip-track{position:relative;flex:1;max-width:420px}
.konsol-strip-track input[type=range]{width:100%;margin:0;accent-color:var(--primary)}
.konsol-strip-ticks{position:relative;height:6px}
.konsol-strip-tick{position:absolute;top:0;width:1px;height:6px;background:var(--border-color);
	border-left:1px solid var(--text-muted);transform:translateX(-50%)}
.konsol-strip-btn{border:1px solid var(--border-color);background:var(--control-bg,transparent);
	color:var(--text-color);border-radius:var(--border-radius,2px);line-height:1;padding:1px 7px;cursor:pointer}
.konsol-strip-btn:disabled{opacity:.4;cursor:default}
.konsol-strip-label{min-width:40px;font-weight:600;color:var(--text-color)}
.konsol-strip-label.out-of-range{color:var(--red-500,var(--text-muted))}
`;

	const ROWS = [
		{ key: "year", field: "fiscal_year", name: "Year", min: "year_min", max: "year_max" },
		{ key: "period", field: "fiscal_period", name: "Period", min: "period_min", max: "period_max" },
	];

	function el(doc, tag, cls, text) {
		const e = doc.createElement(tag);
		if (cls) e.setAttribute("class", cls);
		if (text !== undefined) e.textContent = text;
		return e;
	}

	function inject_style(doc) {
		if (!doc.head || (doc.getElementById && doc.getElementById("konsol-period-strip-css"))) return;
		const s = el(doc, "style");
		s.setAttribute("id", "konsol-period-strip-css");
		s.textContent = CSS;
		doc.head.appendChild(s);
	}

	function attach(report, info, doc) {
		doc = doc || document;
		const existing = report.__konsol_period_strips;
		if (existing && existing.container.parentNode) return existing; // idempotent
		inject_style(doc);

		const form = report.page && report.page.page_form;
		const form_el = form && (form[0] || form);
		if (!form_el || !form_el.parentNode) return null;

		const container = el(doc, "div", "konsol-period-strips");
		const rows = ROWS.map((r) => {
			const min = info[r.min], max = info[r.max];
			const row = el(doc, "div", "konsol-strip-row");
			row.appendChild(el(doc, "span", "konsol-strip-name", r.name));
			const prev = el(doc, "button", `konsol-strip-btn konsol-strip-${r.key}-prev`, "‹");
			prev.setAttribute("type", "button");
			const next = el(doc, "button", `konsol-strip-btn konsol-strip-${r.key}-next`, "›");
			next.setAttribute("type", "button");
			const track = el(doc, "div", "konsol-strip-track");
			const range = el(doc, "input", `konsol-strip-${r.key}-range`);
			range.setAttribute("type", "range");
			range.setAttribute("min", min);
			range.setAttribute("max", max);
			range.setAttribute("step", 1);
			track.appendChild(range);
			if (r.key === "year") {
				const ticks = el(doc, "div", "konsol-strip-ticks");
				for (const m of model.year_marks(info.years, min, max)) {
					const t = el(doc, "span", "konsol-strip-tick");
					t.setAttribute("data-year", m.year);
					t.setAttribute("title", String(m.year));
					t.style.left = `${m.percent}%`;
					ticks.appendChild(t);
				}
				track.appendChild(ticks);
			}
			const label = el(doc, "span", `konsol-strip-label konsol-strip-${r.key}-label`);
			row.appendChild(prev);
			row.appendChild(track);
			row.appendChild(next);
			row.appendChild(label);
			container.appendChild(row);
			return { ...r, min: min, max: max, range, label, prev, next };
		});

		function real(r) {
			return report.get_filter_value(r.field, false);
		}

		function refresh() {
			for (const r of rows) {
				const v = real(r);
				const pos = model.strip_position(v, r.min, r.max);
				r.range.value = String(pos.value);
				r.label.textContent = v === undefined || v === null || v === "" ? "" : String(v);
				const n = Number(v);
				const known = !(v === undefined || v === null || v === "") && Number.isFinite(n);
				r.prev.disabled = known && n <= r.min;
				r.next.disabled = known && n >= r.max;
				r.label.className = `konsol-strip-label konsol-strip-${r.key}-label` +
					(pos.out_of_range ? " out-of-range" : "");
			}
		}

		function commit(changed, value) {
			const values = {};
			for (const r of rows) values[r.field] = r === changed ? value : real(r);
			report.set_filter_value(values); // both keys, one call: one refresh
			refresh();
		}

		function step_to(r, delta) {
			const current = real(r);
			const target = model.step(current, delta, r.min, r.max);
			if (Number(current) === target) return; // nothing changes: no filter, no refresh
			commit(r, target);
		}

		for (const r of rows) {
			// Dragging: label only. Release (change): the one filter change.
			r.range.addEventListener("input", () => { r.label.textContent = r.range.value; });
			r.range.addEventListener("change", () => commit(r, Number(r.range.value)));
			r.prev.addEventListener("click", () => step_to(r, -1));
			r.next.addEventListener("click", () => step_to(r, 1));
			const filter = report.get_filter(r.field, false);
			if (filter && filter.$input) filter.$input.on("change", refresh);
		}

		form_el.parentNode.insertBefore(container, form_el);
		refresh();
		const handle = { container, refresh };
		report.__konsol_period_strips = handle;
		return handle;
	}

	const api = { attach };
	if (typeof window !== "undefined") {
		window.konsol = window.konsol || {};
		window.konsol.period_strip = api;
	}
	if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
