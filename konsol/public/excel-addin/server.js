/* global localStorage, location, Office */

// ============================================================
// Konsolidat — which server the add-in talks to (konsol#605 B)
// ============================================================
//
// Decided by Deepak Pai, 27 Nov 2026 (konsol#605). By default the add-in
// talks to the origin its pane was loaded from, with relative URLs, exactly
// as before. The pane's Server field may point it at another https site
// (origin only). That site must list this pane's origin in Close Settings →
// Excel Add-in Origins, or the browser refuses the call.
//
// The choice belongs to the workbook (konsol#618 review): it is kept in the
// workbook's document settings under "konsolServer" and saved with the file.
// localStorage is one per pane origin, shared by every open workbook, so a
// key there let switching the pane in one workbook move another workbook's
// K.* cells to another site. A workbook with no choice talks to the pane's
// origin. The manifest declares a shared runtime (SharedRuntime 1.1,
// <Runtime lifetime="long">): the pane, functions.js and ribbon.js run in one
// window per workbook, so the custom functions read the same
// Office.context.document.settings as the pane, and need no copy of it.
//
// Tokens are kept per server ("konsol_token:<origin>"), so switching server
// never sends one site's token to another. The single "konsol_token" of
// before #605 was always issued by the pane's own origin; it moves there.
//
// Loaded by index.html before functions.js and ribbon.js. Every request URL
// and token comes from here.
// ============================================================

var konsolServer = (function () {
  var SETTING = "konsolServer";
  // The browser-wide key of the first #605 build; never adopted (see below).
  var OLD_SERVER_KEY = "konsol_server";
  var TOKEN_PREFIX = "konsol_token:";
  var LEGACY_TOKEN_KEY = "konsol_token";
  var ORIGINS_SETTING = "Close Settings → Excel Add-in Origins";
  var HOST = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$/;
  var notice = null;

  function read(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }

  function write(key, value) {
    try {
      if (value) localStorage.setItem(key, value);
      else localStorage.removeItem(key);
    } catch (e) { /* localStorage blocked: nothing is kept */ }
  }

  function paneOrigin() {
    return location.origin;
  }

  // This workbook's document settings, or null outside Excel or before
  // Office has initialised.
  function workbookSettings() {
    try {
      var doc = typeof Office !== "undefined" && Office.context && Office.context.document;
      return (doc && doc.settings) || null;
    } catch (e) {
      return null;
    }
  }

  function stored() {
    var settings = workbookSettings();
    if (!settings) return null;
    try { return settings.get(SETTING); } catch (e) { return null; }
  }

  // {origin: "https://host[:port]"} or {error: "why"}. The same rules as
  // konsol/excel_addin_model.py parse_origin: https only, the origin only
  // (one trailing "/" is dropped, and so is :443), no wildcard.
  function parseOrigin(text) {
    var value = String(text === null || text === undefined ? "" : text).trim();
    if (!value) return { error: "Enter the site's address, e.g. https://site.example." };
    if (value.indexOf("*") >= 0) return { error: value + ": no wildcard; give one site's address." };
    var m = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/(.*)$/.exec(value);
    if (!m || m[1].toLowerCase() !== "https") {
      return { error: value + ": the address starts with https:// (Office add-ins need HTTPS)." };
    }
    var rest = m[2];
    if (rest.charAt(rest.length - 1) === "/") rest = rest.slice(0, -1);
    if (/[\/?#@\\]/.test(rest)) {
      return { error: value + ": give the site's address only, with no path: https://host or https://host:port." };
    }
    var colon = rest.indexOf(":");
    var host = (colon < 0 ? rest : rest.slice(0, colon)).toLowerCase();
    var port = colon < 0 ? "" : rest.slice(colon + 1);
    if (!host || !HOST.test(host)) return { error: value + ": the host is not a valid name." };
    if (colon >= 0) {
      if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
        return { error: value + ": the port must be a number from 1 to 65535." };
      }
      port = Number(port) === 443 ? "" : String(Number(port));
    }
    return { origin: "https://" + host + (port ? ":" + port : "") };
  }

  // The legacy token, issued by the pane's own origin, moves to its key.
  (function migrateLegacyToken() {
    var legacy = read(LEGACY_TOKEN_KEY);
    if (!legacy) return;
    if (!read(TOKEN_PREFIX + paneOrigin())) write(TOKEN_PREFIX + paneOrigin(), legacy);
    write(LEGACY_TOKEN_KEY, "");
  })();

  // The first #605 build kept one browser-wide choice. It is dropped, not
  // moved into this workbook: nothing says which workbook it was for.
  (function dropBrowserWideChoice() {
    var old = read(OLD_SERVER_KEY);
    if (!old) return;
    write(OLD_SERVER_KEY, "");
    notice = "The add-in now keeps the server in each workbook. The browser-wide choice \"" + old +
      "\" was dropped; this workbook talks to " + paneOrigin() +
      " until you enter another server in the pane.";
  })();

  // A stored value that is not an origin (edited by hand) is not used, and
  // the pane reports it: the add-in does not guess what was meant.
  function current() {
    var value = stored();
    if (value === null || value === undefined || value === "") return paneOrigin();
    var parsed = parseOrigin(value);
    if (parsed.origin) return parsed.origin;
    notice = "This workbook's saved server \"" + value + "\" is not an https address, so the add-in talks to " +
      paneOrigin() + ". Enter the server again.";
    return paneOrigin();
  }

  function isCrossOrigin() {
    return current() !== paneOrigin();
  }

  // Same origin: the relative path, as before #605. Another site: absolute.
  function url(path) {
    return isCrossOrigin() ? current() + path : path;
  }

  // Blank, or the pane's own origin, goes back to the default. Resolves to
  // {origin}, {error} (nothing changed), or {origin, unsaved}: the workbook
  // uses the new server now, but Excel could not save it with the file.
  // Saved settings reach disk when the user saves the workbook.
  function choose(text) {
    var value = String(text === null || text === undefined ? "" : text).trim();
    var target = "";
    if (value) {
      var parsed = parseOrigin(value);
      if (!parsed.origin) return Promise.resolve(parsed);
      target = parsed.origin === paneOrigin() ? "" : parsed.origin;
    }
    var settings = workbookSettings();
    if (!settings) {
      return Promise.resolve({ error: "Excel's workbook settings are not available, so the server cannot be " +
        "kept with this workbook. Reload the add-in." });
    }
    var origin = target || paneOrigin();
    return new Promise(function (resolve) {
      try {
        if (target) settings.set(SETTING, target);
        else settings.remove(SETTING);
        settings.saveAsync(function (result) {
          if (result && result.status === Office.AsyncResultStatus.Succeeded) {
            resolve({ origin: origin });
          } else {
            resolve({ origin: origin, unsaved: "Excel could not save the server with this workbook (" +
              ((result && result.error && result.error.message) || "unknown error") +
              "); it applies until the workbook is closed." });
          }
        });
      } catch (err) {
        resolve({ origin: current(), unsaved: "Excel could not save the server with this workbook (" +
          (err && err.message || String(err)) + ")." });
      }
    });
  }

  // "host[:port]" of the server, for the row-1 stamp and the log.
  function host() {
    return current().replace(/^https?:\/\//, "");
  }

  function tokenKey(origin) {
    return TOKEN_PREFIX + origin;
  }

  function getToken() {
    return read(tokenKey(current())) || "";
  }

  function setToken(token) {
    write(tokenKey(current()), token || "");
  }

  // A fetch that rejects never reached an answer: the site is down, or it
  // refused this pane's origin at the CORS check. Same origin, only the first.
  function unreachableMessage() {
    var server = current();
    if (!isCrossOrigin()) {
      return "Could not reach " + server + ". Check the connection and try again.";
    }
    return "Could not reach " + server + " from this add-in. If the site is up, ask an admin of " +
      server + " to add " + paneOrigin() + " to " + ORIGINS_SETTING + ".";
  }

  return {
    parseOrigin: parseOrigin,
    paneOrigin: paneOrigin,
    current: current,
    host: host,
    isCrossOrigin: isCrossOrigin,
    url: url,
    choose: choose,
    tokenKey: tokenKey,
    getToken: getToken,
    setToken: setToken,
    unreachableMessage: unreachableMessage,
    notice: function () { return notice; },
  };
})();

if (typeof window !== "undefined") window.konsolServer = konsolServer;
