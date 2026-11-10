// One esbuild bundle for the Desk period strips (Deepak Pai, 10 Nov 2026):
// Frappe hashes it into a dist file and resolves the bare name through
// assets.json, so a deploy cannot leave browsers on a cached stale copy.
// Imported for side effects, in dependency order; each file still works
// as a plain script and as a node module (the tests).
import "./period_strip_model.js";
import "./report_filter_memory.js";
import "./period_strip.js";
import "./period_reports.js";
