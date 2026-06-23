import * as React from "react";
import { useMachine } from "@xstate/react";
import { Header } from "./components/Header";
import { PrimaryNav } from "./components/PrimaryNav";
import { DomainSubNav } from "./components/DomainSubNav";
import { GlobalOverview } from "./components/GlobalOverview";
import { Setup } from "./components/Setup";
import { Monitor } from "./components/Monitor";
import { History } from "./components/History";
import { Toast } from "./components/Toast";
import { SECTION_OVERVIEW } from "./constants";
import { isDomainSection } from "./domain";
import { konsolAppMachine } from "./machines";

export function App() {
	const [state, send] = useMachine(konsolAppMachine);
	const { section, subview, dark, toast, data, loadError } = state.context;

	React.useEffect(() => {
		document.documentElement.classList.toggle("kc-dark", dark);
	}, [dark]);

	React.useEffect(() => {
		if (!toast) return undefined;
		const t = setTimeout(() => send({ type: "DISMISS_TOAST" }), 3200);
		return () => clearTimeout(t);
	}, [toast, send]);

	const handlePrimary = React.useCallback(
		(pid, cta) => {
			if (cta === "Setup") {
				send({ type: "NAVIGATE_DOMAIN", domain: pid, subview: "setup" });
			} else if (cta === "Monitor") {
				send({ type: "NAVIGATE_DOMAIN", domain: pid, subview: "monitor" });
			} else if (cta === "Retry") {
				send({ type: "NAVIGATE_DOMAIN", domain: pid, subview: "monitor" });
				send({ type: "START_PROCESS", processId: pid });
			} else if (["Start run", "Run again", "Retry failed step"].includes(cta)) {
				send({ type: "START_PROCESS", processId: pid });
			}
		},
		[send]
	);

	const handleMonitorAction = React.useCallback(
		(pid, _label, proc) => {
			if (!proc.runnable && proc.machine_status === "idle") {
				send({ type: "RESOLVE_SETUP" });
			} else if (proc.machine_status !== "running") {
				send({ type: "START_PROCESS", processId: pid });
			}
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

	return (
		<div className={`kc-app ${dark ? "dark" : ""}`}>
			<Toast message={toast} />
			<Header data={data} dark={dark} onToggleTheme={() => send({ type: "TOGGLE_THEME" })} />
			<PrimaryNav
				section={section}
				data={data}
				onSection={(s) => send({ type: "SELECT_SECTION", section: s })}
			/>
			{inDomain ? (
				<DomainSubNav
					section={section}
					subview={subview}
					data={data}
					onSubview={(v) => send({ type: "SELECT_SUBVIEW", subview: v })}
				/>
			) : null}
			<div className="kc-body">
				{section === SECTION_OVERVIEW ? (
					<GlobalOverview
						data={data}
						onOpenDomain={(domain) =>
							send({ type: "NAVIGATE_DOMAIN", domain, subview: "setup" })
						}
						onPrimary={handlePrimary}
					/>
				) : null}
				{inDomain && subview === "setup" ? (
					<Setup
						domain={section}
						data={data}
						onRemind={(owner, item) => send({ type: "REMIND", owner, item })}
					/>
				) : null}
				{inDomain && subview === "monitor" ? (
					<Monitor domain={section} data={data} onAction={handleMonitorAction} />
				) : null}
				{inDomain && subview === "history" ? (
					<History domain={section} data={data} />
				) : null}
			</div>
		</div>
	);
}