import * as React from "react";
import { buildRunArgs } from "../orchestrator/params";

/**
 * ExecuteLaunch — the launch panel for the konsol-exec execution plane.
 *
 * A thin form: it collects the launch fields (Fiscal Year / Period, Scope,
 * Pipeline Definition, Full Refresh + Skip Airbyte Sync), runs the flat form
 * object through the pure ESM core `buildRunArgs(form) -> {definition, params}`,
 * and dispatches a `LAUNCH` event to the E5 `runExecMachine` via the `send`
 * prop (matching how the rest of the SPA dispatches XState events). All logic
 * lives in `buildRunArgs`; this component just wires inputs → event.
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
						<input
							type="text"
							name="fiscal_year"
							className="kc-input"
							value={form.fiscal_year}
							onChange={set("fiscal_year")}
							placeholder="e.g. 2026"
						/>
					</label>
					<label className="kc-field">
						<span className="kc-field-label">Fiscal Period</span>
						<input
							type="text"
							name="fiscal_period"
							className="kc-input"
							value={form.fiscal_period}
							onChange={set("fiscal_period")}
							placeholder="e.g. 06"
						/>
					</label>
					<label className="kc-field">
						<span className="kc-field-label">Scope</span>
						<input
							type="text"
							name="scope"
							className="kc-input"
							value={form.scope}
							onChange={set("scope")}
							placeholder="entity / group"
						/>
					</label>
					<label className="kc-field">
						<span className="kc-field-label">Pipeline Definition</span>
						<input
							type="text"
							name="definition"
							className="kc-input"
							value={form.definition}
							onChange={set("definition")}
							placeholder="default pipeline"
						/>
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
