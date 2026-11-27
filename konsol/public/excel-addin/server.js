/* global localStorage, location */

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
// Tokens are kept per server ("konsol_token:<origin>"), so switching server
// never sends one site's token to another. The single "konsol_token" of
// before #605 was always issued by the pane's own origin; it moves there.
//
// Loaded by index.html before functions.js and ribbon.js (shared runtime:
// one window, one localStorage). Every request URL and token comes from here.
// ============================================================

var konsolServer = (function () {
  var SERVER_KEY = "konsol_server";
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

  // A stored server that is not an origin (edited by hand) is dropped, and
  // the pane reports it: the add-in does not guess what was meant.
  (function checkStored() {
    var stored = read(SERVER_KEY);
    if (stored && !parseOrigin(stored).origin) {
      write(SERVER_KEY, "");
      notice = "The saved server \"" + stored + "\" is not an https address, so it was dropped; " +
        "the add-in talks to " + paneOrigin() + ". Enter the server again.";
    }
  })();

  function current() {
    var stored = read(SERVER_KEY);
    var parsed = stored ? parseOrigin(stored) : {};
    return parsed.origin || paneOrigin();
  }

  function isCrossOrigin() {
    return current() !== paneOrigin();
  }

  // Same origin: the relative path, as before #605. Another site: absolute.
  function url(path) {
    return isCrossOrigin() ? current() + path : path;
  }

  // Blank, or the pane's own origin, goes back to the default.
  function choose(text) {
    if (!String(text === null || text === undefined ? "" : text).trim()) {
      write(SERVER_KEY, "");
      return { origin: paneOrigin() };
    }
    var parsed = parseOrigin(text);
    if (!parsed.origin) return parsed;
    write(SERVER_KEY, parsed.origin === paneOrigin() ? "" : parsed.origin);
    return parsed;
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
