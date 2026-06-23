/* Konsol Control — desk SPA wired to konsol.control_api */
(function () {
  const STATUS = {
    idle: { label: "Idle", color: "var(--ink5)", bg: "var(--card2)" },
    running: { label: "Running", color: "var(--amber)", bg: "var(--amberS)" },
    paused: { label: "Paused", color: "var(--amber)", bg: "var(--amberS)" },
    done: { label: "Completed", color: "var(--green)", bg: "var(--greenS)" },
    error: { label: "Failed", color: "var(--red)", bg: "var(--redS)" },
    cancelled: { label: "Cancelled", color: "var(--ink5)", bg: "var(--card2)" },
  };

  const SETUP = {
    configured: { label: "Configured", color: "var(--green)", bg: "var(--greenS)", glyph: "✓" },
    missing: { label: "Missing", color: "var(--red)", bg: "var(--redS)", glyph: "✗" },
    stale: { label: "Stale", color: "var(--amber)", bg: "var(--amberS)", glyph: "!" },
    blocked: { label: "Blocked", color: "var(--red)", bg: "var(--redS)", glyph: "⊛" },
  };

  class KonsolControl {
    constructor(root) {
      this.root = root;
      this.state = {
        tab: "overview",
        dark: false,
        selected: "forecasting",
        setupSel: "budgeting",
        data: null,
        loading: true,
        toast: null,
      };
      this.poll = null;
    }

    init() {
      this.load();
      frappe.realtime.on("pipeline_progress", () => this.load(true));
      frappe.realtime.on("close_run_update", () => this.load(true));
      frappe.realtime.on("build_request_complete", () => this.load(true));
    }

    load(silent) {
      if (!silent) this.state.loading = true;
      frappe.call({
        method: "konsol.control_api.get_snapshot",
        callback: (r) => {
          this.state.data = r.message;
          this.state.loading = false;
          const active = Object.values(r.message.processes || {}).some(
            (p) => ["running", "paused"].includes(p.machine_status)
          );
          this._togglePoll(active);
          this.render();
        },
      });
    }

    _togglePoll(on) {
      clearInterval(this.poll);
      if (on) this.poll = setInterval(() => this.load(true), 2000);
    }

    toast(msg) {
      this.state.toast = msg;
      this.render();
      clearTimeout(this._toastT);
      this._toastT = setTimeout(() => {
        this.state.toast = null;
        this.render();
      }, 3200);
    }

    start(processId) {
      frappe.call({
        method: "konsol.control_api.start_process",
        args: { process_id: processId },
        callback: (r) => {
          this.toast(`Started ${processId} · ${r.message.name}`);
          this.state.tab = "monitor";
          this.state.selected = processId;
          this.load(true);
        },
      });
    }

    remind(owner, item) {
      frappe.call({
        method: "konsol.control_api.send_reminder",
        args: { owner, item },
        callback: () => this.toast(`Reminder sent to ${owner} · ${new Date().toLocaleTimeString("en-GB")}`),
      });
    }

    openDoctype(doctype) {
      if (doctype) frappe.set_route("List", doctype);
    }

    render() {
      const d = this.state.data;
      if (this.state.loading && !d) {
        this.root.innerHTML = '<div class="kc-loading">Loading control plane…</div>';
        return;
      }
      const s = this.state;
      const procs = d ? Object.values(d.processes) : [];
      const procMap = d ? d.processes : {};
      const sel = procMap[s.selected] || procs[0];
      const setupProc = procMap[s.setupSel] || procs[0];

      const bleed = this.root.closest(".layout-main-section");
      if (bleed) bleed.classList.toggle("konsol-control-dark", s.dark);
      document.body.classList.toggle("konsol-control-dark", s.dark);

      this.root.innerHTML = `
        <div class="kc-app ${s.dark ? "dark" : ""}">
          ${s.toast ? `<div class="kc-toast">${frappe.utils.escape_html(s.toast)}</div>` : ""}
          <div class="kc-header">
            <div class="kc-brand">
              <div class="kc-logo">K</div>
              <div>
                <div class="kc-title">konsol <span style="color:var(--ink4);font-weight:400">control</span></div>
                <div class="kc-subtitle">Financial close orchestration · FY${d.fiscal_year}</div>
              </div>
            </div>
            <div style="display:flex;gap:10px;align-items:center">
              <div class="kc-card" style="padding:6px 12px;font-size:12px;color:var(--ink5);display:flex;gap:7px;align-items:center">
                <span class="kc-dot" style="background:${d.worker_healthy ? "var(--green)" : "var(--red)"}"></span>
                worker · ${d.worker_healthy ? "healthy" : "degraded"}
              </div>
              <button class="kc-btn kc-btn-ghost" data-act="theme">${s.dark ? "☀ Light" : "☾ Dark"}</button>
            </div>
          </div>
          <nav class="kc-nav">
            ${this._tabs(s)}
          </nav>
          <div class="kc-body">
            ${s.tab === "overview" ? this._overview(d, procMap) : ""}
            ${s.tab === "setup" ? this._setup(d, setupProc) : ""}
            ${s.tab === "monitor" ? this._monitor(d, sel) : ""}
            ${s.tab === "history" ? this._history(d) : ""}
          </div>
        </div>`;
      this._bind();
    }

    _tabs(s) {
      const overdue = (s.data?.reminders || []).filter((r) => r.severity === "overdue").length;
      const active = Object.values(s.data?.processes || {}).some((p) => p.machine_status === "running");
      const tabs = [
        ["overview", "Overview", ""],
        ["setup", "Setup & readiness", overdue ? `<span style="font-size:10px;font-weight:600;color:#fff;background:var(--red);min-width:16px;height:16px;padding:0 4px;display:inline-flex;align-items:center;justify-content:center">${overdue}</span>` : ""],
        ["monitor", "Live monitor", active ? '<span class="kc-dot" style="background:var(--amber);animation:kc-spin 1.2s infinite"></span>' : ""],
        ["history", "History", ""],
      ];
      return tabs
        .map(([id, label, badge]) => `<button class="kc-tab ${s.tab === id ? "active" : ""}" data-tab="${id}">${label} ${badge}</button>`)
        .join("");
    }

    _overview(d, procMap) {
      const cards = ["budgeting", "forecasting", "consolidation"]
        .map((id, idx) => {
          const p = procMap[id];
          if (!p) return "";
          const st = STATUS[p.machine_status] || STATUS.idle;
          const run = p.run || {};
          const done = run.step_done || 0;
          const total = run.step_total || 1;
          const pct = Math.round((done / total) * 100);
          const ready = p.runnable;
          let cta = "Start run";
          let ctaColor = "var(--blue)";
          if (p.machine_status === "error") {
            cta = "Retry failed step";
            ctaColor = "var(--red)";
          } else if (["running", "paused"].includes(p.machine_status)) {
            cta = "Open monitor";
          } else if (!ready) {
            cta = "Resolve setup";
            ctaColor = "var(--amber)";
          } else if (p.machine_status === "done") {
            cta = "Run again";
          }
          return `
            <div style="flex:1;display:flex">
              <div class="kc-proc-card">
                <div style="display:flex;justify-content:space-between;margin-bottom:14px">
                  <div style="display:flex;gap:10px;align-items:center">
                    <span style="font-size:11px;font-weight:600;color:#fff;background:${p.accent};width:26px;height:26px;display:flex;align-items:center;justify-content:center">${p.num}</span>
                    <span style="font-size:15px;font-weight:600">${p.name}</span>
                  </div>
                  <span class="kc-pill" style="color:${st.color};background:${st.bg}">${st.label}</span>
                </div>
                <div style="font-size:12.5px;color:var(--ink5);flex:1;margin-bottom:14px;line-height:1.5">${p.desc}</div>
                <div style="font-size:11.5px;color:${ready ? "var(--green)" : "var(--amber)"};margin-bottom:14px;display:flex;gap:7px;align-items:center">
                  <span class="kc-dot" style="background:${ready ? "var(--green)" : "var(--amber)"}"></span>
                  ${ready ? "Setup ready" : `${p.ready_count}/${p.total_count} ready · ${p.blockers} blocker${p.blockers === 1 ? "" : "s"}`}
                </div>
                <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--ink5);margin-bottom:6px">
                  <span>${done} / ${total} steps</span><span>${pct}%</span>
                </div>
                <div class="kc-bar"><div class="kc-bar-fill" style="width:${pct}%;background:${p.machine_status === "error" ? "var(--red)" : p.accent}"></div></div>
                <div style="display:flex;gap:8px">
                  <button class="kc-btn kc-btn-primary" style="background:${ctaColor}" data-act="primary" data-pid="${id}" data-cta="${cta}">${cta}</button>
                  <button class="kc-btn kc-btn-ghost" data-act="open-setup" data-pid="${id}">Open</button>
                </div>
              </div>
              ${idx < 2 ? '<div class="kc-arrow">→</div>' : ""}
            </div>`;
        })
        .join("");

      const reminders = (d.reminders || [])
        .map(
          (r) => `
        <div class="kc-row">
          <span style="width:84px;flex:none;font-size:11.5px;font-weight:600">${r.process}</span>
          <span style="flex:1;font-size:13px">${frappe.utils.escape_html(r.what)}</span>
          <span style="width:120px;flex:none;font-size:12px">${frappe.utils.escape_html(r.owner)}</span>
          <span style="width:104px;flex:none;font-size:11.5px;color:var(--ink5)">${frappe.utils.escape_html(r.due || "")}</span>
          <span class="kc-pill" style="width:66px;text-align:center;font-size:10.5px;color:${r.severity === "overdue" ? "var(--red)" : r.severity === "warn" ? "var(--amber)" : "var(--ink5)"};background:${r.severity === "overdue" ? "var(--redS)" : r.severity === "warn" ? "var(--amberS)" : "var(--card2)"}">${r.severity === "overdue" ? "Overdue" : r.severity === "warn" ? "Action" : "Open"}</span>
          <button class="kc-btn kc-btn-primary" style="flex:none;padding:6px 12px" data-act="remind" data-owner="${frappe.utils.escape_html(r.owner)}" data-item="${frappe.utils.escape_html(r.what)}">Remind</button>
        </div>`
        )
        .join("");

      return `
        <div class="kc-grid3">
          <div class="kc-card"><div class="kc-stat-label">Active runs</div><div class="kc-stat-val">${d.stats.active}<span style="font-size:12px;font-weight:400;color:var(--ink5);margin-left:8px">in progress</span></div></div>
          <div class="kc-card"><div class="kc-stat-label">Needs attention</div><div class="kc-stat-val" style="color:${d.stats.errors ? "var(--red)" : "inherit"}">${d.stats.errors}<span style="font-size:12px;font-weight:400;color:var(--ink5);margin-left:8px">failed</span></div></div>
          <div class="kc-card"><div class="kc-stat-label">Completed today</div><div class="kc-stat-val">${d.stats.done_today}<span style="font-size:12px;font-weight:400;color:var(--ink5);margin-left:8px">runs</span></div></div>
        </div>
        <div class="kc-section-label">Close pipeline</div>
        <div class="kc-pipeline">${cards}</div>
        <div style="margin-top:26px">
          <div class="kc-section-label">Reminders — owners to nudge</div>
          <div class="kc-table">${reminders || '<div class="kc-row" style="color:var(--ink5)">No open reminders</div>'}</div>
        </div>`;
    }

    _setup(d, proc) {
      if (!proc) return "";
      const pills = ["budgeting", "forecasting", "consolidation"]
        .map((id) => {
          const p = d.processes[id];
          const active = id === this.state.setupSel;
          return `<button class="kc-pill-btn" data-setup="${id}" style="background:${active ? "var(--card)" : "transparent"};color:${active ? "var(--ink9)" : "var(--ink5)"};border-color:${active ? "var(--bd2)" : "transparent"}"><span class="kc-dot kc-dot-lg" style="background:${p.blockers ? "var(--red)" : "var(--green)"}"></span>${p.name}</button>`;
        })
        .join("");

      const rows = (proc.prerequisites || [])
        .map((it) => {
          const sm = SETUP[it.status] || SETUP.missing;
          return `
          <div class="kc-row">
            <span style="width:20px;height:20px;flex:none;background:${sm.bg};color:${sm.color};font-weight:700;display:flex;align-items:center;justify-content:center;font-size:12px">${sm.glyph}</span>
            <div style="flex:1">
              <div style="font-size:13.5px;font-weight:500">${it.doctype}</div>
              <div class="kc-mono">${it.location}</div>
            </div>
            <div style="width:120px;flex:none;font-size:12px">${it.owner}</div>
            <span class="kc-pill" style="width:96px;text-align:center;color:${sm.color};background:${sm.bg}">${it.status_label}</span>
            <div style="display:flex;gap:6px">
              <button class="kc-btn kc-btn-ghost" data-doctype="${frappe.utils.escape_html(it.doctype)}">Open in Konsol</button>
              ${it.actionable ? `<button class="kc-btn kc-btn-primary" style="padding:6px 10px" data-act="remind" data-owner="${it.owner}" data-item="${it.doctype}">Remind</button>` : ""}
            </div>
          </div>`;
        })
        .join("");

      let rounds = "";
      if (this.state.setupSel === "budgeting" && d.budget_rounds) {
        rounds = `
          <div style="margin-top:22px">
            <div class="kc-section-label">Budget rounds · layered</div>
            <div class="kc-table">
              ${(d.budget_rounds.rounds || [])
                .map((r) => {
                  const st = { pending: "Not started", draft: "Draft", submitted: "Submitted", approved: "Approved · locked" }[r.state] || r.state;
                  const col = r.state === "approved" ? "var(--green)" : r.state === "submitted" ? "var(--blue)" : "var(--amber)";
                  return `<div class="kc-row">
                    <div style="flex:1"><strong>${r.layer}</strong> <span style="color:var(--ink5);font-size:11.5px">${r.role}</span><div style="font-size:11.5px;color:var(--ink5)">${r.owner} · ${r.week}</div></div>
                    <span style="width:120px;text-align:right;font-variant-numeric:tabular-nums">${r.amount}</span>
                    <span class="kc-pill" style="color:${col}">${st}</span>
                    <button class="kc-btn kc-btn-ghost" data-doctype="Budget Sheet">Open sheets</button>
                  </div>`;
                })
                .join("")}
            </div>
            ${d.budget_rounds.locked ? '<div style="margin-top:8px;font-size:12px;color:var(--green)">■ Budget cycle locked — forecasting unblocked</div>' : ""}
          </div>`;
      }

      return `
        <div class="kc-pills">${pills}</div>
        <div class="kc-card" style="margin-bottom:14px;display:flex;justify-content:space-between;align-items:center">
          <div>
            <div style="font-size:15px;font-weight:600">${proc.name} — prerequisites</div>
            <div style="font-size:12.5px;color:var(--ink5)">Configuration doctypes required before a pipeline run will publish.</div>
          </div>
          <div style="text-align:right">
            <div style="font-size:22px;font-weight:600;color:${proc.blockers ? "var(--red)" : "var(--green)"}">${proc.ready_count} / ${proc.total_count}</div>
            <div style="font-size:11.5px;color:var(--ink5)">configured · ${proc.blockers} blocking</div>
          </div>
        </div>
        <div class="kc-table">${rows}</div>
        ${rounds}`;
    }

    _monitor(d, proc) {
      if (!proc) return "";
      const pills = ["budgeting", "forecasting", "consolidation"]
        .map((id) => {
          const p = d.processes[id];
          const active = id === this.state.selected;
          const st = STATUS[p.machine_status] || STATUS.idle;
          return `<button class="kc-pill-btn" data-select="${id}" style="background:${active ? "var(--card)" : "transparent"};border-color:${active ? "var(--bd2)" : "transparent"}"><span class="kc-dot kc-dot-lg" style="background:${st.color}"></span>${p.name}</button>`;
        })
        .join("");

      const run = proc.run || {};
      const st = STATUS[proc.machine_status] || STATUS.idle;
      const steps = (run.steps || [])
        .map((step) => {
          const dot =
            step.state === "running"
              ? '<div class="kc-spin"></div>'
              : `<span style="width:18px;height:18px;display:flex;align-items:center;justify-content:center;background:${step.state === "done" ? "var(--green)" : step.state === "error" ? "var(--red)" : "transparent"};border:${step.state === "pending" ? "2px solid var(--bd2)" : "none"};color:#fff;font-size:11px">${step.state === "done" ? "✓" : step.state === "error" ? "✗" : ""}</span>`;
          return `<div class="kc-row" style="flex-direction:column;align-items:stretch">
            <div style="display:flex;gap:12px;align-items:center">
              ${dot}
              <div style="flex:1"><span style="font-size:11px;color:var(--ink4);margin-right:8px">${step.num}</span><strong style="font-size:13.5px">${frappe.utils.escape_html(step.name)}</strong><div style="font-size:11.5px;color:var(--ink5)">${frappe.utils.escape_html(step.detail || "")}</div></div>
              <span style="font-size:11.5px;color:var(--ink5)">${step.rows || ""}</span>
              <span style="font-size:11.5px;min-width:40px;text-align:right">${step.pct ? step.pct + "%" : ""}</span>
            </div>
            <div class="kc-bar" style="height:5px;margin:8px 0 0 30px"><div class="kc-bar-fill" style="width:${step.pct || 0}%;background:${step.state === "error" ? "var(--red)" : step.state === "done" ? "var(--green)" : proc.accent}"></div></div>
            ${step.error ? `<div style="margin:8px 0 0 30px;padding:10px;background:var(--redS);color:var(--red);font-size:12px">${frappe.utils.escape_html(step.error)}</div>` : ""}
          </div>`;
        })
        .join("");

      const logs = (run.logs || [])
        .map((l) => {
          const col = l.level === "error" ? "#ff6b6b" : l.level === "ok" ? "#5fd99a" : l.level === "warn" ? "#f0b13b" : "#7db8f0";
          return `<div><span style="color:var(--ink4)">${l.t} </span><span style="color:${col}">${frappe.utils.escape_html(l.text)}</span></div>`;
        })
        .join("");

      let primary = "Start run";
      if (proc.machine_status === "running") primary = "❚❚ Pause (view only)";
      else if (proc.machine_status === "error") primary = "↻ Retry — start new run";
      else if (!proc.runnable) primary = "⚠ Resolve setup";

      return `
        <div class="kc-pills">${pills}</div>
        <div class="kc-card" style="margin-bottom:14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
          <div>
            <div style="display:flex;gap:10px;align-items:center">
              <span style="font-size:15px;font-weight:600">${proc.name}</span>
              <span class="kc-pill" style="color:${st.color};background:${st.bg}">${st.label}</span>
            </div>
            <div style="font-size:12px;color:var(--ink5);margin-top:4px">${run.step_done || 0} / ${run.step_total || 0} steps · ${run.name || "No run yet"}</div>
          </div>
          <button class="kc-btn kc-btn-primary" data-act="start-monitor" data-pid="${proc.id}" ${!proc.runnable && proc.machine_status === "idle" ? 'style="background:var(--amber)"' : ""}>${primary}</button>
        </div>
        <div class="kc-table" style="margin-bottom:14px">${steps || '<div class="kc-row" style="color:var(--ink5)">No active run — start from Overview</div>'}</div>
        <div class="kc-section-label">Console</div>
        <div class="kc-console">${logs || "<div style='color:var(--ink4)'>Waiting for run output…</div>"}</div>`;
    }

    _history(d) {
      const rows = (d.history || [])
        .map((h) => {
          const st = STATUS[h.status] || STATUS.idle;
          return `<div class="kc-row" style="display:grid;grid-template-columns:1.4fr 1fr 1.1fr .8fr .9fr 1fr .6fr;gap:8px">
            <span><span class="kc-dot" style="background:${h.accent};margin-right:8px"></span>${h.process}</span>
            <span>${frappe.utils.escape_html(h.period)}</span>
            <span style="font-size:12px">${h.started}</span>
            <span>${h.duration}</span>
            <span>${h.rows}</span>
            <span style="font-size:12px">${h.by}</span>
            <span class="kc-pill" style="text-align:center;color:${st.color};background:${st.bg}">${st.label}</span>
          </div>`;
        })
        .join("");
      return `<div class="kc-section-label">Run history</div><div class="kc-table">${rows || '<div class="kc-row">No runs yet</div>'}</div>`;
    }

    _bind() {
      this.root.querySelectorAll("[data-tab]").forEach((el) => {
        el.onclick = () => {
          this.state.tab = el.dataset.tab;
          this.render();
        };
      });
      this.root.querySelectorAll("[data-setup]").forEach((el) => {
        el.onclick = () => {
          this.state.setupSel = el.dataset.setup;
          this.render();
        };
      });
      this.root.querySelectorAll("[data-select]").forEach((el) => {
        el.onclick = () => {
          this.state.selected = el.dataset.select;
          this.render();
        };
      });
      const theme = this.root.querySelector('[data-act="theme"]');
      if (theme) theme.onclick = () => { this.state.dark = !this.state.dark; this.render(); };

      this.root.querySelectorAll('[data-act="primary"]').forEach((el) => {
        el.onclick = () => {
          const pid = el.dataset.pid;
          const cta = el.dataset.cta;
          if (cta === "Resolve setup") {
            this.state.tab = "setup";
            this.state.setupSel = pid;
            this.render();
          } else if (cta === "Open monitor") {
            this.state.tab = "monitor";
            this.state.selected = pid;
            this.render();
          } else if (["Start run", "Run again", "Retry failed step"].includes(cta)) {
            this.start(pid);
          }
        };
      });
      this.root.querySelectorAll('[data-act="open-setup"]').forEach((el) => {
        el.onclick = () => {
          this.state.tab = "setup";
          this.state.setupSel = el.dataset.pid;
          this.render();
        };
      });
      this.root.querySelectorAll('[data-act="start-monitor"]').forEach((el) => {
        el.onclick = () => {
          const pid = el.dataset.pid;
          const proc = this.state.data.processes[pid];
          if (!proc.runnable && proc.machine_status === "idle") {
            this.state.tab = "setup";
            this.state.setupSel = pid;
            this.render();
          } else if (proc.machine_status !== "running") {
            this.start(pid);
          }
        };
      });
      this.root.querySelectorAll('[data-act="remind"]').forEach((el) => {
        el.onclick = () => this.remind(el.dataset.owner, el.dataset.item);
      });
      this.root.querySelectorAll("[data-doctype]").forEach((el) => {
        el.onclick = () => this.openDoctype(el.dataset.doctype);
      });
    }
  }

  window.konsol_control_init = function (selector) {
    const root = document.querySelector(selector);
    if (!root) return;
    const app = new KonsolControl(root);
    app.init();
  };
})();