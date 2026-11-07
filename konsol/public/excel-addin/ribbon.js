/* global Office, Excel */

// ============================================================
// Konsolidat — ribbon commands: Refresh sheet, Refresh workbook
// ============================================================
//
// Decided by Deepak Pai, 7 Nov 2026: two buttons on the add-in's Home-ribbon
// group (manifest ExecuteFunction "refreshSheet" / "refreshWorkbook"), not in
// the task pane. Loaded by index.html, the shared runtime page, after
// office.js and functions.js, so the handlers share the pane's sign-in.
//
// The pane script (index.html) provides, on window:
//   konsolWhoami()                    -> Promise<user or null>
//   konsolLog(level, message)         -> the pane's Messages log
//   konsolidatSnapshotReport(params)  -> re-runs a snapshot and saves its params
//
// Snapshot parameters live in the workbook's document settings under
// SETTINGS_KEY, keyed by sheet name, so they travel with the file.
//
// The stamp "Values as of <YYYY-MM-DD HH:MM> UTC" goes in row 1: in the cell
// that already holds a stamp, else in the first empty cell right of row 1's
// last used cell (G1 on the Monthly P&L, J1 on the TB snapshot). It never
// overwrites a cell that holds something else.
// ============================================================

var konsolRibbon = (function () {
  var SETTINGS_KEY = "konsolSnapshots";
  var STAMP_PREFIX = "Values as of ";
  var SNAPSHOT_KEYS = ["template_id", "entity", "year", "period_from", "period_to", "sheet_name"];
  // A K.* custom function call. Excel may also report one in its file form
  // (_xldudf_K_EPM); count both.
  var K_FORMULA = /(^|[^A-Za-z0-9_.])(K\.|_xldudf_K_)[A-Za-z_]+\s*\(/i;
  var busy = false;

  function log(level, message) {
    try {
      if (typeof window !== "undefined" && typeof window.konsolLog === "function") {
        window.konsolLog(level, message);
        return;
      }
    } catch (e) { /* the pane log is optional */ }
    if (typeof console !== "undefined") console.log("[konsol] " + message);
  }

  // Excel has no non-blocking notification for add-ins (Outlook's
  // notificationMessages has no Excel counterpart), so a failure opens the
  // pane, whose Messages panel shows why. Never a blocking dialog.
  function showPane() {
    try {
      if (Office.addin && typeof Office.addin.showAsTaskpane === "function") {
        var p = Office.addin.showAsTaskpane();
        if (p && typeof p.catch === "function") p.catch(function () { /* pane stays hidden */ });
      }
    } catch (e) { /* SharedRuntime 1.1 missing: the log still has it */ }
  }

  function stampText() {
    return STAMP_PREFIX + new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC";
  }

  function columnLetter(index) {
    var s = "";
    var n = index + 1;
    while (n > 0) {
      var m = (n - 1) % 26;
      s = String.fromCharCode(65 + m) + s;
      n = Math.floor((n - 1) / 26);
    }
    return s;
  }

  // -- snapshot parameters in the workbook -----------------------------------

  function settings() {
    return Office.context.document.settings;
  }

  function savedSnapshots() {
    try {
      var all = settings().get(SETTINGS_KEY);
      return all && typeof all === "object" ? all : {};
    } catch (e) {
      return {};
    }
  }

  function saveSettings() {
    return new Promise(function (resolve, reject) {
      settings().saveAsync(function (result) {
        if (result && result.status === Office.AsyncResultStatus.Succeeded) resolve();
        else reject(new Error("could not save the snapshot settings: " +
          ((result && result.error && result.error.message) || "unknown error")));
      });
    });
  }

  function storeSnapshots(all) {
    try {
      settings().set(SETTINGS_KEY, all);
      return saveSettings();
    } catch (err) {
      return Promise.reject(err);
    }
  }

  // Called by the pane after a snapshot is painted.
  function rememberSnapshot(params) {
    var entry = {};
    SNAPSHOT_KEYS.forEach(function (k) {
      if (params && params[k] !== undefined && params[k] !== null) entry[k] = params[k];
    });
    if (!entry.sheet_name) return Promise.reject(new Error("a snapshot has no sheet name"));
    var all = savedSnapshots();
    all[entry.sheet_name] = entry;
    return storeSnapshots(all);
  }

  function resnapshot(name, params) {
    if (typeof window.konsolidatSnapshotReport !== "function") {
      return Promise.reject(new Error("the pane script is not loaded"));
    }
    return window.konsolidatSnapshotReport(params);
  }

  // -- sign-in ---------------------------------------------------------------

  function signedInUser() {
    if (typeof window.konsolWhoami !== "function") return Promise.resolve(null);
    return window.konsolWhoami().catch(function () { return null; });
  }

  function refuseSignedOut(what) {
    log("err", what + ": not signed in. Open the Konsolidat pane and sign in, then refresh again. " +
      "Nothing was recalculated or stamped.");
    showPane();
  }

  // -- reading and stamping a sheet -----------------------------------------

  function countKFormulas(formulas) {
    var n = 0;
    (formulas || []).forEach(function (row) {
      (row || []).forEach(function (f) {
        if (typeof f === "string" && f.charAt(0) === "=" && K_FORMULA.test(f)) n += 1;
      });
    });
    return n;
  }

  // Queue loads for a sheet's formulas and row 1; read them after ctx.sync().
  function loadSheet(sheet) {
    var used = sheet.getUsedRangeOrNullObject(false);
    used.load("formulas");
    var row1 = sheet.getRange("1:1").getUsedRangeOrNullObject(true);
    row1.load("values,columnIndex");
    return { sheet: sheet, used: used, row1: row1 };
  }

  function formulaCount(loaded) {
    return loaded.used.isNullObject ? 0 : countKFormulas(loaded.used.formulas);
  }

  function stampColumn(row1) {
    if (row1.isNullObject) return 0;
    var values = row1.values[0] || [];
    for (var i = 0; i < values.length; i++) {
      if (typeof values[i] === "string" && values[i].indexOf(STAMP_PREFIX) === 0) {
        return row1.columnIndex + i;
      }
    }
    return row1.columnIndex + values.length;
  }

  // Queue the stamp; returns its address, e.g. "G1".
  function stamp(loaded, text) {
    var col = stampColumn(loaded.row1);
    loaded.sheet.getCell(0, col).values = [[text]];
    return columnLetter(col) + "1";
  }

  function plural(n, word) {
    return n + " " + word + (n === 1 ? "" : "s");
  }

  // -- the commands ----------------------------------------------------------

  function sheetCalcSupported() {
    try {
      return Office.context.requirements.isSetSupported("ExcelApi", "1.6");
    } catch (e) {
      return false;
    }
  }

  function refreshSheet() {
    var what = "Refresh sheet";
    return signedInUser().then(function (user) {
      if (!user) { refuseSignedOut(what); return; }
      var name = null;
      var snapshotOk = false;
      return Excel.run(function (ctx) {
        var active = ctx.workbook.worksheets.getActiveWorksheet();
        active.load("name");
        return ctx.sync().then(function () { name = active.name; });
      }).then(function () {
        var params = savedSnapshots()[name];
        if (!params) return true;
        log("info", what + ": snapshotting " + name + " again (" + params.entity + " " + params.year +
          " P" + params.period_from + "–" + params.period_to + ")…");
        return resnapshot(name, params).then(function () {
          snapshotOk = true;
          return true;
        }, function (err) {
          log("err", what + ": the snapshot on " + name + " failed, so it is not stamped: " +
            (err && err.message || String(err)));
          showPane();
          return false;
        });
      }).then(function (go) {
        if (!go) return;
        return Excel.run(function (ctx) {
          var sheet = ctx.workbook.worksheets.getItem(name);
          if (sheetCalcSupported()) {
            sheet.calculate(true);
          } else {
            ctx.workbook.application.calculate(Excel.CalculationType.full);
            log("info", what + ": this Excel has no sheet recalculation (ExcelApi 1.6), so the whole workbook was recalculated.");
          }
          var loaded = loadSheet(sheet);
          return ctx.sync().then(function () {
            var n = formulaCount(loaded);
            if (!n && !snapshotOk) {
              log("info", what + ": recalculated " + name + "; it has no Konsolidat formulas or snapshot, so it is not stamped.");
              return;
            }
            var text = stampText();
            var at = stamp(loaded, text);
            return ctx.sync().then(function () {
              var parts = [];
              if (n) parts.push(plural(n, "formula"));
              if (snapshotOk) parts.push("1 snapshot");
              log("ok", "Refreshed sheet " + name + ": " + parts.join(", ") + ". " + text + " (in " + at + ").");
            });
          });
        });
      });
    });
  }

  function refreshWorkbook() {
    var what = "Refresh workbook";
    return signedInUser().then(function (user) {
      if (!user) { refuseSignedOut(what); return; }
      var activeName = null;
      var sheetNames = [];
      var snapshotted = {};
      var failed = [];
      var saved = savedSnapshots();
      return Excel.run(function (ctx) {
        var active = ctx.workbook.worksheets.getActiveWorksheet();
        active.load("name");
        var sheets = ctx.workbook.worksheets;
        sheets.load("items/name");
        return ctx.sync().then(function () {
          activeName = active.name;
          sheetNames = sheets.items.map(function (s) { return s.name; });
        });
      }).then(function () {
        // A snapshot whose sheet was deleted is forgotten, not recreated.
        var gone = Object.keys(saved).filter(function (n) { return sheetNames.indexOf(n) < 0; });
        var forget = Promise.resolve();
        if (gone.length) {
          gone.forEach(function (n) { delete saved[n]; });
          log("info", what + ": forgot the snapshot of " + gone.join(", ") + ": the sheet no longer exists.");
          forget = storeSnapshots(saved).catch(function (err) { log("err", what + ": " + err.message); });
        }
        // One snapshot at a time: each is its own Excel.run and server call.
        return Object.keys(saved).reduce(function (chain, n) {
          return chain.then(function () {
            return resnapshot(n, saved[n]).then(function () {
              snapshotted[n] = true;
            }, function (err) {
              failed.push(n);
              log("err", what + ": the snapshot on " + n + " failed, so it is not stamped: " +
                (err && err.message || String(err)));
            });
          });
        }, forget);
      }).then(function () {
        return Excel.run(function (ctx) {
          ctx.workbook.application.calculate(Excel.CalculationType.full);
          var loaded = sheetNames.map(function (n) {
            return { name: n, data: loadSheet(ctx.workbook.worksheets.getItem(n)) };
          });
          return ctx.sync().then(function () {
            var text = stampText();
            var total = 0;
            var stamped = [];
            loaded.forEach(function (l) {
              var n = formulaCount(l.data);
              total += n;
              if (failed.indexOf(l.name) >= 0) return;
              if (n || snapshotted[l.name]) {
                stamp(l.data, text);
                stamped.push(l.name);
              }
            });
            // The snapshots activated their sheets; give the user theirs back.
            if (activeName && sheetNames.indexOf(activeName) >= 0) {
              ctx.workbook.worksheets.getItem(activeName).activate();
            }
            return ctx.sync().then(function () {
              var snaps = Object.keys(snapshotted).length;
              var msg = "Refreshed workbook: " + plural(total, "formula") + ", " + plural(snaps, "snapshot") +
                (stamped.length ? "; stamped " + stamped.join(", ") + ". " + text + "." : "; no sheet to stamp.");
              log(failed.length ? "err" : "ok", msg + (failed.length ? " Failed: " + failed.join(", ") + "." : ""));
              if (failed.length) showPane();
            });
          });
        });
      });
    });
  }

  // Every path calls event.completed() exactly once: Excel greys the ribbon
  // button until it does.
  function command(what, run) {
    return function (event) {
      var done = false;
      function complete() {
        if (done) return;
        done = true;
        busy = false;
        try { if (event && typeof event.completed === "function") event.completed(); } catch (e) { /* nothing to tell */ }
      }
      if (busy) {
        log("info", what + ": a refresh is already running.");
        done = true;
        try { if (event && typeof event.completed === "function") event.completed(); } catch (e) { /* nothing to tell */ }
        return;
      }
      busy = true;
      var p;
      try {
        p = Promise.resolve(run());
      } catch (err) {
        p = Promise.reject(err);
      }
      p.catch(function (err) {
        log("err", what + " failed: " + (err && err.message || String(err)));
        showPane();
      }).then(complete, complete);
    };
  }

  return {
    rememberSnapshot: rememberSnapshot,
    savedSnapshots: savedSnapshots,
    refreshSheet: command("Refresh sheet", refreshSheet),
    refreshWorkbook: command("Refresh workbook", refreshWorkbook),
  };
})();

if (typeof window !== "undefined") window.konsolRibbon = konsolRibbon;

// Bind the manifest's ExecuteFunction names. office.js is loaded first, so
// Office.actions exists here; Microsoft's shared-runtime samples associate at
// load time in the same way.
if (typeof Office !== "undefined" && Office.actions && typeof Office.actions.associate === "function") {
  Office.actions.associate("refreshSheet", konsolRibbon.refreshSheet);
  Office.actions.associate("refreshWorkbook", konsolRibbon.refreshWorkbook);
}
