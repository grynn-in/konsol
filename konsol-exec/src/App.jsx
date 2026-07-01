import * as React from "react";
import { useMachine } from "@xstate/react";
import { Spine } from "./components/Spine";
import { DomainSubNav } from "./components/DomainSubNav";
import { GlobalOverview } from "./components/GlobalOverview";
import { Setup } from "./components/Setup";
import { Monitor } from "./components/Monitor";
import { History } from "./components/History";
import { ExecuteLaunch } from "./components/ExecuteLaunch";
import { RunTimeline } from "./components/RunTimeline";
import { Toast } from "./components/Toast";
import { SECTION_OVERVIEW, DOMAIN_SUBVIEWS } from "./constants";
import { isDomainSection, getDomainMeta } from "./domain";
import { konsolAppMachine, runExecMachine } from "./machines";

const SUBVIEWS = DOMAIN_SUBVIEWS.map(([id]) => id);
const DOMAIN_IDS = ["budgeting", "forecasting", "consolidation", "assertions"];

/** Parse `#/section[/subview]` → { section, subview } or null when unroutable. */
function parseHash(hash) {
	const parts = (hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
	if (!parts.length || parts[0] === SECTION_OVERVIEW) return { section: SECTION_OVERVIEW, subview: null };
	if (!DOMAIN_IDS.includes(parts[0])) return null;
	const sub = SUBVIEWS.includes(parts[1]) ? parts[1] : "setup";
	return { section: parts[0], subview: sub };
}

export function App() {
	const [state, send] = useMachine(konsolAppMachine);
	const [execState, execSend] = useMachine(runExecMachine);
	const { section, subview, dark, toast, data, loadError } = state.context;

	// keep latest nav state addressable inside the once-bound hashchange handler
	const navRef = React.useRef({ section, subview });
	navRef.current = { section, subview };

	React.useEffect(() => {
		document.documentElement.classList.toggle("kc-light", !dark);
	}, [dark]);

	// URL → state: hydrate from the incoming URL on mount (so a shared deep link
	// or a hard refresh lands on the right view), then track back/forward.
	// Declared BEFORE the writer below so it runs first on mount.
	React.useEffect(() => {
		const apply = () => {
			const parsed = parseHash(window.location.hash);
			if (!parsed) return;
			const cur = navRef.current;
			if (parsed.section === cur.section && (parsed.subview == null || parsed.subview === cur.subview)) return;
			if (parsed.section === SECTION_OVERVIEW) send({ type: "SELECT_SECTION", section: SECTION_OVERVIEW });
			else send({ type: "NAVIGATE_DOMAIN", domain: parsed.section, subview: parsed.subview });
		};
		apply();
		window.addEventListener("hashchange", apply);
		return () => window.removeEventListener("hashchange", apply);
	}, [send]);

	// URL ← state: every view has its own address (deep-linkable, shareable).
	// Skip the FIRST commit — on mount the machine starts at "overview", and
	// writing that would clobber an incoming deep link before the hydrate effect
	// above has dispatched it. After hydration the state matches the URL, so no
	// spurious write (and no extra history entry) occurs.
	const didHydrate = React.useRef(false);
	React.useEffect(() => {
		if (!didHydrate.current) {
			didHydrate.current = true;
			return;
		}
		const desired = section === SECTION_OVERVIEW ? "#/overview" : `#/${section}/${subview}`;
		if (window.location.hash !== desired) window.location.hash = desired;
	}, [section, subview]);

	React.useEffect(() => {
		if (!toast) return undefined;
		const t = setTimeout(() => send({ type: "DISMISS_TOAST" }), 3200);
		return () => clearTimeout(t);
	}, [toast, send]);

	const onOpen = React.useCallback(
		(domain, sub) => {
			if (domain === SECTION_OVERVIEW) send({ type: "SELECT_SECTION", section: SECTION_OVERVIEW });
			else send({ type: "NAVIGATE_DOMAIN", domain, subview: sub || "setup" });
		},
		[send]
	);

	const handleMonitorAction = React.useCallback(
		(pid, _label, proc) => {
			if (!proc.runnable && proc.machine_status === "idle") send({ type: "RESOLVE_SETUP" });
			else if (proc.machine_status !== "running") send({ type: "START_PROCESS", processId: pid });
		},
		[send]
	);

	if (state.matches("loading")) {
		return <div className="kc-loading">Loading control plane…</div>;
	}
	if (state.matches("failed") && !data) {
		return (
			<div className="kc-loading">
				Failed to load control plane: {loadError?.message || "Unknown error"}
				<div style={{ marginTop: 12 }}>
					<button type="button" className="kc-btn kc-btn-primary" onClick={() => send({ type: "RETRY" })}>
						Retry
					</button>
				</div>
			</div>
		);
	}

	const inDomain = isDomainSection(section);
	const meta = getDomainMeta(section);

	return (
		<div className={`kc-app ${dark ? "" : "kc-light"}`}>
			<Toast message={toast} />
			<div className="kc-shell">
				<Spine
					section={section}
					data={data}
					dark={dark}
					onSection={(s) => send({ type: "SELECT_SECTION", section: s })}
					onToggleTheme={() => send({ type: "TOGGLE_THEME" })}
				/>
				<main className="kc-main">
					<div className="kc-topbar">
						<div className="kc-crumb kc-mono">
							control / <b>{inDomain ? meta.label.toLowerCase() : "overview"}</b>
							{inDomain ? <> / {subview === "execute" ? meta.verb.toLowerCase() : subview}</> : null}
						</div>
						{inDomain ? (
							<DomainSubNav
								section={section}
								subview={subview}
								data={data}
								onSubview={(v) => send({ type: "SELECT_SUBVIEW", subview: v })}
							/>
						) : null}
					</div>

					<div className="kc-content">
						{section === SECTION_OVERVIEW ? <GlobalOverview data={data} onOpen={onOpen} /> : null}

						{inDomain && subview === "setup" ? (
							<Setup domain={section} data={data} onRemind={(owner, item) => send({ type: "REMIND", owner, item })} />
						) : null}

						{inDomain && subview === "monitor" ? (
							<Monitor domain={section} data={data} onAction={handleMonitorAction} />
						) : null}

						{inDomain && subview === "execute" ? (
							<>
								<p className="kc-eyebrow">{meta.label} · {meta.verb}</p>
								<h1 className="kc-h1">{meta.desc}</h1>
								<ExecuteLaunch send={execSend} meta={meta} busy={!execState.matches("idle")} />
								<RunTimeline run={execState.context.run} send={execSend} accent="var(--run)" />
							</>
						) : null}

						{inDomain && subview === "history" ? <History domain={section} data={data} /> : null}
					</div>
				</main>
			</div>
		</div>
	);
}
