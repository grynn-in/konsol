import * as React from "react";
import { buildRunArgs } from "../orchestrator/params";
import { getLaunchOptions } from "../api";

const EMPTY_OPTIONS = {
	definitions: [],
	fiscal_years: [],
	fiscal_periods: [],
	scopes: [],
};

/** Recent-year fallback when ClickHouse can't supply the real distinct years. */
function fallbackYears() {
	const now = new Date().getFullYear();
	const years = [];
	for (let y = now + 1; y >= now - 5; y--) years.push(String(y));
	return years;
}

/** Normalise an option entry to `{value, label}` (years arrive as bare strings). */
function asOption(o) {
	return typeof o === "string" ? { value: o, label: o } : o;
}

/**
 * ExecuteLaunch — the launch panel for the konsol-exec execution plane.
 *
 * A thin form: it collects the launch fields (Fiscal Year / Period, Scope,
 * Pipeline Definition, Full Refresh + Skip Airbyte Sync), runs the flat form
 * object through the pure ESM core `buildRunArgs(form) -> {definition, params}`,
 * and dispatches a `LAUNCH` event to the E5 `runExecMachine` via the `send`
 * prop. The four scalar fields are **dropdowns** populated from the backend
 * (`getLaunchOptions` → `konsol.orchestrator.api.launch_options`): pipeline
 * definitions, fiscal periods, and scopes (consolidation groups + entities)
 * come from doctypes; fiscal years are the distinct years in gold, with a
 * generated recent-year fallback. An empty selection means "all / default"
 * (the params builder omits blanks). All run-shaping logic lives in
 * `buildRunArgs`; this component just wires inputs → event.
 *
 * @param {{ send: Function, accent?: string, busy?: boolean }} props
 */
export function ExecuteLaunch({ send, accent, busy }) {
	const [form, setForm] = React.useState({
		fiscal_year: "",
		fiscal_period: "",
		scope: "",
		definition: "",
		full_refresh: false,
		skip_sync: false,
	});
	const [options, setOptions] = React.useState(EMPTY_OPTIONS);

	React.useEffect(() => {
		let live = true;
		getLaunchOptions()
			.then((opts) => {
				if (!live || !opts) return;
				setOptions({
					definitions: opts.definitions || [],
					fiscal_years:
						opts.fiscal_years && opts.fiscal_years.length
							? opts.fiscal_years
							: fallbackYears(),
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
		const val =
			e.target.type === "checkbox" ? e.target.checked : e.target.value;
		setForm((prev) => ({ ...prev, [key]: val }));
	};

	const onStart = () => {
		const { definition, params } = buildRunArgs(form);
		send({ type: "LAUNCH", definition, params });
	};

	return (
		<div className="kc-domain-space" style={{ "--domain-accent": accent }}>
			<div className="kc-card kc-launch-card">
				<div className="kc-section-label">Launch a run</div>
				<div className="kc-launch-grid">
					<label className="kc-field">
						<span className="kc-field-label">Fiscal Year</span>
						<select
							name="fiscal_year"
							className="kc-input"
							value={form.fiscal_year}
							onChange={set("fiscal_year")}
						>
							<option value="">All years</option>
							{options.fiscal_years.map((o) => {
								const { value, label } = asOption(o);
								return (
									<option key={value} value={value}>
										{label}
									</option>
								);
							})}
						</select>
					</label>
					<label className="kc-field">
						<span className="kc-field-label">Fiscal Period</span>
						<select
							name="fiscal_period"
							className="kc-input"
							value={form.fiscal_period}
							onChange={set("fiscal_period")}
						>
							<option value="">All periods</option>
							{options.fiscal_periods.map((o) => {
								const { value, label } = asOption(o);
								return (
									<option key={value} value={value}>
										{label}
									</option>
								);
							})}
						</select>
					</label>
					<label className="kc-field">
						<span className="kc-field-label">Scope</span>
						<select
							name="scope"
							className="kc-input"
							value={form.scope}
							onChange={set("scope")}
						>
							<option value="">All entities / groups</option>
							{options.scopes.map((o) => {
								const { value, label } = asOption(o);
								return (
									<option key={value} value={value}>
										{label}
									</option>
								);
							})}
						</select>
					</label>
					<label className="kc-field">
						<span className="kc-field-label">Pipeline Definition</span>
						<select
							name="definition"
							className="kc-input"
							value={form.definition}
							onChange={set("definition")}
						>
							<option value="">Default pipeline</option>
							{options.definitions.map((o) => {
								const { value, label } = asOption(o);
								return (
									<option key={value} value={value}>
										{label}
									</option>
								);
							})}
						</select>
					</label>
				</div>
				<div className="kc-launch-checks">
					<label className="kc-check">
						<input
							type="checkbox"
							name="full_refresh"
							checked={form.full_refresh}
							onChange={set("full_refresh")}
						/>
						<span>Full Refresh</span>
					</label>
					<label className="kc-check">
						<input
							type="checkbox"
							name="skip_sync"
							checked={form.skip_sync}
							onChange={set("skip_sync")}
						/>
						<span>Skip Airbyte Sync</span>
					</label>
				</div>
				<div className="kc-launch-actions">
					<button
						type="button"
						className="kc-btn kc-btn-primary"
						style={accent ? { background: accent } : undefined}
						disabled={busy}
						onClick={onStart}
					>
						Start Run
					</button>
				</div>
			</div>
		</div>
	);
}
