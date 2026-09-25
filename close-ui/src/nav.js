// konsol#305 B08: nav.js
//
// Pure role-filtered navigation. `navFor(persona, roles, counts)` never
// imports frappe, vue or route.js: it only returns plain data, and the
// caller (AppShell, B17) decides how to render it and how to route each
// `screen` id (the ids match B07's `SCREENS`).
//
// Persona strings are `period_model`'s (`close_lead`, `group_accountant`,
// `entity_accountant`, `viewer`); `persona` is the value `period_model.persona`
// already computed on the server side and threaded through. A caller with no
// close role gets an *explicit* "no close role" result, never a bare empty
// array: an empty array cannot be told apart from "this role has zero items
// today", so it is never used to mean "this session has no role".
//
// Delivery 1: the Entity Accountant never sees Checks or Sign-off.

export const SCREEN_MY_WORK = "my-work";
export const SCREEN_TRIAL_BALANCES = "trial-balances";
export const SCREEN_CHECKS = "checks";
export const SCREEN_SIGN_OFF = "sign-off";

const LABELS = {
	[SCREEN_MY_WORK]: "My work",
	[SCREEN_TRIAL_BALANCES]: "Trial balances",
	[SCREEN_CHECKS]: "Checks",
	[SCREEN_SIGN_OFF]: "Sign-off",
};

// Screens each persona sees, in display order.
const SCREENS_BY_PERSONA = {
	close_lead: [SCREEN_MY_WORK, SCREEN_TRIAL_BALANCES, SCREEN_CHECKS, SCREEN_SIGN_OFF],
	group_accountant: [SCREEN_MY_WORK, SCREEN_TRIAL_BALANCES, SCREEN_CHECKS, SCREEN_SIGN_OFF],
	// My work has no items for an Entity Accountant's own screens; Checks and
	// Sign-off are out of scope for Delivery 1.
	entity_accountant: [SCREEN_MY_WORK, SCREEN_TRIAL_BALANCES],
	// A Viewer has nothing to action, so no My work; Sign-off is read-only.
	viewer: [SCREEN_TRIAL_BALANCES, SCREEN_CHECKS, SCREEN_SIGN_OFF],
};

/**
 * The screen list for `persona`, with counts from `counts.by_screen`
 * attached. `roles` is the raw role list the persona was computed from; it
 * is not used to filter (persona already encodes that), but is accepted so
 * a caller never has to strip it before calling.
 *
 * Returns `[{screen, label, count, blocking}]`. When `persona` is not one
 * of the four close personas (no close role held), returns a single
 * explicit entry naming that, `{screen: null, label: "No close role",
 * count: null, blocking: false}` — never `[]`.
 */
export function navFor(persona, roles, counts) {
	const screens = SCREENS_BY_PERSONA[persona];
	if (!screens) {
		return [{ screen: null, label: "No close role", count: null, blocking: false }];
	}
	const byScreen = (counts && counts.by_screen) || {};
	return screens.map((screen) => {
		const entry = byScreen[screen];
		return {
			screen,
			label: LABELS[screen],
			count: entry ? entry.count : null,
			blocking: entry ? Boolean(entry.blocking) : false,
		};
	});
}
