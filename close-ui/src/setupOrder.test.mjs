// konsol#305 B30: no component's <script setup> uses a value before it is declared.
//
// C1 run 2: /close/<y>/<p>/sign-off threw `ReferenceError: Cannot access 'b'
// before initialization`. SignOff.vue's immediate watcher set `expanded.value`
// while `const expanded = ref({})` was declared further down. The other node
// tests never execute a component's setup, so nothing caught it.
//
// This test compiles every .vue under src with vue/compiler-sfc (`parse` +
// `compileScript`, which exposes the Babel AST of <script setup>) and, for the
// code each top-level statement runs WHILE setup runs, fails if that code reads
// a top-level `const` / `let` / `class` declared later (the temporal dead zone).
//
// Code that runs during setup:
// - a statement's own expressions, outside nested function bodies;
// - the source and callback of `watch(..., { immediate: true })`, and the
//   effect of `watchEffect` / `watchSyncEffect` / `watchPostEffect`;
// - an immediately invoked function expression;
// - the body of a top-level function that such code calls by name (followed
//   transitively, so `watch(src, load, { immediate: true })` checks `load`).
// A `computed(() => ...)` or an event handler runs later, after every
// declaration, so its body is not checked.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse, compileScript } from "vue/compiler-sfc";

const SRC_DIR = path.dirname(fileURLToPath(import.meta.url));

function vueFiles(dir) {
	const out = [];
	for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
		const full = path.join(dir, entry.name);
		if (entry.isDirectory()) out.push(...vueFiles(full));
		else if (entry.name.endsWith(".vue")) out.push(full);
	}
	return out.sort();
}

const FUNCTION_TYPES = new Set(["ArrowFunctionExpression", "FunctionExpression", "FunctionDeclaration", "ObjectMethod", "ClassMethod"]);
const EFFECT_CALLS = new Set(["watchEffect", "watchSyncEffect", "watchPostEffect"]);

/** Names bound by a declaration pattern (`a`, `{ a, b: c }`, `[a, ...b]`). */
function patternNames(node, out = []) {
	if (!node) return out;
	switch (node.type) {
		case "Identifier": out.push(node.name); break;
		case "ObjectPattern": for (const p of node.properties) patternNames(p.type === "RestElement" ? p.argument : p.value, out); break;
		case "ArrayPattern": for (const e of node.elements) patternNames(e, out); break;
		case "RestElement": patternNames(node.argument, out); break;
		case "AssignmentPattern": patternNames(node.left, out); break;
	}
	return out;
}

function childNodes(node) {
	const out = [];
	for (const key of Object.keys(node)) {
		if (key === "loc" || key === "start" || key === "end" || key === "extra" || key.endsWith("Comments")) continue;
		const v = node[key];
		if (Array.isArray(v)) { for (const c of v) if (c && typeof c.type === "string") out.push(c); }
		else if (v && typeof v.type === "string") out.push(v);
	}
	return out;
}

/** Names a nested function binds locally (its params and body declarations), which shadow top-level ones. */
function localNames(fn) {
	const names = new Set();
	for (const p of fn.params || []) for (const n of patternNames(p)) names.add(n);
	if (fn.id && fn.type !== "FunctionDeclaration") names.add(fn.id.name);
	const body = fn.body && fn.body.type === "BlockStatement" ? fn.body.body : [];
	for (const s of body) {
		if (s.type === "VariableDeclaration") for (const d of s.declarations) for (const n of patternNames(d.id)) names.add(n);
		if ((s.type === "FunctionDeclaration" || s.type === "ClassDeclaration") && s.id) names.add(s.id.name);
	}
	return names;
}

function isImmediateWatch(call) {
	if (call.callee.type !== "Identifier" || call.callee.name !== "watch") return false;
	const opts = call.arguments[2];
	return Boolean(opts && opts.type === "ObjectExpression" && opts.properties.some((p) =>
		p.type === "ObjectProperty" && !p.computed &&
		((p.key.type === "Identifier" && p.key.name === "immediate") || (p.key.type === "StringLiteral" && p.key.value === "immediate")) &&
		p.value.type === "BooleanLiteral" && p.value.value === true));
}

/**
 * Walk the code `node` runs now. `onRef(identifier)` is called for every read
 * of a name not shadowed; `runNow(fnNode)` is called for a function that runs
 * now (an immediate watcher's source/callback, an effect, an IIFE).
 */
function walkNow(node, shadow, onRef, runNow, onCallName) {
	if (!node || typeof node.type !== "string") return;
	if (FUNCTION_TYPES.has(node.type)) return; // defined now, runs later
	if (node.type === "ClassDeclaration" || node.type === "ClassExpression") {
		if (node.superClass) walkNow(node.superClass, shadow, onRef, runNow, onCallName);
		return;
	}
	if (node.type === "Identifier") {
		if (!shadow.has(node.name)) onRef(node);
		return;
	}
	if (node.type === "MemberExpression" || node.type === "OptionalMemberExpression") {
		walkNow(node.object, shadow, onRef, runNow, onCallName);
		if (node.computed) walkNow(node.property, shadow, onRef, runNow, onCallName);
		return;
	}
	if (node.type === "ObjectProperty") {
		if (node.computed) walkNow(node.key, shadow, onRef, runNow, onCallName);
		walkNow(node.value, shadow, onRef, runNow, onCallName);
		return;
	}
	if (node.type === "VariableDeclarator") {
		walkNow(node.init, shadow, onRef, runNow, onCallName);
		return;
	}
	if (node.type === "CallExpression" || node.type === "OptionalCallExpression") {
		const callee = node.callee;
		if (FUNCTION_TYPES.has(callee.type)) runNow(callee, shadow);
		if (callee.type === "Identifier" && !shadow.has(callee.name)) onCallName(callee.name);
		if (callee.type === "Identifier" && !shadow.has(callee.name) && EFFECT_CALLS.has(callee.name) && node.arguments[0] && FUNCTION_TYPES.has(node.arguments[0].type)) {
			runNow(node.arguments[0], shadow);
		}
		if (isImmediateWatch(node)) {
			for (const arg of node.arguments.slice(0, 2)) {
				if (FUNCTION_TYPES.has(arg.type)) runNow(arg, shadow);
				else if (arg.type === "Identifier" && !shadow.has(arg.name)) onCallName(arg.name);
			}
		}
	}
	for (const c of childNodes(node)) walkNow(c, shadow, onRef, runNow, onCallName);
}

/** Walk a function body as code that runs now (it was called during setup). */
function walkFunctionNow(fn, shadow, onRef, runNow, onCallName) {
	const inner = new Set([...shadow, ...localNames(fn)]);
	// Inside a running function, nested blocks run too; nested functions do not.
	walkNow(fn.body, inner, onRef, runNow, onCallName);
}

/**
 * Check one file. Returns { problems, immediateWatchers } where each problem
 * names the file, the identifier and both line numbers.
 */
function checkFile(file) {
	const source = fs.readFileSync(file, "utf8");
	const { descriptor, errors } = parse(source, { filename: file });
	assert.equal(errors.length, 0, `${file}: SFC parse errors: ${errors.map(String).join("; ")}`);
	if (!descriptor.scriptSetup) return { compiled: false, problems: [], immediateWatchers: 0 };
	const compiled = compileScript(descriptor, { id: path.basename(file) });
	const body = compiled.scriptSetupAst;
	assert.ok(Array.isArray(body) && body.length > 0, `${file}: compileScript exposed no <script setup> AST`);
	const lineOffset = descriptor.scriptSetup.loc.start.line - 1;
	const line = (n) => n.loc.start.line + lineOffset;
	const rel = path.relative(SRC_DIR, file);

	// Units: one per declarator (so `const a = 1, b = a` is ordered), else one per statement.
	const units = [];
	for (const stmt of body) {
		if (stmt.type === "VariableDeclaration") {
			for (const d of stmt.declarations) units.push({ node: d, kind: stmt.kind, names: patternNames(d.id), decl: d.id });
		} else if (stmt.type === "ClassDeclaration") {
			units.push({ node: stmt, kind: "class", names: [stmt.id.name], decl: stmt.id });
		} else if (stmt.type === "FunctionDeclaration") {
			units.push({ node: stmt, kind: "function", names: [stmt.id.name], decl: stmt.id, hoisted: true });
		} else {
			units.push({ node: stmt, kind: null, names: [], decl: null });
		}
	}
	const declaredAt = new Map(); // name -> { index, line, kind }
	const functions = new Map(); // name -> function node (declarations and `const f = () => ...`)
	units.forEach((u, index) => {
		for (const n of u.names) declaredAt.set(n, { index, line: line(u.decl), kind: u.kind });
		if (u.kind === "function") functions.set(u.names[0], u.node);
		else if (u.kind && u.node.init && FUNCTION_TYPES.has(u.node.init.type) && u.node.id.type === "Identifier") functions.set(u.names[0], u.node.init);
	});

	const problems = [];
	let immediateWatchers = 0;
	const countWatchers = (node) => {
		if (!node || typeof node.type !== "string") return;
		if (node.type === "CallExpression" && (isImmediateWatch(node) || (node.callee.type === "Identifier" && EFFECT_CALLS.has(node.callee.name)))) immediateWatchers += 1;
		for (const c of childNodes(node)) countWatchers(c);
	};

	units.forEach((u, index) => {
		if (u.hoisted) return; // a function declaration runs nothing where it stands
		countWatchers(u.node);
		const seen = new Set();
		const report = new Set();
		const onRef = (id) => {
			const d = declaredAt.get(id.name);
			if (!d || d.kind === "function" || d.index < index) return;
			const key = `${id.name}:${line(id)}`;
			if (report.has(key)) return;
			report.add(key);
			problems.push(`${rel}: '${id.name}' is used at line ${line(id)} (setup code of the statement at line ${line(u.node)}) before its declaration at line ${d.line}`);
		};
		const runNow = (fn, shadow) => walkFunctionNow(fn, shadow, onRef, runNow, onCallName);
		const onCallName = (name) => {
			const fn = functions.get(name);
			if (!fn || seen.has(name)) return;
			const d = declaredAt.get(name);
			if (d.kind !== "function" && d.index >= index) return; // the call itself is reported as a read
			seen.add(name);
			runNow(fn, new Set());
		};
		walkNow(u.node, new Set(), onRef, runNow, onCallName);
	});
	return { compiled: true, problems, immediateWatchers };
}

test("no <script setup> runs code that reads a top-level value declared after it", () => {
	const files = vueFiles(SRC_DIR);
	let compiledCount = 0;
	let watchers = 0;
	const problems = [];
	for (const f of files) {
		const r = checkFile(f);
		if (r.compiled) compiledCount += 1;
		watchers += r.immediateWatchers;
		problems.push(...r.problems);
	}
	// Guard: this test cannot pass by finding nothing to check.
	assert.ok(compiledCount > 10, `compiled only ${compiledCount} <script setup> file(s); expected more than 10`);
	assert.ok(watchers > 5, `found only ${watchers} immediate watcher(s)/effect(s); expected more than 5`);
	assert.deepEqual(problems, [], `values used before they are declared:\n${problems.join("\n")}`);
});

// The checker itself must catch each shape it claims to, and pass the safe ones.
function checkSource(script) {
	const dir = fs.mkdtempSync(path.join(process.env.TMPDIR || "/tmp", "setup-order-"));
	const file = path.join(dir, "Probe.vue");
	fs.writeFileSync(file, `<script setup>\n${script}\n</script>\n<template><div /></template>\n`);
	try {
		return checkFile(file).problems;
	} finally {
		fs.rmSync(dir, { recursive: true, force: true });
	}
}

test("the checker catches an immediate watcher callback reading a later const (the SignOff shape)", () => {
	const p = checkSource([
		'import { ref, watch } from "vue";',
		"const a = ref(1);",
		"watch(() => a.value, () => { expanded.value = {}; }, { immediate: true });",
		"const expanded = ref({});",
	].join("\n"));
	assert.equal(p.length, 1, p.join("\n"));
	assert.match(p[0], /'expanded' is used at line 4 .* before its declaration at line 5/);
	assert.match(p[0], /Probe\.vue/);
});

test("the checker catches an immediate watcher's source, watchEffect, a direct call and an IIFE", () => {
	const p = checkSource([
		'import { ref, watch, watchEffect } from "vue";',
		"watch(() => late1.value, () => {}, { immediate: true });",
		"watchEffect(() => { late2.value; });",
		"const x = late3 + 1;",
		"(() => late4)();",
		"const late1 = ref(0), late2 = ref(0), late3 = 1, late4 = 2;",
	].join("\n"));
	const names = p.map((s) => s.match(/'(\w+)'/)[1]).sort();
	assert.deepEqual(names, ["late1", "late2", "late3", "late4"], p.join("\n"));
});

test("the checker follows a named function an immediate watcher runs", () => {
	const p = checkSource([
		'import { ref, watch } from "vue";',
		"function load() { state.value = 1; }",
		"watch(() => 1, load, { immediate: true });",
		"const state = ref(0);",
	].join("\n"));
	assert.equal(p.length, 1, p.join("\n"));
	assert.match(p[0], /'state' is used at line 3 .* before its declaration at line 5/);
});

test("the checker passes deferred code, shadowed names and non-immediate watchers", () => {
	const p = checkSource([
		'import { computed, ref, watch } from "vue";',
		"const c = computed(() => later.value);",
		"watch(() => later.value, () => { later.value = 2; });",
		"function handler() { return later.value; }",
		"watch(() => 1, (later) => later, { immediate: true });",
		"const later = ref(1);",
	].join("\n"));
	assert.deepEqual(p, []);
});
