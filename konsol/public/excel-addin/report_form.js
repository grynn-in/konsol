/* global Excel */

// ============================================================
// Konsolidat — the Reports panel: template list and generated form
// (konsol#548 v1 T14)
// ============================================================
//
// The template list is the server's catalog (konsol.api.report_catalog), in
// its order; a template the site cannot read is listed disabled with its
// why. There is no built-in fallback list: if the catalog cannot be read, the
// panel shows the error.
//
// Each declared param gets one widget, by type:
//   entity, group, fiscal_year, period, scenario, hierarchy, hierarchy_node,
//   dimension_value, account, account_heading -> a select filled from
//     konsol.api.report_choices (a searchable input + datalist when long)
//   scope        -> a kind toggle + a select. The toggle offers the param's
//                   kinds that the catalog lists in the template's
//                   available_kinds (when it lists them); each other kind's
//                   why (unavailable_kinds) is shown under it. The kinds are
//                   report_template_model.SCOPE_KINDS: entity, group, node.
//   account_list -> a multi-select
//   choice       -> a select of the param's choices
//   int          -> a number input;  text -> a text input
//   text with echo_only -> not shown; its default is sent
// A picker whose param depends_on another is refetched when that one changes
// (year -> periods, scenarios; hierarchy -> nodes). A scenario whose default
// is "rule" is preselected only when the server names a default; otherwise
// it stays blank and the server's why is shown under it. A required field
// left blank blocks Apply, by name, and so does a picker whose choices are
// still loading ("Loading choices…"), so Apply never sends a new year with
// the old year's budget. A reply for a request that a newer one replaced is
// dropped.
//
// The Apply button reads "Update this report" when konsolReport.inspect()
// says the active sheet is the add-in's own report of the selected template,
// and the form is prefilled from that report's params; otherwise it reads
// "Apply to new sheet" (decided by Deepak Pai, 27 Nov 2026, konsol#548 Q3).
// The check runs on template change and on worksheet activation.
//
// The page supplies the transport (fetchCatalog, fetchChoices) so every call
// goes through its own api() helper: the token, the chosen server, CORS.
// ============================================================

var konsolReportForm = (function () {
  //: param type -> the report_choices kind that fills its picker
  var KIND_OF_TYPE = {
    entity: "entities",
    group: "groups",
    fiscal_year: "fiscal_years",
    period: "periods",
    scenario: "scenarios",
    hierarchy: "hierarchies",
    hierarchy_node: "hierarchy_nodes",
    dimension_value: "dimension_values",
    account: "accounts",
    account_heading: "account_headings",
    account_list: "accounts",
  };
  //: scope kind -> its report_choices kind
  var KIND_OF_SCOPE = { entity: "entities", group: "groups", node: "hierarchy_nodes" };
  var SCOPE_LABEL = { entity: "Entity", group: "Group", node: "Hierarchy node" };
  //: the request args each kind takes from the params it depends on
  var KIND_ARGS = {
    periods: ["year"],
    scenarios: ["year"],
    hierarchy_nodes: ["hierarchy", "year"],
    dimension_values: ["dimension"],
  };
  //: a parent param's type -> the request arg its value fills
  var ARG_OF_PARENT = { fiscal_year: "year", hierarchy: "hierarchy" };
  //: longer lists than this are a searchable input rather than a select
  var LONG_LIST = 25;
  var UPDATE = "Update this report";
  var APPLY_NEW = "Apply to new sheet";
  var LOADING = "Loading choices…";

  function blank(v) {
    return v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length);
  }

  function message(err) {
    return (err && err.message) || String(err);
  }

  function widgetFor(p) {
    if (p.type === "text") return p.echo_only ? "hidden" : "text";
    if (p.type === "int") return "number";
    if (p.type === "choice") return "choice";
    if (p.type === "scope") return "scope";
    if (p.type === "account_list") return "multiselect";
    if (KIND_OF_TYPE[p.type]) return "select";
    return "text";
  }

  function indent(depth) {
    var s = "";
    for (var i = 0; i < (depth || 0); i += 1) s += "  ";
    return s;
  }

  function optionOf(kind, c) {
    if (c === null || typeof c !== "object") return { id: String(c), label: String(c) };
    if (kind === "periods") {
      var n = String(c.number);
      return { id: n, label: c.label && c.label !== n ? n + " — " + c.label : n };
    }
    if (kind === "hierarchies") return { id: String(c.name), label: c.label || String(c.name) };
    if (kind === "hierarchy_nodes") return { id: String(c.code), label: indent(c.depth) + (c.label || c.code) };
    var id = String(c.code !== undefined ? c.code : c.id);
    var name = c.name || c.label;
    return { id: id, label: name && name !== id ? id + " — " + name : id };
  }

  // report_choices' payload as {options: [{id, label}], default, why}.
  function optionsOf(kind, choices) {
    if (choices && !Array.isArray(choices) && Array.isArray(choices.options)) {
      return {
        options: choices.options.map(function (o) {
          return { id: String(o.id), label: o.label || String(o.id) };
        }),
        default: blank(choices.default) ? null : String(choices.default),
        why: choices.why || null,
      };
    }
    var list = Array.isArray(choices) ? choices : [];
    return { options: list.map(function (c) { return optionOf(kind, c); }), default: null, why: null };
  }

  // The choices request for one picker: {kind, args}, or {missing: label}
  // when a param it depends on is still blank.
  function queryFor(p, kind, raw, byName) {
    var wanted = KIND_ARGS[kind] || [];
    var args = {};
    var missing = null;
    (p.depends_on || []).forEach(function (d) {
      var parent = byName[d];
      if (!parent) return;
      var arg = ARG_OF_PARENT[parent.type] || (kind === "dimension_values" ? "dimension" : null);
      if (!arg || wanted.indexOf(arg) < 0) return;
      if (blank(raw[d])) {
        missing = missing || parent.label || d;
        return;
      }
      args[arg] = String(raw[d]);
    });
    if (kind === "scenarios" && p.scenario_type) args.scenario_type = p.scenario_type;
    return missing ? { kind: kind, missing: missing } : { kind: kind, args: args };
  }

  function toNumber(v) {
    return /^-?\d+$/.test(String(v)) ? parseInt(v, 10) : v;
  }

  // The request's params from the widgets' raw values; blanks are left out,
  // so the server applies its own default (or refuses a required one).
  function requestParams(params, raw) {
    var out = {};
    params.forEach(function (p) {
      var v = raw[p.name];
      if (p.type === "scope") {
        if (!v || blank(v.code)) return;
        out[p.name] = { kind: v.kind, code: v.code };
        return;
      }
      if (blank(v)) return;
      if (p.type === "fiscal_year" || p.type === "int" || p.type === "period") v = toNumber(v);
      out[p.name] = v;
    });
    return out;
  }

  function missingRequired(params, raw) {
    return params.filter(function (p) {
      if (!p.required) return false;
      var v = raw[p.name];
      return p.type === "scope" ? !v || blank(v.code) : blank(v);
    }).map(function (p) { return p.label || p.name; });
  }

  function buttonLabel(inspection, templateId) {
    return inspection && inspection.own && inspection.entry &&
      inspection.entry.template_id === templateId ? UPDATE : APPLY_NEW;
  }

  // window.konsolidatApplyReport's two shapes: {template_id, params} as is,
  // and the legacy {template_id, entity, year, ...} with the rest as params.
  function normaliseRequest(arg) {
    var a = arg || {};
    if (a.params && typeof a.params === "object") return { template_id: a.template_id, params: a.params };
    var params = {};
    Object.keys(a).forEach(function (k) {
      if (k !== "template_id" && a[k] !== undefined) params[k] = a[k];
    });
    return { template_id: a.template_id, params: params };
  }

  // -- the panel -----------------------------------------------------------

  function mount(o) {
    var doc = o.doc || document;
    var state = { templates: [], tpl: null, fields: {}, gen: 0,
                  inspection: null, prefilled: null, busy: false };

    function log(level, text) {
      if (o.log) o.log(level, text);
    }

    function el(tag, text) {
      var e = doc.createElement(tag);
      if (text !== undefined) e.textContent = text;
      return e;
    }

    function option(value, text) {
      var e = el("option", text);
      e.value = value;
      return e;
    }

    function show(e, text) {
      if (!e) return;
      e.textContent = text || "";
      e.hidden = !text;
    }

    function byName() {
      var out = {};
      (state.tpl ? state.tpl.params : []).forEach(function (p) { out[p.name] = p; });
      return out;
    }

    // -- widgets ---------------------------------------------------------

    function fill(f, options) {
      f.options = options;
      f.slot.textContent = "";
      var multiple = f.widget === "multiselect";
      if (!multiple && options.length > LONG_LIST) {
        var listId = "konsol-list-" + state.gen + "-" + f.p.name;
        f.input = el("input");
        f.input.type = "text";
        f.input.placeholder = "Type to search " + options.length + " choices";
        f.input.setAttribute("list", listId);
        var dl = el("datalist");
        dl.setAttribute("id", listId);
        options.forEach(function (c) { dl.appendChild(option(c.id, c.label)); });
        f.slot.appendChild(f.input);
        f.slot.appendChild(dl);
      } else {
        f.input = el("select");
        if (multiple) f.input.multiple = true;
        else f.input.appendChild(option("", ""));
        options.forEach(function (c) { f.input.appendChild(option(c.id, c.label)); });
        f.slot.appendChild(f.input);
      }
      f.input.setAttribute("data-param", f.p.name);
      f.input.addEventListener("change", function () { changed(f.p.name); });
      f.input.addEventListener("input", syncButtons);
    }

    function raw(f) {
      if (f.widget === "hidden") return f.p.default;
      if (f.widget === "multiselect") {
        return Array.prototype.filter.call(f.input.options, function (x) { return x.selected; })
          .map(function (x) { return x.value; });
      }
      var v = String(f.input.value || "").trim();
      if (f.widget === "scope") return { kind: scopeKind(f), code: v };
      return v;
    }

    function rawValues() {
      var out = {};
      Object.keys(state.fields).forEach(function (n) { out[n] = raw(state.fields[n]); });
      return out;
    }

    function has(f, v) {
      return (f.options || []).some(function (c) { return c.id === String(v); });
    }

    function setValue(f, v) {
      if (f.widget === "multiselect") {
        var want = (Array.isArray(v) ? v : [v]).map(String);
        Array.prototype.forEach.call(f.input.options, function (x) { x.selected = want.indexOf(x.value) >= 0; });
      } else {
        f.input.value = blank(v) ? "" : String(v);
      }
    }

    // The scope kinds the form offers: the param's, less those the catalog
    // says this site cannot read (an older catalog lists none: all offered).
    function offeredKinds(p) {
      var t = state.tpl;
      var kinds = (p.kinds || []).length ? p.kinds : ["entity"];
      if (!t || !Array.isArray(t.available_kinds)) return kinds;
      return kinds.filter(function (k) { return t.available_kinds.indexOf(k) >= 0; });
    }

    function kindsWhy(p) {
      var t = state.tpl;
      var why = (t && t.unavailable_kinds) || {};
      return (p.kinds || []).filter(function (k) { return why[k]; }).map(function (k) {
        return (SCOPE_LABEL[k] || k) + ": " + why[k];
      }).join("\n");
    }

    function renderField(p) {
      var f = { p: p, widget: widgetFor(p), seq: 0, options: [], pending: undefined, loading: false };
      if (f.widget === "hidden") return f;
      if (f.widget === "scope") f.kinds = offeredKinds(p);
      var wrap = el("div");
      var label = el("label", (p.label || p.name) + (p.required ? " *" : ""));
      label.setAttribute("data-label", p.name);
      wrap.appendChild(label);
      if (f.widget === "scope" && f.kinds.length > 1) {
        f.kindSelect = el("select");
        f.kindSelect.setAttribute("data-scope-kind", p.name);
        f.kinds.forEach(function (k) { f.kindSelect.appendChild(option(k, SCOPE_LABEL[k] || k)); });
        f.kindSelect.addEventListener("change", function () {
          loadField(p.name);
          syncButtons();
        });
        wrap.appendChild(f.kindSelect);
      }
      if (f.widget === "scope") {
        var kw = el("p");
        kw.className = "hint";
        kw.setAttribute("data-kinds-why", p.name);
        show(kw, kindsWhy(p));
        wrap.appendChild(kw);
      }
      f.slot = el("div");
      wrap.appendChild(f.slot);
      if (f.widget === "number" || f.widget === "text") {
        f.input = el("input");
        f.input.type = f.widget;
        f.input.setAttribute("data-param", p.name);
        f.input.value = blank(p.default) ? "" : String(p.default);
        f.input.addEventListener("change", function () { changed(p.name); });
        f.input.addEventListener("input", syncButtons);
        f.slot.appendChild(f.input);
      } else if (f.widget === "choice") {
        fill(f, (p.choices || []).map(function (c) { return { id: String(c), label: String(c) }; }));
        if (!blank(p.default)) setValue(f, p.default);
      } else {
        fill(f, []);
      }
      f.why = el("p");
      f.why.className = "hint";
      f.why.setAttribute("data-why", p.name);
      f.why.hidden = true;
      wrap.appendChild(f.why);
      o.fields.appendChild(wrap);
      return f;
    }

    // The scope's chosen kind; none when the site can read no declared kind
    // (the template is then unavailable and Apply is blocked).
    function scopeKind(f) {
      return f.kindSelect ? f.kindSelect.value : f.kinds[0];
    }

    function kindOf(f) {
      if (f.widget === "scope") return KIND_OF_SCOPE[scopeKind(f)] || null;
      if (f.widget === "select" || f.widget === "multiselect") return KIND_OF_TYPE[f.p.type];
      return null;
    }

    // Fetch one picker's choices and choose its value: a prefill first, then
    // what was chosen before, then the rule's default, then the declared one.
    function loadField(name) {
      var f = state.fields[name];
      var kind = f && kindOf(f);
      if (!kind) return Promise.resolve();
      var q = queryFor(f.p, kind, rawValues(), byName());
      f.seq += 1;
      var seq = f.seq;
      var gen = state.gen;
      if (q.missing) {
        var r = raw(f);
        var had = !blank(f.widget === "scope" ? r.code : r);
        f.loading = false;
        fill(f, []);
        show(f.why, "Choose " + q.missing + " first.");
        syncButtons();
        return had ? changed(name) : Promise.resolve();
      }
      // loading is this field's newest request; an older reply that arrives
      // later neither fills the picker nor clears the flag.
      f.loading = true;
      syncButtons();
      return o.fetchChoices(q.kind, q.args).then(function (res) {
        if (seq !== f.seq || gen !== state.gen) return null;
        f.loading = false;
        var c = optionsOf(q.kind, res && res.choices);
        var before = raw(f);
        before = f.widget === "scope" ? before.code : before;
        fill(f, c.options);
        var why = "";
        var value = "";
        if (f.pending !== undefined) {
          var want = f.pending;
          f.pending = undefined;
          var ok = Array.isArray(want) ? want.every(function (v) { return has(f, v); }) : has(f, want);
          if (ok) value = want;
          else why = "This sheet's report used " + JSON.stringify(want) + ", which is not a choice now.";
        } else if (!blank(before) && (Array.isArray(before)
            ? before.every(function (v) { return has(f, v); }) : has(f, before))) {
          value = before;
        } else if (f.p.default === "rule") {
          if (c.default !== null && has(f, c.default)) value = c.default;
          else why = c.why || "The server names no default; choose one.";
        } else if (!blank(f.p.default) && has(f, f.p.default)) {
          value = f.p.default;
        }
        setValue(f, value);
        show(f.why, why);
        return JSON.stringify(value) !== JSON.stringify(blank(before) ? "" : before) ? changed(name) : null;
      }, function (err) {
        if (seq !== f.seq || gen !== state.gen) return null;
        f.loading = false;
        fill(f, []);
        show(f.why, message(err));
        log("err", "Reports: could not load the choices for " + (f.p.label || name) + ": " + message(err));
        return null;
      }).then(function () {
        syncButtons();
      });
    }

    // A param changed: refetch every picker that depends on it.
    function changed(name) {
      syncButtons();
      var t = state.tpl;
      if (!t) return Promise.resolve();
      return Promise.all(t.params.filter(function (p) {
        return (p.depends_on || []).indexOf(name) >= 0;
      }).map(function (p) { return loadField(p.name); }));
    }

    // -- buttons ---------------------------------------------------------

    function blockedBy() {
      var t = state.tpl;
      if (!t) return state.templates.length ? "Choose a report template." : "";
      if (!t.available) return t.why || "This report cannot be read on this site.";
      if (t.mode !== "formulas" && t.mode !== "snapshot") {
        return "This add-in cannot apply a " + t.mode + " report; reload the add-in.";
      }
      var missing = missingRequired(t.params, rawValues());
      if (missing.length) return "Required: " + missing.join(", ") + ".";
      if (loading()) return LOADING;
      return "";
    }

    function loading() {
      return Object.keys(state.fields).some(function (n) { return state.fields[n].loading; });
    }

    function syncButtons() {
      var t = state.tpl;
      var snapshot = !!t && t.mode === "snapshot";
      var reason = blockedBy();
      o.applyButton.hidden = snapshot;
      if (o.snapshotButton) o.snapshotButton.hidden = !snapshot;
      o.applyButton.textContent = buttonLabel(state.inspection, t && t.id);
      o.applyButton.disabled = !!reason || state.busy || !t;
      if (o.snapshotButton) o.snapshotButton.disabled = !!reason || state.busy || !t;
      show(o.blocked, reason);
    }

    function prefill(params) {
      var touched = [];
      Object.keys(params || {}).forEach(function (name) {
        var f = state.fields[name];
        if (!f || f.widget === "hidden") return;
        var v = params[name];
        if (f.widget === "scope") {
          if (!v || typeof v !== "object") return;
          if (f.kindSelect) f.kindSelect.value = v.kind;
          f.pending = v.code;
          touched.push(name);
          return;
        }
        if (f.widget === "number" || f.widget === "text" || f.widget === "choice") {
          setValue(f, v);
        } else if (has(f, v) || (Array.isArray(v) && v.every(function (x) { return has(f, x); }))) {
          setValue(f, v);
        } else {
          f.pending = v;
          touched.push(name);
        }
      });
      // A pending value lands when its picker reloads; reload the ones whose
      // choices are already in, then every dependent of what changed.
      var loads = touched.map(function (n) { return loadField(n); });
      Object.keys(params || {}).forEach(function (n) { loads.push(changed(n)); });
      return Promise.all(loads).then(syncButtons);
    }

    // Ask report.js about the active sheet; prefill once per report it names.
    function checkSheet() {
      var gen = state.gen;
      var t = state.tpl;
      if (!t || t.mode !== "formulas" || typeof o.inspect !== "function") {
        state.inspection = null;
        syncButtons();
        return Promise.resolve(null);
      }
      return Promise.resolve().then(o.inspect).then(function (insp) {
        if (gen !== state.gen) return null;
        state.inspection = insp || null;
        var mine = buttonLabel(insp, t.id) === UPDATE;
        syncButtons();
        if (mine && state.prefilled !== insp.report_id) {
          state.prefilled = insp.report_id;
          return prefill(insp.entry.params);
        }
        if (!mine) state.prefilled = null;
        return null;
      }, function (err) {
        state.inspection = null;
        syncButtons();
        log("info", "Reports: could not read the active sheet (" + message(err) + "); Apply adds a new sheet.");
        return null;
      });
    }

    function find(id) {
      for (var i = 0; i < state.templates.length; i += 1) {
        if (state.templates[i].id === id) return state.templates[i];
      }
      return null;
    }

    function selectTemplate() {
      state.gen += 1;
      state.fields = {};
      state.inspection = null;
      state.prefilled = null;
      state.tpl = find(o.select.value);
      o.fields.textContent = "";
      var t = state.tpl;
      show(o.hint, t ? t.description || "" : "");
      if (!t) {
        syncButtons();
        return Promise.resolve();
      }
      t.params.forEach(function (p) { state.fields[p.name] = renderField(p); });
      syncButtons();
      var gen = state.gen;
      return Promise.all(t.params.map(function (p) {
        return (p.depends_on || []).length ? null : loadField(p.name);
      })).then(function () {
        // Dependents whose parents have no picker (an int, a choice) and
        // those still blank: load what can be loaded now.
        if (gen !== state.gen) return null;
        return Promise.all(t.params.filter(function (p) {
          return (p.depends_on || []).length && !state.fields[p.name].seq;
        }).map(function (p) { return loadField(p.name); }));
      }).then(function () {
        return gen === state.gen ? checkSheet() : null;
      });
    }

    var catalogGen = 0;

    // Read the catalog and list its templates; keep the chosen one if it is
    // still listed.
    function load() {
      catalogGen += 1;
      var mine = catalogGen;
      var keep = o.select.value;
      show(o.error, "");
      return Promise.resolve().then(o.fetchCatalog).then(function (cat) {
        if (mine !== catalogGen) return null;
        var list = (cat && cat.templates) || [];
        if (!list.length) throw new Error("the server lists no report templates");
        state.templates = list;
        o.select.textContent = "";
        var why = [];
        list.forEach(function (t) {
          var opt = option(t.id, t.title + (t.available ? "" : " (unavailable)"));
          opt.disabled = !t.available;
          if (!t.available) {
            opt.title = t.why || "";
            why.push(t.title + ": " + (t.why || "unavailable on this site"));
          }
          o.select.appendChild(opt);
        });
        show(o.templateWhy, why.join("\n"));
        var chosen = find(keep) || list.filter(function (t) { return t.available; })[0] || list[0];
        o.select.value = chosen.id;
        return selectTemplate();
      }).then(null, function (err) {
        if (mine !== catalogGen) return null;
        state.templates = [];
        o.select.textContent = "";
        show(o.templateWhy, "");
        o.select.value = "";
        selectTemplate();
        var text = "Could not load the report list: " + message(err);
        show(o.error, text);
        log("err", "Reports: " + text);
        return null;
      });
    }

    // Recheck the label whenever another sheet is activated (ExcelApi 1.7,
    // which report.js needs to apply at all).
    function watchActivation() {
      if (typeof Excel === "undefined" || !Excel.run) return Promise.resolve(false);
      return Excel.run(function (ctx) {
        ctx.workbook.worksheets.onActivated.add(function () { return checkSheet(); });
        return ctx.sync();
      }).then(function () { return true; }, function (err) {
        log("info", "Reports: the Apply button is not rechecked when you change sheets (" +
          message(err) + "); change the template to recheck it.");
        return false;
      });
    }

    o.select.addEventListener("change", selectTemplate);
    syncButtons();

    return {
      load: load,
      selectTemplate: selectTemplate,
      checkSheet: checkSheet,
      watchActivation: watchActivation,
      template: function () { return state.tpl; },
      values: function () { return state.tpl ? requestParams(state.tpl.params, rawValues()) : {}; },
      missing: function () { return blockedBy(); },
      busy: function (flag) { state.busy = !!flag; syncButtons(); },
    };
  }

  return {
    mount: mount,
    widgetFor: widgetFor,
    optionsOf: optionsOf,
    queryFor: queryFor,
    missingRequired: missingRequired,
    buttonLabel: buttonLabel,
    normaliseRequest: normaliseRequest,
    UPDATE: UPDATE,
    APPLY_NEW: APPLY_NEW,
    LOADING: LOADING,
    KIND_OF_SCOPE: KIND_OF_SCOPE,
    SCOPE_LABEL: SCOPE_LABEL,
  };
})();

if (typeof window !== "undefined") window.konsolReportForm = konsolReportForm;
