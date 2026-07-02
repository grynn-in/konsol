import * as React from "react";
import { buildRunArgs } from "../orchestrator/params";
import { getLaunchOptions } from "../api";
import { LayerRail } from "./LayerRail";

const EMPTY_OPTIONS = {
	definitions: [],
	fiscal_years: [],
	fiscal_periods: [],
	scopes: [],
};

/** Fallback verb — also the literal the launch button falls back to. */
const FALLBACK_VERB = "Start Run";

function fallbackYears() {
	const now = new Date().getFullYear();
	const years = [];
	for (let y = now + 1; y >= now - 5; y--) years.push(String(y));
	return years;
}

function asOption(o) {
	return typeof o === "string" ? { value: o, label: o } : o;
}

/**
 * Launch panel. The Layer Rail sits on top: for a layered process
 * (Consolidation) it's a range selector — click a stage to set the build start,
 * shift-click to extend; for others it previews the exact step sequence so the
 * verb is never abstract. The form runs through buildRunArgs and dispatches
 * LAUNCH to the runExecMachine.
 */
export function ExecuteLaunch({ send, meta, busy }) {
	const stages = (meta && meta.stages) || [];
	const selectable = meta?.id === "consolidation";
	const verb = meta?.verb || FALLBACK_VERB;

	const [form, setForm] = React.useState({
		fiscal_year: "",
		fiscal_period: "",
		scope: "",
		definition: "",
		full_refresh: false,
	});
	const [options, setOptions] = React.useState(EMPTY_OPTIONS);
	// default build range spans the whole rail
	const [range, setRange] = React.useState({ from: 0, to: Math.max(0, stages.length - 1) });

	React.useEffect(() => {
		setRange({ from: 0, to: Math.max(0, stages.length - 1) });
	}, [meta?.id, stages.length]);

	React.useEffect(() => {
		let live = true;
		getLaunchOptions()
			.then((opts) => {
				if (!live || !opts) return;
				setOptions({
					definitions: opts.definitions || [],
					fiscal_years:
						opts.fiscal_years && opts.fiscal_years.length ? opts.fiscal_years : fallbackYears(),
					fiscal_periods: opts.fiscal_periods || [],
					scopes: opts.scopes || [],
				});
			})
			.catch(() => {
				if (live) setOptions({ ...EMPTY_OPTIONS, fiscal_years: fallbackYears() });
			});
		return () => {
			live = false;
		};
	}, []);

	const set = (key) => (e) => {
		const val = e.target.type === "checkbox" ? e.target.checked : e.target.value;
		setForm((prev) => ({ ...prev, [key]: val }));
	};

	const railStages = stages.map((s) => ({ ...s, state: "idle" }));
	const lo = Math.min(range.from, range.to);
	const hi = Math.max(range.from, range.to);
	const rangeText =
		!selectable || !stages.length
			? verb
			: lo === hi
				? stages[lo].label
				: `${stages[lo].label} → ${stages[hi].label}`;

	const sliceText = [form.fiscal_year || "all years", form.fiscal_period || "all periods", form.scope || "all scope"].join(" · ");

	const onStart = () => {
		const { definition, params } = buildRunArgs(form);
		if (selectable && stages.length) {
			params.from_stage = stages[lo].id;
			params.to_stage = stages[hi].id;
		}
		send({ type: "LAUNCH", definition, params });
	};

	return (
		<div className="kc-exec">
			{stages.length ? (
				<div className="kc-railwrap">
					<LayerRail
						stages={railStages}
						variant="full"
						selectable={selectable}
						range={selectable ? range : undefined}
						onRange={setRange}
					/>
					<div className="kc-rangecap">
						<span className="kc-chip kc-mono">{selectable ? "build range" : "this run does"}</span>
						<span className="kc-mono kc-rangecap-txt">
							{selectable ? rangeText : stages.map((s) => s.label).join("  →  ")}
						</span>
						{selectable ? <span className="kc-muted">· rebuilds the selected layers and everything downstream.</span> : null}
					</div>
				</div>
			) : null}

			<div className="kc-card kc-launch-card">
				<div className="kc-launch-grid">
					<label className="kc-field">
						<span className="kc-field-label">Fiscal Year</span>
						<select name="fiscal_year" className="kc-input" value={form.fiscal_year} onChange={set("fiscal_year")}>
							<option value="">All years</option>
							{options.fiscal_years.map((o) => {
								const { value, label } = asOption(o);
								return <option key={value} value={value}>{label}</option>;
							})}
						</select>
					</label>
					<label className="kc-field">
						<span className="kc-field-label">Fiscal Period</span>
						<select name="fiscal_period" className="kc-input" value={form.fiscal_period} onChange={set("fiscal_period")}>
							<option value="">All periods</option>
							{options.fiscal_periods.map((o) => {
								const { value, label } = asOption(o);
								return <option key={value} value={value}>{label}</option>;
							})}
						</select>
					</label>
					<label className="kc-field">
						<span className="kc-field-label">Scope</span>
						<select name="scope" className="kc-input" value={form.scope} onChange={set("scope")}>
							<option value="">All entities / groups</option>
							{options.scopes.map((o) => {
								const { value, label } = asOption(o);
								return <option key={value} value={value}>{label}</option>;
							})}
						</select>
					</label>
					<label className="kc-field">
						<span className="kc-field-label">Pipeline</span>
						<select name="definition" className="kc-input" value={form.definition} onChange={set("definition")}>
							<option value="">Default pipeline</option>
							{options.definitions.map((o) => {
								const { value, label } = asOption(o);
								return <option key={value} value={value}>{label}</option>;
							})}
						</select>
					</label>

					<div className="kc-launch-run">
						<label className="kc-check">
							<input type="checkbox" name="full_refresh" checked={form.full_refresh} onChange={set("full_refresh")} />
							<span>Full Refresh</span>
						</label>
						<button
							type="button"
							className="kc-btn kc-btn-primary kc-runbtn"
							disabled={busy}
							onClick={onStart}
						>
							▶ {selectable ? `${verb} ${rangeText}` : verb}
						</button>
					</div>
				</div>

				<div className="kc-willrun">
					About to run <b>{rangeText}</b> · <span className="kc-mono">{sliceText}</span>
					{form.full_refresh ? " · full refresh" : ""} · governed dbt build.
				</div>
				<div className="kc-muted kc-launch-note">
					Airbyte sync is governed globally (EPM Settings → Skip Airbyte Sync).
				</div>
			</div>
		</div>
	);
}
