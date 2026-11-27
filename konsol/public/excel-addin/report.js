/* global Office, Excel, konsolServer, crypto */

// ============================================================
// Konsolidat — apply a compiled report to the workbook (konsol#548 v1)
// ============================================================
//
// Decided by Deepak Pai, 27 Nov 2026 (konsol#548, Q3): a re-apply updates
// only a sheet the add-in recognises as its own report; anything else gets a
// new sheet. Sheets painted before v1 carry no marker and are not adopted.
//
// The marker has two parts, and a sheet is the add-in's own only when both
// exist and agree:
//   1. a sheet-scoped named item "konsol_report" over the report's area, with
//      the comment "konsol-report:<report_id>". Excel keeps it in step when
//      rows or columns are inserted, so its range is where the report is now,
//      and the area that is cleared;
//   2. the workbook's document setting konsolReports[report_id] =
//      {template_id, template_version, params, area, server, worksheet_id,
//       applied_at}. ``area`` is the compiled area (the cell map's own
//       addresses), not where the sheet holds it. konsolSnapshots
//       (ribbon.js) is never touched.
// A copied sheet keeps the name, but its worksheet id is not the entry's: it
// is treated as own, and applying forks it into a new report id and entry.
//
// An update first moves the cell map to where the report now is: when the
// user inserted rows above or columns left of it, the name's top-left is no
// longer the entry's, and every cell, format and cell reference in a formula
// is shifted by that offset (rows and columns). It then clears ONLY the named
// area, writes the cells and formats, moves the name to the new area and
// updates the entry. It refuses, writing nothing, when the report was applied
// from another server, or when a larger area would cover non-empty cells
// outside the old one. A cell outside the report's area is never written.
//
// Loaded by index.html after server.js. The pane may provide
// window.konsolLog(level, message); without it, messages go to the console.
// ============================================================

var konsolReport = (function () {
  var NAME = "konsol_report";
  var COMMENT_PREFIX = "konsol-report:";
  var SETTINGS_KEY = "konsolReports";
  var MAX_SHEET_NAME = 31;
  var MAX_LISTED = 10;
  var WHAT = "Apply report";

  function log(level, message) {
    try {
      if (typeof window !== "undefined" && typeof window.konsolLog === "function") {
        window.konsolLog(level, message);
        return;
      }
    } catch (e) { /* the pane log is optional */ }
    if (typeof console !== "undefined") console.log("[konsol] " + message);
  }

  // -- A1 addresses ----------------------------------------------------------

  function colIndex(letters) {
    var n = 0;
    for (var i = 0; i < letters.length; i++) n = n * 26 + (letters.charCodeAt(i) - 64);
    return n - 1;
  }

  function colLetter(index) {
    var s = "";
    var n = index + 1;
    while (n > 0) {
      var m = (n - 1) % 26;
      s = String.fromCharCode(65 + m) + s;
      n = Math.floor((n - 1) / 26);
    }
    return s;
  }

  // "A1" or "B2:M5" (a sheet prefix such as 'P&L'! is dropped) to
  // {r1, c1, r2, c2}, zero-based; null when it is not a cell rectangle.
  function parseArea(text) {
    var value = String(text === null || text === undefined ? "" : text);
    var bang = value.lastIndexOf("!");
    if (bang >= 0) value = value.slice(bang + 1);
    var m = /^\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?$/.exec(value.toUpperCase());
    if (!m) return null;
    var r1 = Number(m[2]) - 1;
    var c1 = colIndex(m[1]);
    var r2 = m[3] ? Number(m[4]) - 1 : r1;
    var c2 = m[3] ? colIndex(m[3]) : c1;
    if (r1 < 0 || r2 < 0) return null;
    return { r1: Math.min(r1, r2), c1: Math.min(c1, c2), r2: Math.max(r1, r2), c2: Math.max(c1, c2) };
  }

  function cellAddress(r, c) {
    return colLetter(c) + (r + 1);
  }

  function areaText(g) {
    var a = cellAddress(g.r1, g.c1);
    var b = cellAddress(g.r2, g.c2);
    return a === b ? a : a + ":" + b;
  }

  function contains(outer, inner) {
    return inner.r1 >= outer.r1 && inner.c1 >= outer.c1 && inner.r2 <= outer.r2 && inner.c2 <= outer.c2;
  }

  function inside(g, r, c) {
    return r >= g.r1 && r <= g.r2 && c >= g.c1 && c <= g.c2;
  }

  function union(a, b) {
    if (!a) return b;
    return { r1: Math.min(a.r1, b.r1), c1: Math.min(a.c1, b.c1), r2: Math.max(a.r2, b.r2), c2: Math.max(a.c2, b.c2) };
  }

  function listCells(cells) {
    if (cells.length <= MAX_LISTED) return cells.join(", ");
    return cells.slice(0, MAX_LISTED).join(", ") + " and " + (cells.length - MAX_LISTED) + " more";
  }

  // -- the cell map ----------------------------------------------------------

  // What apply needs from a cell map, or {error}. The area is report.area
  // when the compiler declares it, else the rectangle the cells cover.
  function planOf(cellMap) {
    if (!cellMap || typeof cellMap !== "object" || !Array.isArray(cellMap.cells)) {
      return { error: "the cell map has no cells" };
    }
    var report = cellMap.report && typeof cellMap.report === "object" ? cellMap.report : {};
    var templateId = report.template_id || cellMap.template_id;
    if (!templateId) return { error: "the cell map names no template" };
    var cells = cellMap.cells;
    var formats = Array.isArray(cellMap.formats) ? cellMap.formats : [];
    var bbox = null;
    var all = cells.concat(formats);
    for (var i = 0; i < all.length; i++) {
      var g = parseArea(all[i] && all[i].range);
      if (!g) return { error: "the cell map has a range that is not a cell address: " + (all[i] && all[i].range) };
      all[i]._g = g;
      bbox = union(bbox, g);
    }
    var area = bbox;
    if (report.area) {
      area = parseArea(report.area);
      if (!area) return { error: "the report area " + report.area + " is not a cell range" };
    }
    if (!area) return { error: "the cell map has no cells" };
    var outside = all.filter(function (x) { return !contains(area, x._g); }).map(function (x) { return x.range; });
    if (outside.length) {
      return { error: "the cell map writes " + listCells(outside) + " outside its report area " + areaText(area) };
    }
    var params = report.params;
    if (!params) {
      params = {};
      Object.keys(cellMap).forEach(function (k) {
        if (["cells", "formats", "report", "template_id", "mode", "sheet_name"].indexOf(k) < 0) params[k] = cellMap[k];
      });
    }
    return {
      templateId: templateId,
      templateVersion: report.template_version === undefined ? null : report.template_version,
      params: params,
      sheetName: String(cellMap.sheet_name || templateId).slice(0, MAX_SHEET_NAME),
      areaG: area,
      area: areaText(area),
      cells: cells,
      formats: formats,
    };
  }

  // -- moving a plan ---------------------------------------------------------

  // A1 reference in a formula: not inside a "string", not part of a name
  // (K.EPM, LOG10( is a function), not a sheet-qualified or R1C1 form.
  var REF = /(\$?)([A-Z]{1,3})(\$?)(\d+)/g;

  function shiftRefs(text, dr, dc) {
    return text.replace(REF, function (m, d1, col, d2, row, at, all) {
      var before = at > 0 ? all.charAt(at - 1) : "";
      var after = all.charAt(at + m.length);
      if (/[A-Za-z0-9_.!]/.test(before) || after === "(" || /[A-Za-z0-9_]/.test(after)) return m;
      return d1 + colLetter(colIndex(col) + dc) + d2 + (Number(row) + dr);
    });
  }

  // A formula with its cell references moved by (dr, dc); text in quotes
  // (a K.EPM entity or account code) is kept as it is.
  function shiftFormula(formula, dr, dc) {
    if (typeof formula !== "string" || formula.charAt(0) !== "=") return formula;
    var parts = formula.split('"');
    for (var i = 0; i < parts.length; i += 2) parts[i] = shiftRefs(parts[i], dr, dc);
    return parts.join('"');
  }

  function shiftArea(g, dr, dc) {
    return { r1: g.r1 + dr, c1: g.c1 + dc, r2: g.r2 + dr, c2: g.c2 + dc };
  }

  // The plan written at (dr, dc) from its compiled place. ``area`` stays the
  // compiled area (it goes into the entry); ``at``/``atG`` are where it is
  // written.
  function movePlan(plan, dr, dc) {
    var moved = Object.assign({}, plan);
    moved.atG = shiftArea(plan.areaG, dr, dc);
    moved.at = areaText(moved.atG);
    if (!dr && !dc) return moved;
    moved.cells = plan.cells.map(function (cell) {
      var out = Object.assign({}, cell);
      out.range = areaText(shiftArea(cell._g, dr, dc));
      if (cell.formulas) {
        out.formulas = cell.formulas.map(function (row) {
          return row.map(function (f) { return shiftFormula(f, dr, dc); });
        });
      }
      return out;
    });
    moved.formats = plan.formats.map(function (f) {
      var out = Object.assign({}, f);
      out._g = shiftArea(f._g, dr, dc);
      out.range = areaText(out._g);
      return out;
    });
    return moved;
  }

  // -- settings --------------------------------------------------------------

  function settings() {
    return Office.context.document.settings;
  }

  function savedReports() {
    try {
      var all = settings().get(SETTINGS_KEY);
      return all && typeof all === "object" ? all : {};
    } catch (e) {
      return {};
    }
  }

  // Resolves "" or why Excel could not keep the entry with the file.
  function storeEntry(reportId, entry) {
    var why = function (detail) {
      return "Excel could not save the report settings with this workbook (" + detail +
        "); Update will not recognise this sheet after the workbook is closed.";
    };
    return new Promise(function (resolve) {
      try {
        var all = savedReports();
        all[reportId] = entry;
        settings().set(SETTINGS_KEY, all);
        settings().saveAsync(function (result) {
          if (result && result.status === Office.AsyncResultStatus.Succeeded) resolve("");
          else resolve(why((result && result.error && result.error.message) || "unknown error"));
        });
      } catch (err) {
        resolve(why(err && err.message || String(err)));
      }
    });
  }

  function newReportId(taken) {
    for (;;) {
      var id = "";
      if (typeof crypto !== "undefined" && crypto && typeof crypto.getRandomValues === "function") {
        var bytes = crypto.getRandomValues(new Uint8Array(8));
        for (var i = 0; i < bytes.length; i++) id += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
      } else {
        id = Math.random().toString(16).slice(2, 10) + Math.random().toString(16).slice(2, 10);
      }
      if (!taken[id]) return id;
    }
  }

  // -- the server ------------------------------------------------------------

  function serverOrigin() {
    if (typeof konsolServer !== "undefined") return konsolServer.current();
    return typeof location !== "undefined" ? location.origin : "";
  }

  // The workbook keeps its server from its first apply on (server.js pin).
  function pinServer() {
    if (typeof konsolServer === "undefined" || typeof konsolServer.pin !== "function") return Promise.resolve("");
    return Promise.resolve(konsolServer.pin()).then(function (r) {
      return (r && r.unsaved) || "";
    }, function () { return ""; });
  }

  function otherServer(sheetName, entry) {
    var now = serverOrigin();
    if (!entry || !entry.server || entry.server === now) return "";
    return "the report on " + sheetName + " was applied from " + entry.server + ", but this workbook now reads " +
      now + ". Switch the pane's Server back, or apply the report to a new sheet";
  }

  function apiSupported() {
    try {
      return typeof Office !== "undefined" && Office.context && Office.context.requirements &&
        Office.context.requirements.isSetSupported("ExcelApi", "1.7");
    } catch (e) {
      return false;
    }
  }

  // -- reading the marker ----------------------------------------------------

  // Read a sheet's marker: resolves {sheet, item, range} for classify(). The
  // name's range is asked for only once the name is known to exist: a method
  // on a null object is not something to rely on.
  function probe(ctx, sheet) {
    sheet.load("name,id");
    var item = sheet.names.getItemOrNullObject(NAME);
    item.load("name,comment");
    return ctx.sync().then(function () {
      if (item.isNullObject) return { sheet: sheet, item: item, range: null };
      var range = item.getRangeOrNullObject();
      range.load("address");
      return ctx.sync().then(function () { return { sheet: sheet, item: item, range: range }; });
    });
  }

  // {own, copied, report_id, entry, area, reason}. own: both marker parts
  // exist and agree. copied: the entry belongs to another worksheet (the
  // sheet is a copy); apply forks it into a new report.
  function classify(p, reports) {
    var name = p.sheet.name;
    var res = { own: false, copied: false, report_id: null, entry: null, area: null, sheet: name, reason: "" };
    if (p.item.isNullObject) {
      res.reason = name + " has no " + NAME + " name: the add-in did not apply a report there (or applied it before v1)";
      return res;
    }
    var m = /^konsol-report:(.+)$/.exec(String(p.item.comment || ""));
    if (!m) {
      res.reason = "the " + NAME + " name on " + name + " has no " + COMMENT_PREFIX + "<id> comment";
      return res;
    }
    res.report_id = m[1];
    var entry = reports[res.report_id];
    if (!entry || typeof entry !== "object") {
      res.reason = "this workbook has no settings for report " + res.report_id + " on " + name;
      return res;
    }
    if (!p.range || p.range.isNullObject) {
      res.reason = "the " + NAME + " name on " + name + " no longer refers to cells";
      return res;
    }
    var g = parseArea(p.range.address);
    if (!g) {
      res.reason = "the " + NAME + " name on " + name + " refers to " + p.range.address + ", not a cell range";
      return res;
    }
    res.entry = entry;
    res.area = areaText(g);
    res.own = true;
    if (entry.worksheet_id !== p.sheet.id) {
      res.copied = true;
      res.reason = name + " is a copy of report " + res.report_id + "; applying makes it a report of its own";
    } else {
      res.reason = name + " holds report " + res.report_id + " (" + entry.template_id + ")";
    }
    return res;
  }

  function publicView(state) {
    return { own: state.own, copied: state.copied, report_id: state.report_id, entry: state.entry,
      area: state.area, sheet: state.sheet, reason: state.reason };
  }

  // Resolves classify()'s answer for the named sheet, or the active one.
  function inspect(sheetName) {
    if (typeof Excel === "undefined") {
      return Promise.resolve({ own: false, copied: false, report_id: null, entry: null, area: null,
        sheet: null, reason: "Excel API unavailable" });
    }
    return Excel.run(function (ctx) {
      var sheets = ctx.workbook.worksheets;
      var sheet = sheetName ? sheets.getItemOrNullObject(sheetName) : sheets.getActiveWorksheet();
      return ctx.sync().then(function () {
        if (sheet.isNullObject) {
          return { own: false, copied: false, report_id: null, entry: null, area: null, sheet: sheetName,
            reason: "there is no sheet " + sheetName };
        }
        return probe(ctx, sheet).then(function (p) { return publicView(classify(p, savedReports())); });
      });
    });
  }

  // -- writing ---------------------------------------------------------------

  function writeCells(sheet, plan) {
    plan.cells.forEach(function (cell) {
      var range = sheet.getRange(cell.range);
      if (cell.formulas) range.formulas = cell.formulas;
      else if (cell.values) range.values = cell.values;
    });
    plan.formats.forEach(function (f) {
      var range = sheet.getRange(f.range);
      if (f.number_format !== undefined && f.number_format !== null) {
        var g = f._g;
        var grid = [];
        for (var r = g.r1; r <= g.r2; r++) {
          var row = [];
          for (var c = g.c1; c <= g.c2; c++) row.push(f.number_format);
          grid.push(row);
        }
        range.numberFormat = grid;
      }
      if (f.bold !== undefined && f.bold !== null) range.format.font.bold = !!f.bold;
    });
  }

  function entryFor(plan, worksheetId) {
    return {
      template_id: plan.templateId,
      template_version: plan.templateVersion,
      params: plan.params,
      area: plan.area,
      server: serverOrigin(),
      worksheet_id: worksheetId,
      applied_at: new Date().toISOString(),
    };
  }

  // "<name>", else "<name> (2)", "(3)"… Excel compares names without case
  // and allows 31 characters.
  function freeSheetName(base, taken) {
    var lower = taken.map(function (n) { return String(n).toLowerCase(); });
    if (lower.indexOf(base.toLowerCase()) < 0) return base;
    for (var n = 2; ; n++) {
      var suffix = " (" + n + ")";
      var name = base.slice(0, MAX_SHEET_NAME - suffix.length) + suffix;
      if (lower.indexOf(name.toLowerCase()) < 0) return name;
    }
  }

  function outcome(action, sheet, area, message) {
    log(action === "refused" ? "err" : "ok", message);
    return { action: action, sheet: sheet, area: area, message: message };
  }

  function refuse(sheet, area, why) {
    return outcome("refused", sheet, area, WHAT + ": " + why + ". Nothing was changed.");
  }

  function withNotes(message, notes) {
    var extra = notes.filter(function (n) { return n; });
    return extra.length ? message + " " + extra.join(" ") : message;
  }

  function update(ctx, sheet, p, state, plan, pinNote) {
    var name = state.sheet;
    var refused = otherServer(name, state.entry);
    if (refused) return Promise.resolve(refuse(name, plan.area, refused));
    var oldG = parseArea(state.area);
    // Where the entry's compiled area now sits: the name's top-left minus
    // the entry's (rows or columns inserted above / left of the report).
    var was = parseArea(state.entry && state.entry.area) || plan.areaG;
    var compiled = plan;
    plan = movePlan(compiled, oldG.r1 - was.r1, oldG.c1 - was.c1);
    var check = Promise.resolve([]);
    if (!contains(oldG, plan.atG)) {
      var grown = sheet.getRange(plan.at);
      grown.load("formulas");
      check = ctx.sync().then(function () {
        var busy = [];
        var f = grown.formulas;
        for (var r = 0; r < f.length; r++) {
          for (var c = 0; c < f[r].length; c++) {
            var row = plan.atG.r1 + r;
            var col = plan.atG.c1 + c;
            if (inside(oldG, row, col)) continue;
            if (f[r][c] !== "" && f[r][c] !== null && f[r][c] !== undefined) busy.push(cellAddress(row, col));
          }
        }
        return busy;
      });
    }
    return check.then(function (busy) {
      if (busy.length) {
        return refuse(name, plan.at, "the report on " + name + " now needs " + plan.at + ", but " +
          listCells(busy) + " outside its area " + state.area + (busy.length === 1 ? " is" : " are") +
          " not empty. Clear " + (busy.length === 1 ? "that cell" : "those cells") +
          ", or apply the report to a new sheet");
      }
      var reports = savedReports();
      var reportId = state.copied ? newReportId(reports) : state.report_id;
      sheet.getRange(state.area).clear();
      writeCells(sheet, plan);
      p.item.delete();
      sheet.names.add(NAME, sheet.getRange(plan.at), COMMENT_PREFIX + reportId);
      return ctx.sync().then(function () {
        return storeEntry(reportId, entryFor(compiled, p.sheet.id));
      }).then(function (unsaved) {
        var msg = state.copied
          ? "Updated " + name + " (" + plan.at + ") as a new report: the sheet was a copy of report " +
            state.report_id + "."
          : "Updated report " + plan.templateId + " on " + name + " (" + plan.at + ").";
        return outcome("update", name, plan.at, withNotes(msg, [pinNote, unsaved]));
      });
    });
  }

  function addSheet(ctx, sheets, plan, why, pinNote) {
    var taken = sheets.items.map(function (s) { return s.name; });
    var name = freeSheetName(plan.sheetName, taken);
    var sheet = sheets.add(name);
    writeCells(sheet, plan);
    var reportId = newReportId(savedReports());
    sheet.names.add(NAME, sheet.getRange(plan.area), COMMENT_PREFIX + reportId);
    sheet.activate();
    sheet.load("id,name");
    return ctx.sync().then(function () {
      return storeEntry(reportId, entryFor(plan, sheet.id));
    }).then(function (unsaved) {
      var msg = "Applied report " + plan.templateId + " to new sheet " + name + " (" + plan.area + ")" +
        (why ? ": " + why : "") + ".";
      return outcome("new", name, plan.area, withNotes(msg, [pinNote, unsaved]));
    });
  }

  // options.target: "active" (default) updates the active sheet when it is
  // the add-in's own report of the same template, else writes a new sheet;
  // "new" always writes a new sheet. Resolves {action: "update"|"new"|
  // "refused", sheet, area, message}; rejects only when Excel itself fails.
  function apply(cellMap, options) {
    var target = options && options.target === "new" ? "new" : "active";
    if (typeof Excel === "undefined") return Promise.resolve(refuse(null, null, "the Excel API is unavailable"));
    if (!apiSupported()) {
      return Promise.resolve(refuse(null, null, "this version of Excel does not support ExcelApi 1.7, which " +
        "applying a report needs. Update Excel, or use Excel on the web"));
    }
    var plan = planOf(cellMap);
    if (plan.error) return Promise.resolve(refuse(cellMap && cellMap.sheet_name || null, null, plan.error));
    return pinServer().then(function (pinNote) {
      return Excel.run(function (ctx) {
        var sheets = ctx.workbook.worksheets;
        sheets.load("items/name");
        var active = target === "active" ? sheets.getActiveWorksheet() : null;
        return ctx.sync().then(function () {
          return active ? probe(ctx, active) : null;
        }).then(function (p) {
          var state = p ? classify(p, savedReports()) : null;
          if (state && state.own && state.entry.template_id === plan.templateId) {
            return update(ctx, active, p, state, plan, pinNote);
          }
          var why = "";
          if (!state) why = "a new sheet was asked for";
          else if (state.own) {
            why = state.sheet + " holds report " + state.entry.template_id + ", not " + plan.templateId;
          } else why = state.reason;
          return addSheet(ctx, sheets, plan, why, pinNote);
        });
      });
    }).then(null, function (err) {
      log("err", WHAT + " failed: " + (err && err.message || String(err)));
      throw err;
    });
  }

  return {
    NAME: NAME,
    SETTINGS_KEY: SETTINGS_KEY,
    inspect: inspect,
    apply: apply,
    savedReports: savedReports,
    parseArea: parseArea,
  };
})();

if (typeof window !== "undefined") window.konsolReport = konsolReport;
