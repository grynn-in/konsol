// konsol#305 B06: api.test.mjs
//
// Pure API client tests. `fetchImpl` is always injected — no global fetch or
// window mocking except where a real server global (window.csrf_token) is
// the thing under test, and that is restored in a finally.
import { test } from "node:test";
import assert from "node:assert/strict";
import { get, post, errorMessage } from "./api.js";

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function htmlResponse(status) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      throw new SyntaxError("Unexpected token < in JSON at position 0");
    },
  };
}

test("a GET builds the URL with its parameters", async () => {
  let calledUrl;
  let calledOpts;
  const fetchImpl = async (url, opts) => {
    calledUrl = url;
    calledOpts = opts;
    return jsonResponse(200, { message: { ok: true } });
  };
  const result = await get(
    "konsol.close.period_api.get_context",
    { fiscal_year: 2025, fiscal_period: 7 },
    { fetchImpl }
  );
  assert.equal(
    calledUrl,
    "/api/method/konsol.close.period_api.get_context?fiscal_year=2025&fiscal_period=7"
  );
  assert.equal(calledOpts.method, "GET");
  assert.equal(calledOpts.credentials, "include");
  assert.deepEqual(result, { ok: true });
});

test("a GET with no parameters builds a bare URL", async () => {
  let calledUrl;
  const fetchImpl = async (url) => {
    calledUrl = url;
    return jsonResponse(200, { message: null });
  };
  await get("konsol.close.period_api.get_context", null, { fetchImpl });
  assert.equal(calledUrl, "/api/method/konsol.close.period_api.get_context");
});

test("a POST carries csrf_token", async () => {
  const originalWindow = globalThis.window;
  globalThis.window = { csrf_token: "zz-csrf-123" };
  try {
    let calledOpts;
    const fetchImpl = async (url, opts) => {
      calledOpts = opts;
      return jsonResponse(200, { message: "ok" });
    };
    await post(
      "konsol.close.tb_upload_api.submit",
      { fiscal_year: 2025 },
      { fetchImpl }
    );
    assert.equal(calledOpts.method, "POST");
    assert.equal(calledOpts.credentials, "include");
    const sentBody = JSON.parse(calledOpts.body);
    assert.equal(sentBody.csrf_token, "zz-csrf-123");
    assert.equal(sentBody.fiscal_year, 2025);
  } finally {
    globalThis.window = originalWindow;
  }
});

test("HTTP 403 with _server_messages raises an Error with the joined messages", async () => {
  const serverMessages = JSON.stringify([
    JSON.stringify({ message: "Not permitted for role EPM User." }),
    JSON.stringify({ message: "Contact your Close Lead." }),
  ]);
  const fetchImpl = async () =>
    jsonResponse(403, { exc_type: "PermissionError", _server_messages: serverMessages });
  await assert.rejects(
    () => get("konsol.close.period_api.get_context", {}, { fetchImpl }),
    (err) => {
      assert.ok(err instanceof Error);
      assert.equal(
        err.message,
        "Not permitted for role EPM User. Contact your Close Lead."
      );
      return true;
    }
  );
});

test("HTTP 417 with exc raises an Error with message", async () => {
  const fetchImpl = async () =>
    jsonResponse(417, {
      exc: "Traceback...\nfrappe.exceptions.ValidationError: The period is closed.",
      message: "The period is closed.",
    });
  await assert.rejects(
    () => post("konsol.close.tb_upload_api.submit", {}, { fetchImpl }),
    (err) => {
      assert.ok(err instanceof Error);
      assert.equal(err.message, "The period is closed.");
      return true;
    }
  );
});

test("a rejecting fetchImpl raises a Network error", async () => {
  const fetchImpl = async () => {
    throw new TypeError("fetch failed: getaddrinfo ENOTFOUND");
  };
  await assert.rejects(
    () => get("konsol.close.period_api.get_context", {}, { fetchImpl }),
    (err) => {
      assert.ok(err instanceof Error);
      assert.equal(err.message, "Network error: fetch failed: getaddrinfo ENOTFOUND");
      return true;
    }
  );
});

test("a non-JSON HTML response raises Unexpected response (status)", async () => {
  const fetchImpl = async () => htmlResponse(500);
  await assert.rejects(
    () => get("konsol.close.period_api.get_context", {}, { fetchImpl }),
    (err) => {
      assert.ok(err instanceof Error);
      assert.equal(err.message, "Unexpected response (500)");
      return true;
    }
  );
});

test("errorMessage falls back to a named status when the payload has nothing usable", () => {
  assert.equal(errorMessage({}, 500), "Unexpected response (500)");
  assert.equal(errorMessage(null, 502), "Unexpected response (502)");
});

test("errorMessage prefers _server_messages over a bare message", () => {
  const serverMessages = JSON.stringify([JSON.stringify({ message: "Declared reason." })]);
  const payload = { _server_messages: serverMessages, message: "generic error" };
  assert.equal(errorMessage(payload, 403), "Declared reason.");
});
