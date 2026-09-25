/* React 18 UMD — Windows 98 consolidation close wizard. No JSX. */
(function () {
  const h = React.createElement;
  const { useState, useEffect, useCallback } = React;

  function errMsg(j, fallback) {
    try {
      if (j && j._server_messages) {
        const arr = JSON.parse(j._server_messages);
        return arr
          .map(function (x) {
            try {
              const o = JSON.parse(x);
              return o.message || x;
            } catch (e) {
              return x;
            }
          })
          .join("\n");
      }
    } catch (e) {}
    if (j && j.exception) return String(j.exception).split("\n")[0];
    return fallback || "Request failed";
  }

  async function api(method, args, http) {
    http = http || "POST";
    const opts = {
      method: http,
      credentials: "include",
      headers: { "X-Frappe-CSRF-Token": window.csrf_token || "" },
    };
    if (http === "GET") {
      const q = new URLSearchParams();
      Object.keys(args || {}).forEach(function (k) {
        const v = args[k];
        q.set(k, typeof v === "string" ? v : JSON.stringify(v));
      });
      const r = await fetch("/api/method/" + method + "?" + q.toString(), opts);
      const j = await r.json();
      if (!r.ok || j.exc) throw new Error(errMsg(j, r.statusText));
      return j.message;
    }
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(args || {});
    const r = await fetch("/api/method/" + method, opts);
    const j = await r.json();
    if (!r.ok || j.exc) throw new Error(errMsg(j, r.statusText));
    return j.message;
  }

  async function uploadFile(file) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("is_private", "1");
    fd.append("folder", "Home");
    const r = await fetch("/api/method/upload_file", {
      method: "POST",
      credentials: "include",
      headers: { "X-Frappe-CSRF-Token": window.csrf_token || "" },
      body: fd,
    });
    const j = await r.json();
    if (!r.ok || j.exc) throw new Error(errMsg(j, "Upload failed"));
    return j.message;
  }

  const STEPS = [
    { id: "tb", title: "1. Load trial balances", hint: "Upload the company-year file. Check, then load ready periods." },
    { id: "build", title: "2. Build consolidation", hint: "Approve the high-risk consolidation job so dbt writes gold numbers." },
    { id: "assert", title: "3. Run assertions", hint: "dbt tests for this year and period. Wait until it is not Running." },
    { id: "sign", title: "4. Sign off", hint: "Green signs off. Amber needs an acknowledgement. Red needs an override reason." },
    { id: "close", title: "5. Close the period", hint: "Locks the period. New trial balances for it will be refused." },
  ];

  function Icon({ kind }) {
    const map = {
      computer: "🖥",
      folder: "📁",
      hammer: "🔨",
      clip: "📋",
      pen: "✎",
      lock: "🔒",
      check: "✓",
    };
    return h("span", { className: "w98-icon", "aria-hidden": true }, map[kind] || "•");
  }

  function App() {
    const now = new Date();
    const [year, setYear] = useState(2022);
    const [period, setPeriod] = useState(12);
    const [years, setYears] = useState([2022, 2023, 2024, 2025, 2026]);
    const [step, setStep] = useState(0);
    const [busy, setBusy] = useState("");
    const [log, setLog] = useState("");
    const [clock, setClock] = useState(now.toLocaleTimeString());
    const [tbCount, setTbCount] = useState(0);
    const [entities, setEntities] = useState(0);
    const [uploads, setUploads] = useState([]);
    const [checkReport, setCheckReport] = useState(null);
    const [builds, setBuilds] = useState([]);
    const [run, setRun] = useState(null);
    const [periodStatus, setPeriodStatus] = useState("Open");
    const [ack, setAck] = useState("");
    const [override, setOverride] = useState("");

    const note = useCallback(function (msg) {
      setLog(function (prev) {
        const t = new Date().toLocaleTimeString();
        return (prev ? prev + "\n" : "") + t + "  " + msg;
      });
    }, []);

    useEffect(function () {
      const id = setInterval(function () {
        setClock(new Date().toLocaleTimeString());
      }, 1000);
      return function () { clearInterval(id); };
    }, []);

    const refresh = useCallback(async function () {
      try {
        const fy = await api("frappe.client.get_list", {
          doctype: "EPM Fiscal Year",
          fields: ["name", "fiscal_year", "status"],
          order_by: "fiscal_year asc",
          limit_page_length: 20,
        });
        if (fy && fy.length) {
          setYears(fy.map(function (r) { return r.fiscal_year; }));
        }
      } catch (e) {}
      try {
        const subs = await api("frappe.client.get_list", {
          doctype: "Trial Balance Submission",
          fields: ["name", "data_area_id"],
          filters: { fiscal_year: year, fiscal_period: period, docstatus: 1 },
          limit_page_length: 500,
        });
        setTbCount((subs || []).length);
        const setE = {};
        (subs || []).forEach(function (s) { setE[s.data_area_id] = 1; });
        setEntities(Object.keys(setE).length);
      } catch (e) {
        setTbCount(0);
        setEntities(0);
      }
      try {
        const ba = await api("frappe.client.get_list", {
          doctype: "Build Approval",
          fields: ["name", "workflow_state", "build_scope", "risk_level", "modified"],
          filters: { build_scope: "consolidation" },
          order_by: "modified desc",
          limit_page_length: 8,
        });
        setBuilds(ba || []);
      } catch (e) {
        setBuilds([]);
      }
      try {
        const latest = await api(
          "konsol.consolidation.doctype.assertion_run.assertion_run.latest_close_run",
          { fiscal_year: year, fiscal_period: period }
        );
        if (latest && latest.name) {
          const doc = await api("frappe.client.get", { doctype: "Assertion Run", name: latest.name });
          setRun(doc);
        } else {
          setRun(null);
        }
      } catch (e) {
        setRun(null);
      }
      try {
        const ydoc = await api("frappe.client.get", { doctype: "EPM Fiscal Year", name: String(year) });
        const row = (ydoc.periods || []).find(function (p) { return Number(p.fiscal_period) === Number(period); });
        setPeriodStatus(row ? row.status : "?");
      } catch (e) {
        setPeriodStatus("?");
      }
      try {
        const rec = await api("konsol.tb_bulk.recent_uploads", { limit: 6 });
        setUploads(rec || []);
      } catch (e) {}
    }, [year, period]);

    useEffect(function () {
      refresh();
      const id = setInterval(refresh, 8000);
      return function () { clearInterval(id); };
    }, [refresh]);

    const checkpoints = [
      { id: "tb", label: "Trial balances loaded", detail: tbCount ? tbCount + " submissions · " + entities + " entities" : "None for this period", state: tbCount > 0 ? "done" : "idle" },
      { id: "build", label: "Consolidation built", detail: (builds[0] && builds[0].name + " · " + builds[0].workflow_state) || "No consolidation job yet", state: builds[0] && builds[0].workflow_state === "Completed" ? "done" : builds[0] && builds[0].workflow_state === "Failed" ? "fail" : builds[0] && (builds[0].workflow_state === "Running" || builds[0].workflow_state === "Approved") ? "run" : "idle" },
      { id: "assert", label: "Assertions finished", detail: run ? run.name + " · " + run.status + " (" + (run.passed || 0) + " pass / " + (run.failed || 0) + " fail / " + (run.errored || 0) + " err)" : "Not run", state: !run ? "idle" : run.status === "Running" || run.status === "Queued" ? "run" : run.status === "Green" ? "done" : run.status === "Amber" ? "done" : "fail" },
      { id: "sign", label: "Signed off", detail: (run && run.signoff_status) || "Not signed off", state: run && ["Signed Off", "Acknowledged", "Overridden"].indexOf(run.signoff_status) >= 0 ? "done" : "idle" },
      { id: "close", label: "Period closed", detail: "P" + String(period).padStart(2, "0") + " is " + periodStatus, state: periodStatus === "Closed" || periodStatus === "Locked" ? "done" : "idle" },
    ];

    async function wrap(label, fn) {
      setBusy(label);
      note(label + "…");
      try {
        await fn();
        note(label + " — done.");
        await refresh();
      } catch (e) {
        note(label + " — " + (e.message || e));
      }
      setBusy("");
    }

    async function onPickFiles(files) {
      if (!files || !files.length) return;
      await wrap("Upload + check " + files[0].name, async function () {
        const saved = await uploadFile(files[0]);
        const report = await api("konsol.tb_bulk.check_file", { file_url: saved.file_url });
        setCheckReport(report);
        note("Checked " + report.name + ": " + (report.valid_count || 0) + " ready of " + (report.group_count || 0));
      });
    }

    async function onLoad() {
      if (!checkReport || !checkReport.name) return;
      await wrap("Load " + checkReport.name, async function () {
        await api("konsol.tb_bulk.load", { name: checkReport.name, skip_invalid: 1 });
        for (let i = 0; i < 60; i++) {
          const u = await api("konsol.tb_bulk.get_upload", { name: checkReport.name });
          setCheckReport(u);
          if (u.status && u.status !== "Loading" && u.status !== "Checked") {
            note("Load status " + u.status + " loaded=" + (u.loaded_count || 0));
            break;
          }
          await new Promise(function (r) { setTimeout(r, 2000); });
        }
      });
    }

    async function onApprove(name) {
      await wrap("Approve " + name, async function () {
        const doc = await api("frappe.client.get", { doctype: "Build Approval", name: name });
        await api("frappe.model.workflow.apply_workflow", { doc: doc, action: "Approve" });
        for (let i = 0; i < 90; i++) {
          const list = await api("frappe.client.get_list", {
            doctype: "Build Approval",
            fields: ["name", "workflow_state", "build_scope", "risk_level", "modified"],
            filters: { name: name },
            limit_page_length: 1,
          });
          const st = list && list[0] && list[0].workflow_state;
          note(name + " → " + st);
          if (st === "Completed" || st === "Failed" || st === "Cancelled") break;
          await new Promise(function (r) { setTimeout(r, 4000); });
        }
      });
    }

    async function onNewBuild() {
      await wrap("Request consolidation build", async function () {
        const doc = await api("frappe.client.insert", {
          doc: {
            doctype: "Build Approval",
            build_scope: "consolidation",
            trigger_source: "manual",
            full_refresh: 0,
          },
        });
        note("Created " + doc.name);
      });
    }

    async function onAssert() {
      await wrap("Run assertions FY" + year + " P" + period, async function () {
        const name = await api(
          "konsol.consolidation.doctype.assertion_run.assertion_run.trigger_close_run",
          { fiscal_year: year, fiscal_period: period }
        );
        note("Assertion run " + name);
        for (let i = 0; i < 120; i++) {
          const doc = await api("frappe.client.get", { doctype: "Assertion Run", name: name });
          setRun(doc);
          if (doc.status !== "Queued" && doc.status !== "Running") break;
          await new Promise(function (r) { setTimeout(r, 4000); });
        }
      });
    }

    async function onSign() {
      if (!run) return;
      await wrap("Sign off " + run.name, async function () {
        const args = { close_run: run.name };
        if (run.status === "Amber") args.acknowledgement = ack || "Warnings acknowledged.";
        if (run.status === "Red" || run.status === "Error") args.override_reason = override || "Override for this close.";
        const out = await api(
          "konsol.consolidation.doctype.assertion_run.assertion_run.sign_off_close",
          args
        );
        note("Sign-off " + (out && out.signoff_status));
      });
    }

    async function onClose() {
      await wrap("Close FY" + year + " P" + period, async function () {
        await api("run_doc_method", {
          dt: "EPM Fiscal Year",
          dn: String(year),
          method: "close_period",
          args: { fiscal_period: period, note: "Closed from Consolidation Wizard" },
        });
      });
    }

    const stepBody = (function () {
      if (step === 0) {
        const rows = ((checkReport && checkReport.report) || []).slice(0, 40);
        return h("div", null,
          h("p", { className: "w98-help" }, "Drop a CSV or XLSX from trial-balances/<COMPANY>/FY20xx.xlsx. Amount basis is already on each row. Only ready entity-periods load."),
          h("div", {
            className: "w98-drop",
            onDragOver: function (e) { e.preventDefault(); },
            onDrop: function (e) { e.preventDefault(); onPickFiles(e.dataTransfer.files); },
          },
            h("div", null, h(Icon, { kind: "folder" }), " Drop file here, or "),
            h("input", {
              type: "file",
              accept: ".csv,.xlsx,.xls",
              onChange: function (e) { onPickFiles(e.target.files); e.target.value = ""; },
            })
          ),
          checkReport ? h("div", null,
            h("p", null, "Upload ", h("b", null, checkReport.name), " — ", checkReport.status || "Checked",
              " · ready ", String(checkReport.valid_count || 0), " / ", String(checkReport.group_count || 0)),
            h("div", { className: "w98-table-wrap" },
              h("table", { className: "interactive" },
                h("thead", null, h("tr", null, h("th", null, "Entity"), h("th", null, "Year"), h("th", null, "Period"), h("th", null, "OK"), h("th", null, "Notes"))),
                h("tbody", null, rows.map(function (r, i) {
                  return h("tr", { key: i },
                    h("td", null, r.entity || r.data_area_id),
                    h("td", null, r.fiscal_year),
                    h("td", null, r.fiscal_period),
                    h("td", null, r.ok ? "Yes" : "No"),
                    h("td", null, (r.errors || []).join("; ").slice(0, 120))
                  );
                }))
              )
            ),
            h("div", { className: "w98-actions" },
              h("button", { disabled: !!busy, onClick: onLoad }, "Load ready periods")
            )
          ) : null,
          uploads && uploads.length ? h("p", { className: "w98-muted" }, "Recent: ", uploads.map(function (u) { return u.name + " " + u.status; }).join(" · ")) : null
        );
      }
      if (step === 1) {
        return h("div", null,
          h("p", { className: "w98-help" }, "A Pending Review job blocks later rebuilds. Approve consolidation after TBs are in. Chart jobs are optional here."),
          h("div", { className: "w98-actions" },
            h("button", { disabled: !!busy, onClick: onNewBuild }, "New consolidation request"),
            h("button", { disabled: !!busy, onClick: refresh }, "Refresh")
          ),
          h("div", { className: "w98-table-wrap" },
            h("table", { className: "interactive" },
              h("thead", null, h("tr", null, h("th", null, "Job"), h("th", null, "State"), h("th", null, "Risk"), h("th", null, ""))),
              h("tbody", null, (builds.length ? builds : [{ name: "(none)", workflow_state: "—" }]).map(function (b) {
                return h("tr", { key: b.name },
                  h("td", null, b.name),
                  h("td", null, b.workflow_state),
                  h("td", null, b.risk_level),
                  h("td", null, b.workflow_state === "Pending Review"
                    ? h("button", { disabled: !!busy, onClick: function () { onApprove(b.name); } }, "Approve")
                    : null)
                );
              }))
            )
          )
        );
      }
      if (step === 2) {
        return h("div", null,
          h("p", { className: "w98-help" }, "Runs dbt tests for FY", String(year), " P", String(period), ". Only one suite can run at a time. Wait until status is Green, Amber, or Red."),
          h("div", { className: "w98-actions" },
            h("button", { disabled: !!busy, onClick: onAssert }, "Run assertion suite")
          ),
          run ? h("div", { className: "w98-kpi" },
            h("p", { className: "status-bar-field" }, "Run ", run.name),
            h("p", { className: "status-bar-field" }, "Status ", run.status),
            h("p", { className: "status-bar-field" }, "Pass ", String(run.passed || 0)),
            h("p", { className: "status-bar-field" }, "Fail ", String(run.failed || 0)),
            h("p", { className: "status-bar-field" }, "Error ", String(run.errored || 0)),
            h("p", { className: "status-bar-field" }, "Warn ", String(run.warned || 0))
          ) : h("p", { className: "w98-muted" }, "No run for this period yet.")
        );
      }
      if (step === 3) {
        return h("div", null,
          h("p", { className: "w98-help" }, "Green → Sign off. Amber → type why you accept the warnings. Red → override reason (EPM Admin)."),
          run ? h("div", null,
            h("p", null, run.name, " is ", h("b", null, run.status), " · sign-off ", run.signoff_status || "Not Signed Off"),
            run.status === "Amber" ? h("div", { className: "field-row" },
              h("label", null, "Acknowledgement ",
                h("textarea", { value: ack, rows: 3, onChange: function (e) { setAck(e.target.value); }, style: { width: "100%" } })
              )
            ) : null,
            (run.status === "Red" || run.status === "Error") ? h("div", { className: "field-row" },
              h("label", null, "Override reason ",
                h("textarea", { value: override, rows: 3, onChange: function (e) { setOverride(e.target.value); }, style: { width: "100%" } })
              )
            ) : null,
            h("div", { className: "w98-actions" },
              h("button", { disabled: !!busy || !run || run.status === "Running" || run.status === "Queued", onClick: onSign }, "Sign off")
            )
          ) : h("p", { className: "w98-muted" }, "Run assertions first.")
        );
      }
      return h("div", null,
        h("p", { className: "w98-help" }, "Closes P", String(period).padStart(2, "0"), " of FY", String(year), ". Needs complete Closing + Average rates. After this, that period will not accept new TBs."),
        h("p", null, "Current status: ", h("b", null, periodStatus)),
        h("div", { className: "w98-actions" },
          h("button", { disabled: !!busy || periodStatus === "Closed" || periodStatus === "Locked", onClick: onClose }, "Close period")
        ),
        periodStatus === "Closed" ? h("p", { className: "w98-ok" }, "This period is closed. Read numbers in Cube (port 4000) or Excel =K.EPM.") : null
      );
    })();

    return h("div", { className: "w98-desktop" },
      h("div", { className: "w98-stage" },
        h("div", { className: "window w98-app" },
          h("div", { className: "title-bar" },
            h("div", { className: "title-bar-text" }, "Consolidation Close Wizard — Load TBs to Close"),
            h("div", { className: "title-bar-controls" },
              h("button", { "aria-label": "Minimize" }),
              h("button", { "aria-label": "Maximize" }),
              h("button", { "aria-label": "Close" })
            )
          ),
          h("div", { className: "window-body" },
            h("div", { className: "w98-period" },
              h("label", null, "Fiscal year",
                h("select", { value: year, onChange: function (e) { setYear(Number(e.target.value)); } },
                  years.map(function (y) { return h("option", { key: y, value: y }, y); })
                )
              ),
              h("label", null, "Period",
                h("select", { value: period, onChange: function (e) { setPeriod(Number(e.target.value)); } },
                  [1,2,3,4,5,6,7,8,9,10,11,12].map(function (p) {
                    return h("option", { key: p, value: p }, "P" + String(p).padStart(2, "0"));
                  })
                )
              ),
              h("button", { disabled: !!busy, onClick: refresh }, "Refresh status")
            ),
            h("div", { className: "w98-layout" },
              h("div", { className: "sunken-panel w98-side" },
                h("p", null, h("b", null, "Checkpoints")),
                h("p", { className: "w98-muted" }, "FY", String(year), " · P", String(period).padStart(2, "0")),
                h("ul", { className: "w98-checks" },
                  checkpoints.map(function (c, i) {
                    return h("li", {
                      key: c.id,
                      className: step === i ? "current" : "",
                      onClick: function () { setStep(i); },
                      style: { cursor: "pointer" },
                    },
                      h("span", { className: "w98-dot " + c.state }, c.state === "done" ? "✓" : i + 1),
                      h("span", null,
                        h("div", null, c.label),
                        h("div", { className: "w98-muted" }, c.detail)
                      )
                    );
                  })
                )
              ),
              h("div", { className: "w98-main" },
                h("menu", { role: "tablist", style: { display: "flex", gap: 0, padding: 0, margin: "0 0 8px" } },
                  STEPS.map(function (s, i) {
                    return h("button", {
                      key: s.id,
                      "aria-selected": step === i,
                      onClick: function () { setStep(i); },
                      disabled: !!busy && step !== i,
                    }, s.title.replace(/^\d+\.\s/, ""));
                  })
                ),
                h("fieldset", null,
                  h("legend", null, STEPS[step].title),
                  h("p", { className: "w98-help" }, STEPS[step].hint),
                  stepBody
                ),
                h("div", { className: "w98-actions" },
                  h("button", { disabled: step === 0 || !!busy, onClick: function () { setStep(step - 1); } }, "Back"),
                  h("button", { disabled: step === STEPS.length - 1 || !!busy, onClick: function () { setStep(step + 1); } }, "Next")
                ),
                h("div", { className: "w98-log", "aria-live": "polite" }, log || "Ready. Pick a year and period, then start at step 1.")
              )
            ),
            h("div", { className: "status-bar" },
              h("p", { className: "status-bar-field" }, busy ? "Working: " + busy : "Idle"),
              h("p", { className: "status-bar-field" }, entities + " entities loaded"),
              h("p", { className: "status-bar-field" }, periodStatus)
            )
          )
        )
      ),
      h("div", { className: "w98-taskbar" },
        h("button", { className: "w98-start" }, h(Icon, { kind: "computer" }), " Start"),
        h("button", { className: "w98-task active" }, "Consolidation Close Wizard"),
        h("div", { className: "w98-clock" }, clock)
      )
    );
  }

  const root = ReactDOM.createRoot(document.getElementById("root"));
  root.render(h(App));
})();
