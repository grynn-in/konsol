<script setup>
/**
 * konsol#305 A17/A18: the Adjustments screen (E6; stories 6.1, 6.2; W2-10
 * Viewer reads; R2). A18 adds the draft editor ("New" / Edit), "Save draft"
 * and "Send for approval" on top of A17's read-only list and panel.
 *
 * Data:
 * - A05 GET `journal_api.get_journals(fiscal_year, fiscal_period)`, turned
 *   into `{period, journals, groups, accounts, workflowInstalled,
 *   firstState, canDraft, canSend, canEditPeriod}` by adjustments.js's
 *   `journalsView` (A13). The screen never decides a duration label, a
 *   status or a rejection's time itself: those come from the server through
 *   journalsView, which throws when asked for a time zone or `now` it was
 *   not given (shown through LoadState, never rendered as current).
 * - Selecting a journal opens a side panel: its lines, its effect per
 *   statement heading through `effectView` ("no heading" text comes from
 *   effectView itself, never a literal in this template), and the last
 *   rejection reason on a Draft. An `editable` journal (A13's `editable`)
 *   also offers an Edit button there.
 * - A06 POST `journal_api.save_journal`, through the one call site
 *   `saveDraft()`; its body comes only from `saveJournalBody` (A13). A07
 *   POST `journal_api.send_for_approval`, through the one call site
 *   `sendForApproval()`, carrying only the journal's name. Both reload
 *   `get_journals` once on success and call the injected `CONTEXT_RELOAD`
 *   once (Rates.vue's pattern). "New" renders only when `canDraft`, "Send
 *   for approval" only when `canSend` and the draft is saved.
 * - E6-P8: the effect panel is the saved journal's `effect`; a dirty,
 *   unsaved edit shows `effectView(null).note` instead.
 * - W43 (#305-W3-4, W4-E18): for a Draft/Pending journal (`docstatus === 0`,
 *   and not dirty), the effect panel instead shows Before/Change/After per
 *   heading from `beforeAfter(journal.effect, view)` (numbers.js, W42/N54):
 *   `before` is the statement's current figure, `change` reuses the exact
 *   display-sign multiplier the statement already computed for that
 *   heading, `after` is their sum — all in the statement's own display
 *   sign, never re-derived or hardcoded here. An Approved journal
 *   (`docstatus === 1`) is already in the last build, so showing
 *   before/after would double count: the panel shows "Included in the
 *   statement" plus its own per-heading effect list (`selectedEffect.
 *   headings`, same as the draft panel showed before W43 — U4) instead. A
 *   Cancelled journal (`docstatus === 2`) never reached a build at all, so
 *   it shows "Cancelled — not in the statement." instead, with no effect
 *   list (U4: W43 had shown both docstatus 1 and 2 as "Included", which was
 *   wrong for 2). The statement for the selected journal's period
 *   and group is read once per selection (its own seq guard, separate from
 *   the journals list's), and any of its non-ok states (or a GET failure)
 *   shows the message, never blank or zero columns.
 * - Amended 3 Oct: while `draftTotals(draft.lines).invalid` is non-empty,
 *   "Save draft" is disabled and the screen names the invalid line numbers;
 *   no request is sent. A line whose amount does not parse never reaches
 *   the server as 0 — the save is refused client-side before any POST.
 *
 * Who sees what (W2-10, #305-P21-1): the GET is open to every close role
 * (`JOURNAL_ROLES`, journal_api.py) except the Entity Accountant, who has no
 * journal permission; journals are not entity-scoped, so a Viewer reads
 * every journal. amended 3 Oct (E6-P1 option (c)): only the EPM Analyst and
 * System Manager draft or send (`canDraft`/`canSend` already fold this in).
 *
 * - #305 story 6.5: `view.reversingIn` (journals approved in an earlier
 *   period whose reversal posts into this one) renders as a read-only
 *   "Reversing into this period" section with its server label ("Reverses
 *   here from <code>"), the reversal posting's lines and its effect through
 *   `effectView`. The original period's list shows "Reverses in <code>" in
 *   the Duration column, as before.
 *
 * The period comes from the URL (route.js, D5); nothing is kept in the
 * browser. Not built here, and not rendered as dead controls: the Partner
 * column, Evidence/attach, and "Still in force from earlier periods"
 * (engineering call) — all P2 or belong to a later row.
 */
import { computed, inject, reactive, ref, onBeforeUnmount, watch } from "vue";
import { useRoute } from "vue-router";
import { Button, FeatherIcon } from "frappe-ui";
import LoadState from "../components/LoadState.vue";
import { get, post } from "../api.js";
import { parse } from "../route.js";
import {
	journalsView,
	effectView,
	durationOptions,
	durationIndex,
	draftTotals,
	saveJournalBody,
	editable,
	snapshotLines,
	snapshotDraft,
	draftDirty,
	canOpenNew,
	canSaveDraft,
	canSendDraft,
	dimKeysOf,
	dimValueText,
	NO_REVERSAL_NOTE,
} from "../adjustments.js";
import { beforeAfter, statementView } from "../numbers.js";
import { messageLines } from "../signoff.js";
import { userTimeZone } from "../timefmt.js";
import { CONTEXT_RELOAD } from "../contextRefresh.js";

const GET_JOURNALS = "konsol.close.journal_api.get_journals";
const SAVE_JOURNAL = "konsol.close.journal_api.save_journal";
const SEND = "konsol.close.journal_api.send_for_approval";
const GET_STATEMENT = "konsol.close.statement_api.get_statement";

//: the journal's Adjustment Type Select (journal_api.ADJUSTMENT_TYPES).
const ADJUSTMENT_TYPE_OPTIONS = [
	{ value: "topside", label: "Topside" },
	{ value: "reclassification", label: "Reclassification" },
];

//: E6-P15: the sentence send_for_approval (A07) refuses with when no
//: journal workflow is installed. Shown once, here, as a note — never
//: repeated per journal.
const NO_WORKFLOW_NOTE =
	"The journal workflow is not installed on this site (migrate installs it); the Close Lead approves a draft directly from Approvals.";
const NO_ZONE = "Your browser reported no time zone, so times cannot be shown.";

const route = useRoute();
const period = computed(() => {
	const p = parse(`/close${route.path}`);
	return p.error || p.year == null ? null : { year: p.year, period: p.period };
});
const periodName = computed(() =>
	period.value ? `FY${period.value.year} P${String(period.value.period).padStart(2, "0")}` : "this period",
);
const what = computed(() => `the adjustments for ${periodName.value}`);
const timeZone = userTimeZone();
// No default: a screen outside the shell is a wiring bug, and Vue warns about it.
const reloadContext = inject(CONTEXT_RELOAD);

const journals = reactive({ status: "loading", payload: null, error: null, busy: false });
let seq = 0;

// journalsView throws when it is not given a time zone or a valid `now`
// (mirrors Rates.vue's/Intercompany.vue's `view`, never swallowed). Moved
// above the draft editor's state (D04) so `dimKeys`/`declaredDimensions`,
// derived from it, exist before `blankLine()`'s first call.
const viewError = ref(null);
const view = computed(() => {
	if (journals.status !== "ready" || !journals.payload) return null;
	if (!timeZone) {
		viewError.value = NO_ZONE;
		return null;
	}
	try {
		viewError.value = null;
		return journalsView(journals.payload, new Date(), timeZone);
	} catch (e) {
		viewError.value = e.message;
		return null;
	}
});
const loadState = computed(() => {
	if (journals.status !== "ready") return journals.status;
	return view.value ? "ready" : "error";
});
const loadError = computed(() => viewError.value || journals.error);

//: konsolidat#245 option D (D04). `dimKeys` (adjustments.js's `dimKeysOf`)
//: is the one list every snapshot/dirty/save call and every new blank line
//: reads; `declaredDimensions` keeps `{key, label, suggestions}` for the
//: template (the picker's label and its datalist). Both are `[]` before the
//: list loads or when zero dimensions are declared, so the editor and the
//: panel render exactly as they did before option D (goal 4).
const dimKeys = computed(() => dimKeysOf(view.value));
const declaredDimensions = computed(() => (view.value ? view.value.dimensions : []));

const selectedName = ref(null);

function blankLine() {
	const line = { data_area_id: "", main_account: "", debit_amount: "", credit_amount: "", description: "" };
	for (const key of dimKeys.value) {
		line[key] = "";
	}
	return line;
}

const editorOpen = ref(false);
const draft = reactive({
	name: null,
	consolidation_group: "",
	adjustment_type: "topside",
	description: "",
	duration: { kind: "none" },
	lines: [blankLine()],
});
const draftError = ref(null);
const saving = ref(false);
const sending = ref(false);
//: U3: everything on screen right after it was last saved — a successful
//: save, or a journal `openEdit` just loaded (equally "last saved"). `null`
//: before any save: a fresh "New" draft is never dirty against nothing.
const savedSnapshot = ref(null);

const totals = computed(() => draftTotals(draft.lines));

const durationChoices = computed(() => durationOptions(view.value || { reversalChoices: [] }));
const selectedDurationIndex = computed(() => durationIndex(durationChoices.value, draft.duration));
function setDuration(i) {
	draft.duration = durationChoices.value[Number(i)] || { kind: "none" };
}

const selectedGroup = computed(() =>
	view.value ? (view.value.groups || []).find((g) => g.consolidation_group === draft.consolidation_group) : null,
);
const entityOptions = computed(() => (selectedGroup.value ? selectedGroup.value.entities : []));
const accountOptions = computed(() =>
	Object.entries((view.value && view.value.accounts) || {})
		.map(([code, acc]) => ({ code, label: `${code} — ${acc.account_name || code}` }))
		.sort((a, b) => a.code.localeCompare(b.code)),
);

function invalidLinesMessage(invalid) {
	if (!invalid.length) return null;
	const noun = invalid.length > 1 ? "Lines" : "Line";
	const verb = invalid.length > 1 ? "have" : "has";
	return `${noun} ${invalid.join(", ")} ${verb} an amount that is not a number: fix it before saving.`;
}

function openNew() {
	draftError.value = null;
	const groups = view.value ? view.value.groups : [];
	draft.name = null;
	draft.consolidation_group = groups && groups.length === 1 ? groups[0].consolidation_group : "";
	draft.adjustment_type = "topside";
	draft.description = "";
	draft.duration = { kind: "none" };
	draft.lines = [blankLine()];
	savedSnapshot.value = null;
	editorOpen.value = true;
	closePanel();
}

function openEdit(journal) {
	draftError.value = null;
	draft.name = journal.name;
	draft.consolidation_group = journal.consolidation_group;
	draft.adjustment_type = journal.adjustment_type;
	draft.description = journal.description || "";
	draft.duration = journal.reverse
		? { kind: "reverses", fiscal_year: journal.reverse.fiscal_year, fiscal_period: journal.reverse.fiscal_period }
		: { kind: "none" };
	draft.lines = journal.lines.length ? snapshotLines(journal.lines, dimKeys.value) : [blankLine()];
	//: U3: the just-loaded draft counts as "last saved" too.
	savedSnapshot.value = snapshotDraft(draft, dimKeys.value);
	editorOpen.value = true;
}

function closeEditor() {
	editorOpen.value = false;
	draftError.value = null;
}

function addLine() {
	draft.lines.push(blankLine());
}
function removeLine(idx) {
	if (draft.lines.length > 1) draft.lines.splice(idx, 1);
}

async function saveDraft() {
	draftError.value = null;
	const t = draftTotals(draft.lines);
	if (t.invalid.length) {
		draftError.value = invalidLinesMessage(t.invalid);
		return;
	}
	if (!period.value || saving.value) return;
	if (!view.value || !view.value.canEditPeriod) return;
	const periodKey = { fiscal_year: period.value.year, fiscal_period: period.value.period };
	//: U11: a late save (started in an open period) must not set draft.name
	//: from a now-stale result if the route's period changed meanwhile.
	const startedPeriod = `${periodKey.fiscal_year}/${periodKey.fiscal_period}`;
	saving.value = true;
	try {
		const body = saveJournalBody(periodKey, draft, dimKeys.value);
		const result = await post(SAVE_JOURNAL, body);
		const nowPeriod = period.value ? `${period.value.year}/${period.value.period}` : null;
		if (nowPeriod !== startedPeriod) return;
		draft.name = result.name;
		savedSnapshot.value = snapshotDraft(draft, dimKeys.value);
		reloadContext();
		await loadJournals({ quiet: true });
	} catch (e) {
		draftError.value = e.message;
	} finally {
		saving.value = false;
	}
}

async function sendForApproval() {
	if (!draft.name || sending.value) return;
	if (!view.value || !view.value.canEditPeriod || !view.value.canSend || editorDirty.value) return;
	draftError.value = null;
	sending.value = true;
	try {
		await post(SEND, { name: draft.name });
		reloadContext();
		await loadJournals({ quiet: true });
		closeEditor();
	} catch (e) {
		draftError.value = e.message;
	} finally {
		sending.value = false;
	}
}

const journalCountText = computed(() => {
	const n = view.value ? view.value.journals.length : 0;
	return `${n} this period`;
});

const selectedJournal = computed(() => {
	if (!selectedName.value || !view.value) return null;
	return view.value.journals.find((j) => j.name === selectedName.value) || null;
});
//: E6-P8/U3: while the editor's state differs from `savedSnapshot` in any
//: field (not lines alone), the effect panel shows `effectView(null).note`
//: rather than the stale saved effect, and Send is disabled (`sendAllowed`
//: below) — both read this one flag.
const editorDirty = computed(() => {
	if (!editorOpen.value) return false;
	return draftDirty(savedSnapshot.value, draft, dimKeys.value);
});
const selectedEffect = computed(() => {
	if (!selectedJournal.value) return null;
	return effectView(editorDirty.value ? null : selectedJournal.value.effect);
});

//: W43: the statement for the selected Draft/Pending journal's period and
//: group (#305-W3-4, W4-E18) — a second, independent load from the
//: journals list's own `journals`/`seq`, with its own stale-response guard
//: (`statementSeq`). Only fetched while the side panel holds a journal
//: with `docstatus === 0`: an approved/reversed journal shows "Included in
//: the statement" instead and needs no GET at all.
const statement = reactive({ status: "idle", payload: null, error: null, now: null });
let statementSeq = 0;

//: `null` while no fetch is needed (no selection, an approved/reversed
//: journal, or no period/group to ask for) — the watch below treats `null`
//: as "nothing to load" and resets `statement`.
const statementRequestKey = computed(() => {
	const j = selectedJournal.value;
	if (!j || j.docstatus !== 0 || !period.value || !j.consolidation_group) return null;
	return `${period.value.year}/${period.value.period}/${j.consolidation_group}`;
});

async function loadStatement(fiscalYear, fiscalPeriod, consolidationGroup) {
	const mine = ++statementSeq;
	statement.status = "loading";
	try {
		const payload = await get(GET_STATEMENT, {
			fiscal_year: fiscalYear,
			fiscal_period: fiscalPeriod,
			consolidation_group: consolidationGroup,
		});
		if (mine !== statementSeq) return;
		statement.payload = payload;
		statement.error = null;
		statement.now = new Date();
		statement.status = "ready";
	} catch (e) {
		if (mine !== statementSeq) return;
		statement.error = e.message;
		statement.status = "error";
	}
}

watch(
	statementRequestKey,
	(key) => {
		statement.payload = null;
		statement.error = null;
		statement.now = null;
		if (!key) {
			statement.status = "idle";
			statementSeq++; // drop any response still in flight
			return;
		}
		statement.status = "loading";
		loadStatement(period.value.year, period.value.period, selectedJournal.value.consolidation_group);
	},
	{ immediate: true },
);

//: statementView throws on anything it does not recognise (mirrors
//: Rates.vue's/Intercompany.vue's/Numbers.vue's own `view`, never
//: swallowed): recorded here and shown as the panel's message, same as a
//: GET failure.
const statementViewError = ref(null);
const statementViewResult = computed(() => {
	if (statement.status !== "ready" || !statement.payload) return null;
	if (!timeZone) {
		statementViewError.value = NO_ZONE;
		return null;
	}
	try {
		statementViewError.value = null;
		return statementView(statement.payload, statement.now || new Date(), timeZone);
	} catch (e) {
		statementViewError.value = e.message;
		return null;
	}
});

//: The Before/After panel's one view: `{kind: "loading"}` while the GET is
//: in flight, `{kind: "message", text}` for a GET failure, a thrown
//: statementView, or the statement's own non-ok business state (never a
//: guessed breakdown next to an error), or `{kind: "rows", rows}` once
//: `beforeAfter` (numbers.js, W42/N54) has something real to show. `null`
//: when Before/After does not apply here at all (no selection, or an
//: approved/reversed journal — the template shows "Included in the
//: statement" in that case, never this computed).
const beforeAfterPanel = computed(() => {
	const j = selectedJournal.value;
	if (!j || j.docstatus !== 0) return null;
	if (statement.status === "loading" || statement.status === "idle") {
		return { kind: "loading" };
	}
	if (statement.status === "error") {
		return { kind: "message", text: statement.error };
	}
	if (statementViewError.value) {
		return { kind: "message", text: statementViewError.value };
	}
	const view = statementViewResult.value;
	if (!view) {
		return { kind: "loading" };
	}
	if (view.state) {
		return { kind: "message", text: view.state.message };
	}
	return { kind: "rows", rows: beforeAfter(j.effect, view) };
});

//: U4: `can_draft`/`can_send` ignore period status; the screen gates New,
//: Save and Send on `canEditPeriod` too (adjustments.js).
const newAllowed = computed(() => canOpenNew(view.value));
const saveAllowed = computed(() => canSaveDraft(view.value, totals.value));
const sendAllowed = computed(() => canSendDraft(view.value, draft, editorDirty.value));

//: Edit shown only on an `editable` journal (A13).
const canEditSelected = computed(() => {
	if (!selectedJournal.value || !view.value) return false;
	return editable(selectedJournal.value, view.value);
});

function selectJournal(journal) {
	selectedName.value = journal.name;
}
function closePanel() {
	selectedName.value = null;
}

async function loadJournals({ quiet = false } = {}) {
	if (!period.value) {
		journals.status = "error";
		journals.error = "This address names no period.";
		return;
	}
	const mine = ++seq;
	if (!quiet) journals.status = "loading";
	journals.busy = true;
	try {
		const payload = await get(GET_JOURNALS, {
			fiscal_year: period.value.year,
			fiscal_period: period.value.period,
		});
		if (mine !== seq) return;
		journals.payload = payload;
		journals.error = null;
		journals.status = "ready";
	} catch (e) {
		if (mine !== seq) return;
		journals.error = e.message;
		journals.status = "error";
	} finally {
		if (mine === seq) journals.busy = false;
	}
}

watch(
	() => (period.value ? `${period.value.year}/${period.value.period}` : null),
	() => {
		journals.payload = null;
		closePanel();
		closeEditor();
		loadJournals();
	},
	{ immediate: true },
);

onBeforeUnmount(() => {
	seq++;
	statementSeq++;
});

function lines(text) {
	const out = messageLines(text);
	return out.length ? out : ["The server gave no reason."];
}
</script>

<template>
	<div class="mx-auto max-w-6xl px-6 py-6">
		<header class="flex flex-wrap items-start justify-between gap-3">
			<div>
				<h1 class="text-xl font-semibold text-ink-gray-9">Adjustments</h1>
				<p class="mt-1 text-sm text-ink-gray-6">{{ periodName }} · {{ journalCountText }}</p>
			</div>
			<Button v-if="newAllowed" theme="gray" variant="solid" :disabled="editorOpen" @click="openNew">New</Button>
		</header>

		<p
			v-if="view && !view.workflowInstalled"
			role="status"
			class="mt-3 rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
		>
			{{ NO_WORKFLOW_NOTE }}
		</p>

		<!-- Draft editor (A18): "New" above, or Edit from the side panel below. -->
		<section
			v-if="editorOpen"
			class="mt-4 rounded border border-outline-gray-2 bg-surface-white p-4"
		>
			<div class="flex items-start justify-between gap-2">
				<h2 class="text-base font-semibold text-ink-gray-9">{{ draft.name ? draft.name : "New adjustment" }}</h2>
				<Button variant="ghost" size="sm" aria-label="Close editor" @click="closeEditor">
					<FeatherIcon name="x" class="h-4 w-4" />
				</Button>
			</div>

			<p v-if="draftError" role="alert" class="mt-2 rounded border border-outline-red-1 bg-surface-red-1 px-3 py-2 text-sm text-ink-gray-8">
				<span v-for="(line, i) in lines(draftError)" :key="i" class="block">{{ line }}</span>
			</p>

			<div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
				<label class="flex flex-col gap-1 text-sm">
					<span class="text-ink-gray-6">Type</span>
					<select v-model="draft.adjustment_type" class="rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8">
						<option v-for="opt in ADJUSTMENT_TYPE_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
					</select>
				</label>
				<label class="flex flex-col gap-1 text-sm">
					<span class="text-ink-gray-6">Group</span>
					<select v-model="draft.consolidation_group" class="rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8">
						<option value="" disabled>Choose a group</option>
						<option v-for="g in (view ? view.groups : [])" :key="g.consolidation_group" :value="g.consolidation_group">{{ g.consolidation_group }}</option>
					</select>
				</label>
				<label class="flex flex-col gap-1 text-sm sm:col-span-2">
					<span class="text-ink-gray-6">Why</span>
					<textarea v-model="draft.description" rows="2" class="rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"></textarea>
				</label>
				<label class="flex flex-col gap-1 text-sm">
					<span class="text-ink-gray-6">Duration</span>
					<select :value="selectedDurationIndex" class="rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8" @change="setDuration($event.target.value)">
						<option v-for="(opt, i) in durationChoices" :key="i" :value="i">{{ opt.label }}</option>
					</select>
					<span v-if="durationChoices.length <= 1" class="text-xs text-ink-gray-5">{{ NO_REVERSAL_NOTE }}</span>
				</label>
			</div>

			<div class="mt-4">
				<h3 class="mb-2 text-sm font-semibold text-ink-gray-9">Lines</h3>
				<table class="w-full text-left text-sm">
					<thead class="text-xs uppercase tracking-wide text-ink-gray-6">
						<tr>
							<th class="py-1 font-medium">Entity</th>
							<th class="py-1 font-medium">Account</th>
							<th class="py-1 font-medium">Debit</th>
							<th class="py-1 font-medium">Credit</th>
							<th class="py-1 font-medium">Description</th>
							<th v-for="dim in declaredDimensions" :key="dim.key" class="py-1 font-medium">{{ dim.label }}</th>
							<th class="py-1 font-medium"></th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="(line, idx) in draft.lines" :key="idx" class="border-t border-outline-gray-2">
							<td class="py-1 pr-1">
								<select v-model="line.data_area_id" :aria-label="`Entity, line ${idx + 1}`" class="w-full rounded border border-outline-gray-2 bg-surface-white px-1 py-1 text-ink-gray-8">
									<option value="" disabled>Entity</option>
									<option v-for="e in entityOptions" :key="e" :value="e">{{ e }}</option>
								</select>
							</td>
							<td class="py-1 pr-1">
								<select v-model="line.main_account" :aria-label="`Account, line ${idx + 1}`" class="w-full rounded border border-outline-gray-2 bg-surface-white px-1 py-1 text-ink-gray-8">
									<option value="" disabled>Account</option>
									<option v-for="a in accountOptions" :key="a.code" :value="a.code">{{ a.label }}</option>
								</select>
							</td>
							<td class="py-1 pr-1">
								<input v-model="line.debit_amount" type="text" inputmode="decimal" :aria-label="`Debit, line ${idx + 1}`" class="w-24 rounded border border-outline-gray-2 bg-surface-white px-1 py-1 text-right font-mono text-ink-gray-8" />
							</td>
							<td class="py-1 pr-1">
								<input v-model="line.credit_amount" type="text" inputmode="decimal" :aria-label="`Credit, line ${idx + 1}`" class="w-24 rounded border border-outline-gray-2 bg-surface-white px-1 py-1 text-right font-mono text-ink-gray-8" />
							</td>
							<td class="py-1 pr-1">
								<input v-model="line.description" type="text" :aria-label="`Description, line ${idx + 1}`" class="w-full rounded border border-outline-gray-2 bg-surface-white px-1 py-1 text-ink-gray-8" />
							</td>
							<td v-for="dim in declaredDimensions" :key="dim.key" class="py-1 pr-1">
								<input
									v-model="line[dim.key]"
									type="text"
									:list="`dim-list-${dim.key}`"
									:aria-label="`${dim.label}, line ${idx + 1}`"
									class="w-full rounded border border-outline-gray-2 bg-surface-white px-1 py-1 text-ink-gray-8"
								/>
							</td>
							<td class="py-1">
								<Button variant="ghost" size="sm" aria-label="Remove line" :disabled="draft.lines.length <= 1" @click="removeLine(idx)">
									<FeatherIcon name="trash-2" class="h-4 w-4" />
								</Button>
							</td>
						</tr>
					</tbody>
				</table>
				<!-- D04: one shared <datalist> per declared dimension (suggestions only
				     — never a refusal of a typed value outside the list, konsol#247). -->
				<datalist v-for="dim in declaredDimensions" :key="dim.key" :id="`dim-list-${dim.key}`">
					<option v-for="s in dim.suggestions" :key="s" :value="s" />
				</datalist>
				<Button class="mt-2" variant="outline" size="sm" @click="addLine">Add line</Button>
			</div>

			<p class="mt-3 text-sm text-ink-gray-7">
				Dr {{ totals.debit.toFixed(2) }} · Cr {{ totals.credit.toFixed(2) }} ·
				<span v-if="totals.balanced" class="font-medium text-ink-green-4">Balanced</span>
				<span v-else class="font-medium text-ink-amber-4">Out of balance by {{ totals.difference.toFixed(2) }}</span>
			</p>
			<p v-if="totals.invalid.length" role="alert" class="mt-1 text-sm text-ink-red-4">{{ invalidLinesMessage(totals.invalid) }}</p>

			<div class="mt-4 flex flex-wrap items-center gap-2">
				<Button theme="gray" variant="solid" :loading="saving" :disabled="saving || !saveAllowed" @click="saveDraft">Save draft</Button>
				<Button v-if="sendAllowed" variant="outline" :loading="sending" :disabled="sending" @click="sendForApproval">Send for approval</Button>
				<span v-else-if="draft.name && editorDirty" class="text-xs text-ink-gray-5">Save your changes before sending.</span>
				<span class="text-xs text-ink-gray-5">The Close Lead approves. Only an open period accepts it.</span>
			</div>
		</section>

		<LoadState class="mt-4" :state="loadState" :what="what" :source="GET_JOURNALS" :error="loadError" :busy="journals.busy" @retry="loadJournals">
			<template v-if="view">
				<p
					v-if="!view.journals.length"
					class="rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
				>
					No journals for {{ periodName }}.
				</p>
				<div v-else class="overflow-x-auto rounded border border-outline-gray-2">
					<table class="w-full text-left text-sm">
						<thead class="bg-surface-gray-1 text-xs uppercase tracking-wide text-ink-gray-6">
							<tr>
								<th class="px-3 py-2 font-medium">Title</th>
								<th class="px-3 py-2 font-medium">Type</th>
								<th class="px-3 py-2 font-medium">Duration</th>
								<th class="px-3 py-2 font-medium">Status</th>
								<th class="px-3 py-2 font-medium">Totals</th>
								<th class="px-3 py-2 font-medium">Prepared by</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="journal in view.journals"
								:key="journal.name"
								class="cursor-pointer border-t border-outline-gray-2 hover:bg-surface-gray-1"
								:class="selectedName === journal.name ? 'bg-surface-gray-2' : ''"
								tabindex="0"
								role="button"
								:aria-label="`${journal.title}, ${journal.status}`"
								@click="selectJournal(journal)"
								@keyup.enter="selectJournal(journal)"
							>
								<td class="px-3 py-2 font-medium text-ink-gray-9">{{ journal.title }}</td>
								<td class="px-3 py-2 capitalize text-ink-gray-7">{{ journal.adjustment_type }}</td>
								<td class="px-3 py-2 text-ink-gray-7">{{ journal.duration }}</td>
								<td class="px-3 py-2">
									<span class="inline-block rounded bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7">{{ journal.status }}</span>
								</td>
								<td class="px-3 py-2 font-mono text-ink-gray-8">{{ journal.totalsText }}</td>
								<td class="px-3 py-2 text-ink-gray-7">{{ journal.preparer }}</td>
							</tr>
						</tbody>
					</table>
				</div>

				<!-- #305 story 6.5: journals approved in an earlier period whose
				     reversal posts into this one. Read-only: the warehouse build
				     posts the reversal from the approved journal; label, lines and
				     effect all come from get_journals. -->
				<section v-if="view.reversingIn.length" aria-label="Reversing into this period" class="mt-6">
					<h2 class="mb-2 text-sm font-semibold text-ink-gray-9">Reversing into this period</h2>
					<p class="mb-2 text-xs text-ink-gray-5">Read-only: posted here from a journal approved in an earlier period.</p>
					<details
						v-for="item in view.reversingIn"
						:key="item.name"
						class="mb-2 rounded border border-outline-gray-2 px-3 py-2 text-sm"
					>
						<summary class="cursor-pointer">
							<span class="font-medium text-ink-gray-9">{{ item.title }}</span>
							<span class="ml-2 inline-block rounded bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7">{{ item.label }}</span>
							<span class="ml-2 font-mono text-ink-gray-8">{{ item.totalsText }}</span>
							<span class="ml-2 text-xs text-ink-gray-6">{{ item.name }} · approved by {{ item.approved_by }} · {{ item.approvedAtText }}</span>
						</summary>
						<table class="mt-2 w-full text-left text-sm">
							<thead class="text-xs uppercase tracking-wide text-ink-gray-6">
								<tr>
									<th class="py-1 font-medium">Entity</th>
									<th class="py-1 font-medium">Account</th>
									<th class="py-1 font-medium">Dr</th>
									<th class="py-1 font-medium">Cr</th>
									<th v-for="dim in declaredDimensions" :key="dim.key" class="py-1 font-medium">{{ dim.label }}</th>
								</tr>
							</thead>
							<tbody>
								<tr v-for="line in item.lines" :key="line.idx" class="border-t border-outline-gray-2">
									<td class="py-1 text-ink-gray-8">{{ line.data_area_id }}</td>
									<td class="py-1 text-ink-gray-8">{{ line.account_name || line.main_account }}</td>
									<td class="py-1 font-mono text-ink-gray-8">{{ line.debit_amount || "" }}</td>
									<td class="py-1 font-mono text-ink-gray-8">{{ line.credit_amount || "" }}</td>
									<td v-for="dim in declaredDimensions" :key="dim.key" class="py-1 text-ink-gray-8">{{ dimValueText(line, dim.key) }}</td>
								</tr>
							</tbody>
						</table>
						<h3 class="mt-2 text-xs font-semibold uppercase tracking-wide text-ink-gray-6">Effect</h3>
						<ul class="mt-1 space-y-1">
							<li
								v-for="(heading, i) in item.effectView.headings"
								:key="i"
								class="flex items-center justify-between gap-2"
							>
								<span class="text-ink-gray-7">{{ heading.label }}</span>
								<span class="font-mono text-ink-gray-8">{{ heading.amountText }}</span>
							</li>
						</ul>
						<p v-if="item.effectView.noHeadingText" class="mt-1 text-xs text-ink-gray-5">
							{{ item.effectView.noHeadingText }}
						</p>
					</details>
				</section>
			</template>
		</LoadState>

		<!-- Side panel: the selected journal's lines, effect and last rejection -->
		<div
			v-if="selectedJournal"
			role="dialog"
			aria-label="Journal detail"
			class="fixed inset-y-0 right-0 z-10 w-full max-w-md overflow-y-auto border-l border-outline-gray-2 bg-surface-white p-5 shadow-lg"
		>
			<div class="flex items-start justify-between gap-2">
				<h2 class="text-base font-semibold text-ink-gray-9">{{ selectedJournal.title }}</h2>
				<div class="flex items-center gap-1">
					<Button v-if="canEditSelected" size="sm" @click="openEdit(selectedJournal)">Edit</Button>
					<Button variant="ghost" size="sm" aria-label="Close" @click="closePanel">
						<FeatherIcon name="x" class="h-4 w-4" />
					</Button>
				</div>
			</div>
			<p class="mt-1 text-sm text-ink-gray-6">{{ selectedJournal.status }} · {{ selectedJournal.duration }}</p>

			<div
				v-if="selectedJournal.docstatus === 0 && selectedJournal.lastRejection"
				class="mt-3 rounded border border-outline-amber-1 bg-surface-amber-1 px-3 py-2 text-sm text-ink-amber-3"
			>
				<p class="text-xs uppercase tracking-wide">Last rejection</p>
				<p v-for="(line, i) in lines(selectedJournal.lastRejection.reason)" :key="i">{{ line }}</p>
				<p class="mt-1 text-xs">{{ selectedJournal.lastRejection.actor }} · {{ selectedJournal.lastRejection.at }}</p>
			</div>

			<section class="mt-4">
				<h3 class="mb-2 text-sm font-semibold text-ink-gray-9">Lines</h3>
				<div v-if="!selectedJournal.lines.length" class="text-sm text-ink-gray-6">No lines.</div>
				<table v-else class="w-full text-left text-sm">
					<thead class="text-xs uppercase tracking-wide text-ink-gray-6">
						<tr>
							<th class="py-1 font-medium">Entity</th>
							<th class="py-1 font-medium">Account</th>
							<th class="py-1 font-medium">Dr</th>
							<th class="py-1 font-medium">Cr</th>
							<th v-for="dim in declaredDimensions" :key="dim.key" class="py-1 font-medium">{{ dim.label }}</th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="line in selectedJournal.lines" :key="line.idx" class="border-t border-outline-gray-2">
							<td class="py-1 text-ink-gray-8">{{ line.data_area_id }}</td>
							<td class="py-1 text-ink-gray-8">{{ line.account_name || line.main_account }}</td>
							<td class="py-1 font-mono text-ink-gray-8">{{ line.debit_amount || "" }}</td>
							<td class="py-1 font-mono text-ink-gray-8">{{ line.credit_amount || "" }}</td>
							<td v-for="dim in declaredDimensions" :key="dim.key" class="py-1 text-ink-gray-8">{{ dimValueText(line, dim.key) }}</td>
						</tr>
					</tbody>
				</table>
			</section>

			<section v-if="selectedEffect" class="mt-4">
				<h3 class="mb-2 text-sm font-semibold text-ink-gray-9">Effect</h3>
				<p v-if="selectedEffect.note" class="text-sm text-ink-gray-6">{{ selectedEffect.note }}</p>
				<template v-else>
					<!-- W43: a Draft/Pending journal (not yet approved) shows
					     Before/Change/After per heading, read off the real
					     statement through beforeAfter (numbers.js, W42/N54); an
					     approved or reversed journal is already in the last
					     build, so before/after would double count (W4-E18). -->
					<template v-if="selectedJournal.docstatus === 0">
						<p v-if="beforeAfterPanel && beforeAfterPanel.kind === 'loading'" class="text-sm text-ink-gray-6">
							Loading the statement…
						</p>
						<p v-else-if="beforeAfterPanel && beforeAfterPanel.kind === 'message'" class="text-sm text-ink-gray-6">
							{{ beforeAfterPanel.text }}
						</p>
						<template v-else-if="beforeAfterPanel">
							<div v-if="!beforeAfterPanel.rows.length" class="text-sm text-ink-gray-6">No effect.</div>
							<table v-else class="w-full text-left text-sm">
								<thead class="text-xs uppercase tracking-wide text-ink-gray-6">
									<tr>
										<th class="py-1 font-medium">Heading</th>
										<th class="py-1 font-medium text-right">Before</th>
										<th class="py-1 font-medium text-right">Change</th>
										<th class="py-1 font-medium text-right">After</th>
									</tr>
								</thead>
								<tbody>
									<tr
										v-for="(row, i) in beforeAfterPanel.rows"
										:key="i"
										class="border-t border-outline-gray-2"
									>
										<td class="py-1 text-ink-gray-7">{{ row.headingName }}</td>
										<td class="py-1 text-right font-mono text-ink-gray-8">{{ row.before }}</td>
										<td class="py-1 text-right font-mono text-ink-gray-8">{{ row.change ?? "—" }}</td>
										<td class="py-1 text-right font-mono text-ink-gray-8">{{ row.after ?? "—" }}</td>
									</tr>
								</tbody>
							</table>
						</template>
					</template>
					<!-- U4 (W4 review): docstatus 2 (cancelled/reversed-away) never
					     reached the last build, so it is never "included" — distinct
					     from docstatus 1 (approved), which is already in the last
					     build and shows the per-heading effect list it showed before
					     W43 (U4's "judgement" note), restored here rather than left
					     as the bare sentence W43 left in its place. -->
					<p v-else-if="selectedJournal.docstatus === 2" class="text-sm text-ink-gray-6">
						Cancelled — not in the statement.
					</p>
					<template v-else>
						<p class="text-sm text-ink-gray-6">Included in the statement.</p>
						<div v-if="!selectedEffect.headings.length" class="text-sm text-ink-gray-6">No effect.</div>
						<ul v-else class="mt-2 space-y-1 text-sm">
							<li v-for="(heading, i) in selectedEffect.headings" :key="i" class="flex items-center justify-between gap-2">
								<span class="text-ink-gray-7">{{ heading.label }}</span>
								<span class="font-mono text-ink-gray-8">{{ heading.amountText }}</span>
							</li>
						</ul>
					</template>
					<p
						v-if="selectedEffect.noHeadingText && selectedJournal.docstatus !== 2"
						class="mt-1 text-xs text-ink-gray-5"
					>
						{{ selectedEffect.noHeadingText }}
					</p>
				</template>
			</section>
		</div>
	</div>
</template>
