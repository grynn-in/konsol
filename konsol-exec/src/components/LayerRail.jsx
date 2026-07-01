import * as React from "react";
import { METAL } from "../constants";

/**
 * The Layer Rail — the signature element. Renders a process's step sequence as
 * a refinery: strata fill with their metal as each layer lands, the connector
 * "flows" while a layer is in motion. In the full variant it doubles as the
 * launch selector — click a stage to set the build start, shift-click to extend
 * the range.
 *
 * Props:
 *   stages     [{ id, label, metal, glyph, state }]  state: done|now|fail|idle
 *   variant    "full" | "mini"                        (mini = compact, in cards)
 *   selectable bool                                   enable range selection
 *   range      { from, to }                           selected index range
 *   onRange    (nextRange) => void
 */
export function LayerRail({ stages = [], variant = "full", selectable = false, range, onRange }) {
	if (!stages.length) return null;
	const lo = range ? Math.min(range.from, range.to) : -1;
	const hi = range ? Math.max(range.from, range.to) : -1;

	const pick = (i, ev) => {
		if (!selectable || typeof onRange !== "function") return;
		if (ev.shiftKey && range) onRange({ from: range.from, to: i });
		else onRange({ from: i, to: i });
	};

	if (variant === "mini") {
		return (
			<div className="kc-mini-rail" role="img" aria-label="build progress">
				{stages.map((s, i) => (
					<React.Fragment key={s.id}>
						{i > 0 ? <span className="kc-mini-gap" /> : null}
						<span
							className={`kc-mini-node kc-is-${s.state}`}
							style={{ "--m": METAL[s.metal] || "var(--silver)" }}
						/>
					</React.Fragment>
				))}
			</div>
		);
	}

	return (
		<div className="kc-rail" role="group" aria-label="layer rail">
			{stages.map((s, i) => {
				const sel = selectable && i >= lo && i <= hi;
				const prev = stages[i - 1];
				return (
					<React.Fragment key={s.id}>
						{i > 0 ? (
							<span
								className={`kc-pipe ${prev.state === "done" ? "kc-filled" : ""} ${
									s.state === "now" ? "kc-flow" : ""
								}`}
								style={{
									"--m1": METAL[prev.metal] || "var(--silver)",
									"--m2": METAL[s.metal] || "var(--silver)",
								}}
							/>
						) : null}
						<div
							className={`kc-stage kc-is-${s.state} ${sel ? "kc-sel" : ""}`}
							style={{ "--m": METAL[s.metal] || "var(--silver)" }}
						>
							<div className="kc-stage-cap">{s.label}</div>
							{selectable ? (
								<button
									type="button"
									className="kc-node"
									aria-pressed={sel}
									aria-label={`${s.label}${sel ? " (selected)" : ""}`}
									onClick={(e) => pick(i, e)}
								>
									<span className="kc-node-metal" />
									<span className="kc-node-glyph">{s.glyph}</span>
								</button>
							) : (
								<div className="kc-node kc-node-static">
									<span className="kc-node-metal" />
									<span className="kc-node-glyph">{s.glyph}</span>
								</div>
							)}
							<div className="kc-stage-sub">
								{s.state === "now" ? "building…" : s.state === "fail" ? "failed" : s.state === "done" ? "done" : ""}
							</div>
						</div>
					</React.Fragment>
				);
			})}
		</div>
	);
}
