import { useCallback, useEffect, useState } from "react";
import { getSnapshot, sendReminder, startProcess } from "../api";

export function useControlPlane() {
	const [data, setData] = useState(null);
	const [isLoading, setIsLoading] = useState(true);
	const [isError, setIsError] = useState(false);
	const [error, setError] = useState(null);

	const load = useCallback(async (silent = false) => {
		if (!silent) setIsLoading(true);
		try {
			const snapshot = await getSnapshot();
			setData(snapshot);
			setIsError(false);
			setError(null);
		} catch (e) {
			setIsError(true);
			setError(e);
		} finally {
			setIsLoading(false);
		}
	}, []);

	useEffect(() => {
		load();
	}, [load]);

	const active = Object.values(data?.processes || {}).some((p) =>
		["running", "paused"].includes(p.machine_status)
	);

	useEffect(() => {
		if (!active) return undefined;
		const id = setInterval(() => load(true), 2000);
		return () => clearInterval(id);
	}, [active, load]);

	useEffect(() => {
		const refresh = () => load(true);
		frappe.realtime.on("pipeline_progress", refresh);
		frappe.realtime.on("close_run_update", refresh);
		frappe.realtime.on("build_request_complete", refresh);
	}, [load]);

	const start = useCallback(
		async (processId) => {
			const result = await startProcess(processId);
			await load(true);
			return result;
		},
		[load]
	);

	const remind = useCallback(async (owner, item) => {
		await sendReminder(owner, item);
	}, []);

	return { data, isLoading, isError, error, start, remind, reload: load };
}