// konsol#305 T08a: auditTrail.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
	trailView, STATIC_KINDS, DYNAMIC_KINDS,
	filterChoices, filterParams, noFilters, toggled, GROUP_LEVEL,
} from "./auditTrail.js";

const NOW = new Date("2026-09-20T12:00:00Z");
const TZ = "UTC";

function counts(overrides = {}) {
	return {
		approvals: 0,
		self_approvals: 0,
		rejections: 0,
		on_behalf_uploads: 0,
		acknowledgements: 0,
		overrides: 0,
		reopenings: 0,
		recovered: 0,
		reasons_not_recorded: 0,
		cancellations: 0,
		...overrides,
	};
}

function event(overrides = {}) {
	return {
		name: "CE-0001",
		kind: "approved",
		entity: null,
		reference_doctype: "Group Exchange Rate",
		reference_name: "GER-0001",
		actor: "jane@example.com",
		actor_name: "Jane Doe",
		actor_missing: false,
		actor_persona: null,
		at: "2026-09-20T10:42:00+00:00",
		reason: null,
		detail: null,
		source: "live",
		...overrides,
	};
}

function payload(events = [], overrides = {}) {
	return {
		period: { fiscal_year: 2026, fiscal_period: 7, code: "FY2026 P07", status: "Open" },
		summary: {
			signoff: { state: "none" },
			closed: null,
			locked: null,
			counts: counts(),
		},
		events,
		hidden: 0,
		...overrides,
	};
}

// --- one payload per kind maps to its label and tone -----------------------

test("approved -> Approved, ok, and Prepared by <preparer>", () => {
	const v = trailView(payload([event({ kind: "approved", detail: { preparer: "owner@example.com" } })]), NOW, TZ);
	assert.equal(v.rows[0].label, "Approved");
	assert.equal(v.rows[0].tone, "ok");
	assert.equal(v.rows[0].detail, "Prepared by owner@example.com");
});

test("self_approved -> Self-approved, warn", () => {
	const v = trailView(payload([event({ kind: "self_approved" })]), NOW, TZ);
	assert.equal(v.rows[0].label, "Self-approved");
	assert.equal(v.rows[0].tone, "warn");
});

test("rejected -> Rejected, block", () => {
	const v = trailView(payload([event({ kind: "rejected" })]), NOW, TZ);
	assert.equal(v.rows[0].label, "Rejected");
	assert.equal(v.rows[0].tone, "block");
});

test("approval_cancelled gets its label and tone", () => {
	const v = trailView(
		payload([event({ kind: "approval_cancelled", reference_doctype: "Group Exchange Rate", reference_name: "GER-0007" })]),
		NOW,
		TZ
	);
	assert.equal(v.rows[0].label, "Approval cancelled");
	assert.equal(v.rows[0].tone, "warn");
	assert.equal(v.rows[0].item, "Group Exchange Rate GER-0007");
});

test("period_closed / period_locked / period_reopened labels and tones, with item from detail.period_code", () => {
	const closed = trailView(
		payload([event({ kind: "period_closed", detail: { period_code: "FY2026 P07", from: "Open" } })]),
		NOW,
		TZ
	);
	assert.equal(closed.rows[0].label, "Closed");
	assert.equal(closed.rows[0].tone, "mute");
	assert.equal(closed.rows[0].item, "Period FY2026 P07");

	const locked = trailView(payload([event({ kind: "period_locked", detail: { period_code: "FY2026 P07" } })]), NOW, TZ);
	assert.equal(locked.rows[0].label, "Locked");
	assert.equal(locked.rows[0].tone, "mute");

	const reopened = trailView(payload([event({ kind: "period_reopened", detail: { period_code: "FY2026 P07" } })]), NOW, TZ);
	assert.equal(reopened.rows[0].label, "Reopened");
	assert.equal(reopened.rows[0].tone, "warn");
});

test("year_* labels and tones, with item FY<fiscal_year> taken from payload.period, not an event field", () => {
	// The event carries no fiscal_year field at all -- _event_out never
	// sends one (trail_api.py:116-137). The item's year must come from
	// payload.period.fiscal_year (the server's period), here 2025, which
	// differs from NOW's year so a stray "current year" guess would also
	// be caught.
	const closed = trailView(
		payload([event({ kind: "year_closed" })], { period: { fiscal_year: 2025, fiscal_period: 0, code: "FY2025 P00", status: "Open" } }),
		NOW,
		TZ
	);
	assert.equal(closed.rows[0].label, "Year closed");
	assert.equal(closed.rows[0].tone, "mute");
	assert.equal(closed.rows[0].item, "FY2025");

	const locked = trailView(
		payload([event({ kind: "year_locked" })], { period: { fiscal_year: 2025, fiscal_period: 0, code: "FY2025 P00", status: "Open" } }),
		NOW,
		TZ
	);
	assert.equal(locked.rows[0].label, "Year locked");
	assert.equal(locked.rows[0].tone, "mute");

	const reopened = trailView(
		payload([event({ kind: "year_reopened" })], { period: { fiscal_year: 2025, fiscal_period: 0, code: "FY2025 P00", status: "Open" } }),
		NOW,
		TZ
	);
	assert.equal(reopened.rows[0].label, "Year reopened");
	assert.equal(reopened.rows[0].tone, "warn");
});

test("signed_off: Signed Off/Acknowledged/Overridden/unknown each get their label and tone", () => {
	const signed = trailView(payload([event({ kind: "signed_off", detail: { signoff_status: "Signed Off" } })]), NOW, TZ);
	assert.equal(signed.rows[0].label, "Signed off");
	assert.equal(signed.rows[0].tone, "ok");

	const ack = trailView(
		payload([event({ kind: "signed_off", reason: "seen it", detail: { signoff_status: "Acknowledged" } })]),
		NOW,
		TZ
	);
	assert.equal(ack.rows[0].label, "Acknowledged");
	assert.equal(ack.rows[0].tone, "warn");
	assert.equal(ack.rows[0].detail, 'Acknowledged: "seen it"');

	const over = trailView(
		payload([event({ kind: "signed_off", reason: "override", detail: { signoff_status: "Overridden" } })]),
		NOW,
		TZ
	);
	assert.equal(over.rows[0].label, "Overridden");
	assert.equal(over.rows[0].tone, "block");
	assert.equal(over.rows[0].detail, 'Reason: "override"');

	const rec = trailView(payload([event({ kind: "signed_off", detail: { signoff_status: "unknown" } })]), NOW, TZ);
	assert.equal(rec.rows[0].label, "Signed off (state not recorded)");
	assert.equal(rec.rows[0].tone, "warn");
});

test("signoff_voided -> Sign-off voided, warn", () => {
	const v = trailView(payload([event({ kind: "signoff_voided", reason: "data changed" })]), NOW, TZ);
	assert.equal(v.rows[0].label, "Sign-off voided");
	assert.equal(v.rows[0].tone, "warn");
});

test("tb_submitted: On behalf (warn), Submitted (mute), Submitted (on-behalf not recorded) (mute)", () => {
	const onBehalf = trailView(
		payload([event({ kind: "tb_submitted", entity: "ZZAA", detail: { on_behalf: "Yes" } })]),
		NOW,
		TZ
	);
	assert.equal(onBehalf.rows[0].label, "On behalf");
	assert.equal(onBehalf.rows[0].tone, "warn");
	assert.equal(onBehalf.rows[0].item, "Trial balance · ZZAA");

	const direct = trailView(
		payload([event({ kind: "tb_submitted", entity: "ZZAA", detail: { on_behalf: "No" } })]),
		NOW,
		TZ
	);
	assert.equal(direct.rows[0].label, "Submitted");
	assert.equal(direct.rows[0].tone, "mute");

	const blank = trailView(
		payload([event({ kind: "tb_submitted", entity: "ZZAA", detail: { on_behalf: "" } })]),
		NOW,
		TZ
	);
	assert.equal(blank.rows[0].label, "Submitted (on-behalf not recorded)");
	assert.equal(blank.rows[0].tone, "mute");
});

test("tb_cancelled / tb_exception_declared / tb_exception_cancelled labels and tones", () => {
	const cancelled = trailView(payload([event({ kind: "tb_cancelled", entity: "ZZAA" })]), NOW, TZ);
	assert.equal(cancelled.rows[0].label, "Cancelled");
	assert.equal(cancelled.rows[0].tone, "mute");

	const declared = trailView(
		payload([event({ kind: "tb_exception_declared", entity: "ZZAA", reason: "no TB this period" })]),
		NOW,
		TZ
	);
	assert.equal(declared.rows[0].label, "No-TB exception");
	assert.equal(declared.rows[0].tone, "warn");

	const excCancelled = trailView(payload([event({ kind: "tb_exception_cancelled", entity: "ZZAA" })]), NOW, TZ);
	assert.equal(excCancelled.rows[0].label, "Exception cancelled");
	assert.equal(excCancelled.rows[0].tone, "mute");
});

// --- ic_sent_back (konsol#305 X02, #305-W3-1) ---------------------------

test('ic_sent_back -> "Sent back", warn, and the pair in item and reason in detail', () => {
	const v = trailView(
		payload([
			event({
				kind: "ic_sent_back",
				reference_doctype: null,
				reference_name: null,
				reason: 'Our side agrees to INV-5531',
				detail: {
					entity_a: "UK01",
					account_a: "1810",
					entity_b: "DE01",
					account_b: "2810",
					groups: [{ consolidation_group: "GRP", difference: 120.5, tolerance: 50, match_status: "over_tolerance" }],
				},
			}),
		]),
		NOW,
		TZ
	);
	assert.equal(v.rows[0].label, "Sent back");
	assert.equal(v.rows[0].tone, "warn");
	assert.equal(v.rows[0].item, "Intercompany · UK01 1810 ↔ DE01 2810");
	assert.equal(v.rows[0].detail, 'Reason: "Our side agrees to INV-5531"');
});

test('failure path: ic_sent_back item text never contains "null"; a detail missing a key reads "not recorded"', () => {
	const v = trailView(
		payload([
			event({
				kind: "ic_sent_back",
				reference_doctype: null,
				reference_name: null,
				reason: "partial detail",
				detail: { entity_a: "UK01", account_a: "1810", entity_b: "DE01" },
			}),
		]),
		NOW,
		TZ
	);
	assert.equal(v.rows[0].item, "Intercompany pair (not recorded)");
	assert.ok(!v.rows[0].item.includes("null"));
});

// --- commentary_saved (konsol#305 M41, #305-W4-6) -----------------------

test('commentary_saved -> "Commentary", mute, heading in item, text in detail', () => {
	const v = trailView(
		payload([
			event({
				kind: "commentary_saved",
				reference_doctype: "Statement Commentary",
				reference_name: "SC-GRP-2026-7-4",
				detail: {
					consolidation_group: "GRP",
					heading: "4",
					heading_name: "NET SALES",
					text: "Volume down 4%",
				},
			}),
		]),
		NOW,
		TZ
	);
	assert.equal(v.rows[0].label, "Commentary");
	assert.equal(v.rows[0].tone, "mute");
	assert.equal(v.rows[0].item, "Commentary · NET SALES (4)");
	assert.ok(v.rows[0].detail.includes('Text: "Volume down 4%"'));
});

test('failure path: commentary_saved with no heading_name reads "heading not recorded", never "null null"', () => {
	const v = trailView(
		payload([
			event({
				kind: "commentary_saved",
				reference_doctype: "Statement Commentary",
				reference_name: "SC-GRP-2026-7-4",
				detail: { consolidation_group: "GRP", heading: "4", text: "Volume down 4%" },
			}),
		]),
		NOW,
		TZ
	);
	assert.equal(v.rows[0].item, "Commentary (heading not recorded)");
	assert.ok(!v.rows[0].item.includes("null"));
});

test("failure path: commentary_saved with a blank text reads 'Commentary cleared'", () => {
	const v = trailView(
		payload([
			event({
				kind: "commentary_saved",
				reference_doctype: "Statement Commentary",
				reference_name: "SC-GRP-2026-7-4",
				detail: { consolidation_group: "GRP", heading: "4", heading_name: "NET SALES", text: "" },
			}),
		]),
		NOW,
		TZ
	);
	assert.equal(v.rows[0].detail, "Commentary cleared");
});

// R41j (U8): `commentary_model.event_detail` always sets `"text": text or
// ""` (konsol/close/commentary_model.py:85-93) — the key is never omitted,
// even for a clearing save. A `commentary_saved` event whose detail has no
// `text` key at all is a broken contract, not a clear, and must throw
// rather than silently reading as "Commentary cleared" (a blank STRING
// still reads as cleared — tested above).
test("failure path: commentary_saved with no detail.text key throws, never reading as cleared", () => {
	const v = payload([
		event({
			kind: "commentary_saved",
			reference_doctype: "Statement Commentary",
			reference_name: "SC-GRP-2026-7-4",
			detail: { consolidation_group: "GRP", heading: "4", heading_name: "NET SALES" },
		}),
	]);
	assert.throws(() => trailView(v, NOW, TZ), /detail\.text/);
});

// --- failure paths -----------------------------------------------------

test("failure path: an unknown kind throws, naming it", () => {
	assert.throws(
		() => trailView(payload([event({ kind: "bogus_kind" })]), NOW, TZ),
		/bogus_kind/
	);
});

test('failure path: signed_off with signoff_status "Bogus" throws, and is never shown as "Signed off"', () => {
	assert.throws(
		() => trailView(payload([event({ kind: "signed_off", detail: { signoff_status: "Bogus" } })]), NOW, TZ),
		/Bogus/
	);
});

test("failure path: a payload with no summary throws", () => {
	const bad = payload([]);
	delete bad.summary;
	assert.throws(() => trailView(bad, NOW, TZ), /summary/);
});

test("failure path: a payload with no hidden key throws; the note is never guessed as 0", () => {
	const bad = payload([]);
	delete bad.hidden;
	assert.throws(() => trailView(bad, NOW, TZ), /hidden/);
});

// --- detail text, summary strip, hiddenNote -----------------------------

test("a backfilled self-approval's detail contains both Reason not recorded and Recovered from records", () => {
	const v = trailView(
		payload([
			event({
				kind: "self_approved",
				source: "backfill",
				detail: { preparer: "jane@example.com", reason_not_recorded: true },
			}),
		]),
		NOW,
		TZ
	);
	assert.ok(v.rows[0].detail.includes("Reason not recorded"));
	assert.ok(v.rows[0].detail.includes("Recovered from records"));
});

test("a voided summary gives Voided — <reason>", () => {
	const v = trailView(
		payload([], {
			summary: {
				signoff: { state: "voided", by: "jane@example.com", at: "2026-09-20T09:00:00+00:00", reason: "data changed" },
				closed: null,
				locked: null,
				counts: counts(),
			},
		}),
		NOW,
		TZ
	);
	assert.equal(v.signedOff, "Voided — data changed");
});

test("an empty event list gives rows: [] and Not signed off", () => {
	const v = trailView(payload([]), NOW, TZ);
	assert.deepEqual(v.rows, []);
	assert.equal(v.signedOff, "Not signed off");
});

test("hidden: 2 gives the note; hidden: 0 gives null", () => {
	const withHidden = trailView(payload([], { hidden: 2 }), NOW, TZ);
	assert.equal(withHidden.hiddenNote, "2 events for entities outside your scope are not shown");

	const noHidden = trailView(payload([], { hidden: 0 }), NOW, TZ);
	assert.equal(noHidden.hiddenNote, null);
});

test("a signed summary gives '<by> · <when>', the run result, and closedLocked/exceptions from counts", () => {
	const v = trailView(
		payload([], {
			summary: {
				signoff: {
					state: "signed",
					by: "jane@example.com",
					at: "2026-09-20T10:42:00+00:00",
					result: "Acknowledged",
					run_status: "Amber",
					reason: "seen it",
					warnings: "AssertionX",
				},
				closed: { by: "jane@example.com", at: "2026-09-20T11:00:00+00:00" },
				locked: { by: "jane@example.com", at: "2026-09-20T11:30:00+00:00" },
				counts: counts({ self_approvals: 2, on_behalf_uploads: 1, overrides: 0 }),
			},
		}),
		NOW,
		TZ
	);
	assert.equal(v.signedOff, "jane@example.com · 10:42");
	assert.equal(v.result, "Amber — Acknowledged · AssertionX");
	assert.equal(v.closedLocked, "Closed 11:00 · Locked 11:30");
	assert.equal(v.exceptions, "2 self-approval(s) · 1 on-behalf upload(s) · 0 override(s)");
});

test("T08d: a signed summary with by_name shows the name, never the raw login", () => {
	const v = trailView(
		payload([], {
			summary: {
				signoff: {
					state: "signed",
					by: "jane@example.com",
					by_name: "Jane Doe",
					by_missing: false,
					at: "2026-09-20T10:42:00+00:00",
					result: "Acknowledged",
					run_status: "Amber",
					reason: null,
					warnings: null,
				},
				closed: null,
				locked: null,
				counts: counts(),
			},
		}),
		NOW,
		TZ
	);
	assert.equal(v.signedOff, "Jane Doe · 10:42");
});

test("T08d: a signed summary with by_missing shows '<id> (user deleted)'", () => {
	const v = trailView(
		payload([], {
			summary: {
				signoff: {
					state: "signed",
					by: "ghost@example.com",
					by_name: "ghost@example.com",
					by_missing: true,
					at: "2026-09-20T10:42:00+00:00",
					result: "Acknowledged",
					run_status: "Amber",
					reason: null,
					warnings: null,
				},
				closed: null,
				locked: null,
				counts: counts(),
			},
		}),
		NOW,
		TZ
	);
	assert.equal(v.signedOff, "ghost@example.com (user deleted) · 10:42");
});

test("closedLocked is Open when neither closed nor locked is set", () => {
	const v = trailView(payload([]), NOW, TZ);
	assert.equal(v.closedLocked, "Open");
});

test("by() appends the persona label when actor_persona is set", () => {
	const v = trailView(payload([event({ actor_persona: "close_lead" })]), NOW, TZ);
	assert.equal(v.rows[0].by, "Jane Doe · Close Lead");
});

test("by() is the plain actor_name when actor_persona is null", () => {
	const v = trailView(payload([event({ actor_persona: null })]), NOW, TZ);
	assert.equal(v.rows[0].by, "Jane Doe");
});

test("failure path: an unknown persona throws", () => {
	assert.throws(
		() => trailView(payload([event({ actor_persona: "bogus_persona" })]), NOW, TZ),
		/bogus_persona/
	);
});

// --- parity with close_event_model.py -----------------------------------
//
// konsol#305 T08a instruction: "Every KIND in close_event_model.py must
// have a label, and an unknown kind must throw." This reads the Python
// module's KINDS tuple (never imported -- close-ui is JS) and checks that
// every name it declares resolves to a label in auditTrail.js, either
// statically (STATIC_KINDS) or via the two kinds whose label depends on
// detail (DYNAMIC_KINDS).

function pythonKinds() {
	const path = fileURLToPath(
		new URL("../../konsol/close/close_event_model.py", import.meta.url)
	);
	const source = readFileSync(path, "utf8");
	const match = source.match(/KINDS = \(([\s\S]*?)\)\n/);
	assert.ok(match, "close_event_model.py's KINDS tuple was not found");
	return [...match[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

test("every KINDS entry in close_event_model.py resolves to a label in auditTrail.js", () => {
	const kinds = pythonKinds();
	assert.ok(kinds.length > 0, "expected to find at least one declared kind");
	const known = new Set([...STATIC_KINDS, ...DYNAMIC_KINDS]);
	const missing = kinds.filter((k) => !known.has(k));
	assert.deepEqual(missing, []);
});

test("STATIC_KINDS and DYNAMIC_KINDS together name no kind close_event_model.py does not declare", () => {
	const kinds = new Set(pythonKinds());
	const extra = [...STATIC_KINDS, ...DYNAMIC_KINDS].filter((k) => !kinds.has(k));
	assert.deepEqual(extra, []);
});

// --- module hygiene -------------------------------------------------------

test("the source imports no vue, frappe or xstate", () => {
	const path = fileURLToPath(new URL("./auditTrail.js", import.meta.url));
	const source = readFileSync(path, "utf8");
	for (const term of ["vue", "frappe", "xstate"]) {
		assert.ok(!source.includes(`"${term}`) && !source.includes(`'${term}`), `unexpected import of ${term}`);
	}
});


// --- story 10.2: filters ----------------------------------------------------
//
// FILTERED is the real producer's output: trail_api.get_trail for a Viewer
// scoped to ZZA, filtered to approved + tb_submitted from 1 Sep
// (test_close_trail_api.py asserts it equals the fixture).

const FILTERED = JSON.parse(
	readFileSync(
		fileURLToPath(new URL("../../konsol/tests/fixtures/close_trail_filtered.json", import.meta.url)),
		"utf8",
	),
);

test("10.2: the producer's filtered payload renders its rows in the server's order", () => {
	const v = trailView(FILTERED, NOW, TZ);
	assert.deepEqual(v.rows.map((r) => r.name), FILTERED.events.map((e) => e.name));
	assert.deepEqual(v.rows.map((r) => r.label), ["Approved", "Submitted"]);
});

test("10.2: a filtered payload says how many of the scoped events it shows", () => {
	const v = trailView(FILTERED, NOW, TZ);
	assert.equal(v.countNote, `Showing ${FILTERED.events.length} of ${FILTERED.total} events`);
});

test("10.2: an unfiltered payload has no count note", () => {
	const v = trailView(payload([event()], { total: 1, filters: noFilters() }), NOW, TZ);
	assert.equal(v.countNote, null);
});

test("10.2 failure path: a payload with filters but no total throws; the count is never guessed", () => {
	const p = { ...FILTERED };
	delete p.total;
	assert.throws(() => trailView(p, NOW, TZ), /total/);
});

test("10.2: filterChoices labels the producer's options; the hidden entity is not among them", () => {
	const c = filterChoices(FILTERED);
	assert.deepEqual(c.kinds, [
		{ value: "approved", label: "Approved" },
		{ value: "signed_off", label: "Signed off" },
		{ value: "tb_submitted", label: "Submitted" },
		{ value: "year_closed", label: "Year closed" },
	]);
	assert.deepEqual(c.entities, [
		{ value: GROUP_LEVEL, label: "Group-level" },
		{ value: "ZZA", label: "ZZA" },
	]);
	assert.deepEqual(c.actors, [
		{ value: "zz-a@example.com", label: "A Accountant" },
		{ value: "zz-b@example.com", label: "B Lead" },
	]);
	assert.ok(!JSON.stringify(c).includes("ZZX"));
});

test("10.2: a deleted actor's choice shows the id and says so", () => {
	const c = filterChoices({
		...FILTERED,
		options: { ...FILTERED.options, actors: [{ actor: "gone@example.com", actor_name: "gone@example.com", actor_missing: true }] },
	});
	assert.deepEqual(c.actors, [{ value: "gone@example.com", label: "gone@example.com (user deleted)" }]);
});

test("10.2 failure path: filterChoices throws on an unknown kind and on a payload with no options", () => {
	assert.throws(
		() => filterChoices({ ...FILTERED, options: { ...FILTERED.options, kinds: ["bogus"] } }),
		/bogus/,
	);
	const p = { ...FILTERED };
	delete p.options;
	assert.throws(() => filterChoices(p), /options/);
});

test("10.2: filterParams sends lists as JSON and dates as given; an empty field is left out", () => {
	assert.deepEqual(filterParams(noFilters()), {});
	assert.deepEqual(
		filterParams({ kinds: ["approved"], actors: [], entities: ["ZZA", GROUP_LEVEL], date_from: "2026-09-01", date_to: "" }),
		{ kinds: '["approved"]', entities: '["ZZA","(group)"]', date_from: "2026-09-01" },
	);
});

test("10.2: filterParams of the server's echoed filters reproduces the request", () => {
	assert.deepEqual(filterParams(FILTERED.filters), {
		kinds: '["approved","tb_submitted"]',
		date_from: "2026-09-01",
	});
});

test("10.2: toggled adds a missing value and removes a present one, never mutating", () => {
	const list = ["a"];
	assert.deepEqual(toggled(list, "b"), ["a", "b"]);
	assert.deepEqual(toggled(list, "a"), []);
	assert.deepEqual(list, ["a"]);
});

test("10.2: GROUP_LEVEL matches trail_model.py's", () => {
	const source = readFileSync(
		fileURLToPath(new URL("../../konsol/close/trail_model.py", import.meta.url)),
		"utf8",
	);
	const m = source.match(/^GROUP_LEVEL = "([^"]*)"$/m);
	assert.ok(m, "trail_model.py declares GROUP_LEVEL");
	assert.equal(GROUP_LEVEL, m[1]);
});

// --- signoff_rejected (konsol#305 story 9.4, #157, #305-W5-1) -------------

test('signoff_rejected -> "Sign-off rejected", block, the run as item and the reason in detail', () => {
	const v = trailView(
		payload([
			event({
				kind: "signoff_rejected",
				reference_doctype: "Assertion Run",
				reference_name: "AR-0007",
				reason: "ZZA's TB is the draft",
				detail: { signoff_status: "Signed Off", preparer: "ana@example.com" },
			}),
		]),
		NOW,
		TZ
	);
	assert.equal(v.rows[0].label, "Sign-off rejected");
	assert.equal(v.rows[0].tone, "block");
	assert.equal(v.rows[0].item, "Assertion Run AR-0007");
	assert.equal(v.rows[0].detail, 'Reason: "ZZA\'s TB is the draft"');
});

test("a rejected summary gives Rejected — <reason> (#305-W5-1)", () => {
	const v = trailView(
		payload([], {
			summary: {
				signoff: { state: "rejected", by: "lead@example.com", at: "2026-09-20T09:00:00+00:00", reason: "ZZA's TB is the draft" },
				closed: null,
				locked: null,
				counts: counts(),
			},
		}),
		NOW,
		TZ
	);
	assert.equal(v.signedOff, "Rejected — ZZA's TB is the draft");
	assert.equal(v.result, null);
});
