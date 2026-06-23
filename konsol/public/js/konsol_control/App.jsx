import * as React from "react";
import { Header } from "./components/Header";
import { TabBar } from "./components/TabBar";
import { Overview } from "./components/Overview";
import { Setup } from "./components/Setup";
import { Monitor } from "./components/Monitor";
import { History } from "./components/History";
import { Toast } from "./components/Toast";
import { useControlPlane } from "./hooks/useControlPlane";
export function App() {
	const { data, isLoading, isError, error, start, remind } = useControlPlane();
	const [tab, setTab] = React.useState("overview");
	const [dark, setDark] = React.useState(false);
	const [selected, setSelected] = React.useState("forecasting");
	const [setupSel, setSetupSel] = React.useState("budgeting");
	const [toast, setToast] = React.useState(null);

	React.useEffect(() => {
		document.body.classList.toggle("konsol-control-active", true);
		document.body.classList.toggle("konsol-control-dark", dark);
		return () => {
			document.body.classList.remove("konsol-control-active", "konsol-control-dark");
		};
	}, [dark]);

	const showToast = React.useCallback((msg) => {
		setToast(msg);
		const t = setTimeout(() => setToast(null), 3200);
		return () => clearTimeout(t);
	}, []);

	const handleStart = React.useCallback(
		async (processId) => {
			try {
				const r = await start(processId);
				showToast(`Started ${processId} · ${r.name}`);
				setTab("monitor");
				setSelected(processId);
			} catch (e) {
				frappe.msgprint({
					title: __("Start failed"),
					indicator: "red",
					message: e?.message || String(e),
				});
			}
		},
		[start, showToast]
	);

	const handleRemind = React.useCallback(
		async (owner, item) => {
			await remind(owner, item);
			showToast(`Reminder sent to ${owner} · ${new Date().toLocaleTimeString("en-GB")}`);
		},
		[remind, showToast]
	);

	const handlePrimary = React.useCallback(
		(pid, cta) => {
			if (cta === "Resolve setup") {
				setTab("setup");
				setSetupSel(pid);
			} else if (cta === "Open monitor") {
				setTab("monitor");
				setSelected(pid);
			} else if (["Start run", "Run again", "Retry failed step"].includes(cta)) {
				handleStart(pid);
			}
		},
		[handleStart]
	);

	const handleMonitorAction = React.useCallback(
		(pid, label, proc) => {
			if (!proc.runnable && proc.machine_status === "idle") {
				setTab("setup");
				setSetupSel(pid);
			} else if (proc.machine_status !== "running") {
				handleStart(pid);
			}
		},
		[handleStart]
	);

	if (isLoading && !data) {
		return <div className="kc-loading">Loading control plane…</div>;
	}

	if (isError) {
		return (
			<div className="kc-loading">
				Failed to load control plane: {error?.message || "Unknown error"}
			</div>
		);
	}

	return (
		<div className={`kc-app ${dark ? "dark" : ""}`}>
			<Toast message={toast} />
			<Header data={data} dark={dark} onToggleTheme={() => setDark((d) => !d)} />
			<TabBar tab={tab} data={data} onTab={setTab} />
			<div className="kc-body">
				{tab === "overview" ? (
					<Overview
						data={data}
						onPrimary={handlePrimary}
						onOpenSetup={(pid) => {
							setTab("setup");
							setSetupSel(pid);
						}}
						onRemind={handleRemind}
					/>
				) : null}
				{tab === "setup" ? (
					<Setup
						data={data}
						setupSel={setupSel}
						onSetupSel={setSetupSel}
						onRemind={handleRemind}
					/>
				) : null}
				{tab === "monitor" ? (
					<Monitor
						data={data}
						selected={selected}
						onSelect={setSelected}
						onAction={handleMonitorAction}
					/>
				) : null}
				{tab === "history" ? <History data={data} /> : null}
			</div>
		</div>
	);
}