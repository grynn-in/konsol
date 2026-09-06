/**
 * Mock konsol control plane for developing konsol-exec on its own.
 *
 * The real console needs nine containers — MariaDB, Redis x2, ClickHouse,
 * three Frappe processes, Cube and Caddy — which is a lot to stand up to
 * change a button. This serves the built SPA plus fake responses in the exact
 * shapes `konsol.control_api.get_snapshot` and
 * `konsol.orchestrator.api.launch_options` return.
 *
 *     yarn build && yarn mock      # then open http://localhost:8973/konsol-exec/close
 *
 * The fixture deliberately includes a period calendar with OPN and CLS around
 * the twelve, because a mock with only three tidy periods is what hid the
 * "opens on CLS" bug until the console was deployed against real data.
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIST = process.env.DIST || path.resolve(HERE, "../../konsol/public/konsol_exec");
const PORT = Number(process.env.PORT || 8973);
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".html": "text/html" };

const prereq = (doctype, location, owner, status, actionable = false) => ({
  doctype, location, owner, status,
  status_label: { configured: "Configured", missing: "Missing", stale: "Stale", blocked: "Blocked" }[status],
  actionable,
});

const SNAPSHOT = {
  worker_healthy: true,
  stats: { active: 1, errors: 0, done_today: 2 },
  reminders: [],
  processes: {
    budgeting: {
      machine_status: "done", ready_count: 8, total_count: 8, blockers: 0, runnable: 1,
      run: { step_done: 3, step_total: 3 },
      prerequisites: [
        prereq("EPM Settings", "Setup → EPM Settings", "EPM Admin", "configured"),
        prereq("Fiscal Period", "Lists → EPM → Fiscal Period", "EPM Admin", "configured"),
        prereq("Budget Cycle", "Lists → EPM → Budget Cycle", "Budget Manager", "configured"),
      ],
    },
    forecasting: {
      machine_status: "done", ready_count: 7, total_count: 7, blockers: 0, runnable: 1,
      run: { step_done: 3, step_total: 3 },
      prerequisites: [
        prereq("EPM Settings", "Setup → EPM Settings", "EPM Admin", "configured"),
        prereq("Allocation Driver", "Lists → Allocation → Allocation Driver", "EPM Analyst", "configured"),
      ],
    },
    consolidation: {
      machine_status: "running", ready_count: 7, total_count: 9, blockers: 0, runnable: 1,
      run: { step_done: 3, step_total: 5 },
      prerequisites: [
        prereq("EPM Settings", "Setup → EPM Settings", "EPM Admin", "configured"),
        prereq("Consolidation Group", "Lists → Consolidation → Consolidation Group", "EPM Admin", "configured"),
        prereq("Ownership Period", "Lists → Consolidation → Ownership Period", "EPM Admin", "stale", true),
        prereq("IC Elimination Rule", "Lists → Consolidation → IC Elimination Rule", "EPM Admin", "configured"),
      ],
    },
    assertions: {
      machine_status: "idle", ready_count: 5, total_count: 6, blockers: 1, runnable: 0,
      run: {},
      prerequisites: [
        prereq("EPM Settings", "Setup → EPM Settings", "EPM Admin", "configured"),
        prereq("Historical Equity Rate", "Lists → Consolidation → Historical Equity Rate", "EPM Admin", "missing", true),
      ],
    },
  },
  runs: {
    consolidation: [
      { name: "PR-2026-0091", status: "Running", started_at: "2026-09-06 09:14", owner: "controller@grynn.in" },
      { name: "PR-2026-0088", status: "Success", started_at: "2026-09-05 18:02", owner: "controller@grynn.in" },
      { name: "PR-2026-0085", status: "Failed", started_at: "2026-09-05 11:40", owner: "ops@grynn.in" },
    ],
  },
};

const LAUNCH_OPTIONS = {
  definitions: ["consolidation-nightly", "consolidation-adhoc"],
  fiscal_years: ["2024", "2025", "2026"],
  // A real fiscal calendar: an opening period, twelve you close, a closing
  // period. Not three tidy months.
  fiscal_periods: [
    { value: "0", label: "OPN" },
    ...Array.from({ length: 12 }, (_, i) => ({
      value: String(i + 1),
      label: `P${i + 1} · Q${Math.floor(i / 3) + 1}`,
    })),
    { value: "13", label: "CLS" },
  ],
  scopes: [
    { value: "GROUP_CORP", label: "GROUP_CORP (group)" },
    { value: "SGP", label: "SGP — Singapore Pte Ltd" },
  ],
};

http.createServer((req, res) => {
  const p = new URL(req.url, "http://x").pathname;
  if (p.startsWith("/api/method/")) {
    const m = p.replace("/api/method/", "");
    const body = m.includes("get_snapshot") ? SNAPSHOT : m.includes("launch_options") ? LAUNCH_OPTIONS : {};
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ message: body }));
  }
  if (p.startsWith("/assets/konsol/konsol_exec/")) {
    const f = path.join(DIST, p.replace("/assets/konsol/konsol_exec/", ""));
    if (fs.existsSync(f)) {
      res.writeHead(200, { "Content-Type": TYPES[path.extname(f)] || "application/octet-stream" });
      return res.end(fs.readFileSync(f));
    }
  }
  res.writeHead(200, { "Content-Type": "text/html" });
  res.end(fs.readFileSync(path.join(DIST, "index.html")));
}).listen(PORT, () => {
  console.log(`mock konsol control plane on http://localhost:${PORT}/konsol-exec/close`);
  if (!fs.existsSync(path.join(DIST, "index.html"))) {
    console.warn(`  no build found in ${DIST} — run \`yarn build\` first`);
  }
});
