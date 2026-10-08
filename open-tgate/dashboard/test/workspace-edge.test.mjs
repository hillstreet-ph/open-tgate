import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

const env = { API_BASE_URL: "https://backend.example.test" };
const request = (path, options = {}) => new Request(`https://console.test${path}`, options);

test("workspace routes reject anonymous requests and unknown admin paths", async () => {
  assert.equal((await worker.fetch(request("/api/v1/workspace/chats"), env)).status, 401);
  assert.equal((await worker.fetch(request("/mcp", { method: "POST" }), env)).status, 401);
  assert.equal((await worker.fetch(request("/api/v1/telegram/commands", { method: "POST" }), env)).status, 403);
  assert.equal((await worker.fetch(request("/api/v1/workspace/chats", { method: "DELETE" }), env)).status, 405);
});

test("workspace proxy forwards only bearer auth and uses no-store, manual redirects", async () => {
  const original = globalThis.fetch;
  let seen;
  globalThis.fetch = async (url, init) => { seen = { url: String(url), ...init }; return Response.json({ chats: [] }); };
  try {
    const result = await worker.fetch(request("/api/v1/workspace/chats?account_id=abc", { headers: { authorization: "Bearer operator-token", cookie: "session=private" } }), env);
    assert.equal(result.status, 200);
    assert.equal(result.headers.get("cache-control"), "no-store");
    assert.equal(seen.url, "https://backend.example.test/api/v1/workspace/chats?account_id=abc");
    assert.equal(seen.headers.authorization, "Bearer operator-token");
    assert.equal(seen.headers.cookie, undefined);
    assert.equal(seen.redirect, "manual");
    globalThis.fetch = async () => new Response(null, { status: 302, headers: { location: "https://elsewhere.test" } });
    assert.equal((await worker.fetch(request("/api/v1/workspace/chats", { headers: { authorization: "Bearer operator-token" } }), env)).status, 502);
  } finally { globalThis.fetch = original; }
});

test("proxy bounds streamed JSON and does not allow HTTP production backends", async () => {
  const headers = { authorization: "Bearer operator-token", "content-type": "application/json" };
  const huge = request("/api/v1/workspace/knowledge", { method: "POST", headers, body: "a".repeat(1048577) });
  assert.equal((await worker.fetch(huge, env)).status, 413);
  assert.equal((await worker.fetch(request("/api/v1/workspace/chats", { headers }), { API_BASE_URL: "http://unsafe.test" })).status, 503);
  assert.equal((await worker.fetch(request("/api/v1/workspace/knowledge", { method: "POST", headers: { authorization: "Bearer operator-token" }, body: "hello" }), env)).status, 415);
});
