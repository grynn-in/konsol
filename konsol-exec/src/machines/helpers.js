export function hasActiveRuns(data) {
	return Object.values(data?.processes || {}).some((p) =>
		["running", "paused"].includes(p.machine_status)
	);
}

export const clearRunDetail = {
	selected: null,
	detail: null,
	error: null,
};