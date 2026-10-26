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

const sourcePath = "/api/v1/workspace/knowledge/12345678-1234-1234-1234-123456789abc";
const revokePath = "/api/v1/workspace/keys/12345678-1234-1234-1234-123456789abc/revoke";
const bodylessRoutes = [{ path: sourcePath, method: "DELETE" }, { path: revokePath, method: "POST" }];

for (const route of bodylessRoutes) {
  test(`${route.method} ${route.path} forwards the intentional bodyless action`, async () => {
    const original = globalThis.fetch;
    let seen;
    globalThis.fetch = async (url, init) => {
      seen = { url: String(url), ...init };
      return route.method === "DELETE" ? new Response(null, { status: 204 }) : Response.json({ key: { revoked_at: "2026-10-09" } });
    };
    try {
      const result = await worker.fetch(request(route.path, {
        method: route.method, headers: { authorization: "Bearer operator-token", cookie: "session=private" },
      }), { ...env, API_ADMIN_TOKEN: "must-not-be-injected" });
      assert.equal(result.status, route.method === "DELETE" ? 204 : 200);
      assert.equal(seen.url, `https://backend.example.test${route.path}`);
      assert.equal(seen.method, route.method);
      assert.equal(seen.body, undefined);
      assert.equal(seen.headers.authorization, "Bearer operator-token");
      assert.equal(seen.headers.cookie, undefined);
      assert.equal(seen.redirect, "manual");
      assert.equal(result.headers.get("cache-control"), "no-store");
    } finally { globalThis.fetch = original; }
  });

  test(`${route.method} optional supplied bodies still require valid bounded JSON`, async () => {
    const original = globalThis.fetch;
    let calls = 0;
    let seen;
    globalThis.fetch = async (_, init) => { calls += 1; seen = init; return Response.json({ ok: true }); };
    const auth = { authorization: "Bearer operator-token" };
    try {
      const valid = await worker.fetch(request(route.path, {
        method: route.method, headers: { ...auth, "content-type": "application/json; charset=utf-8" }, body: '{"note":"optional"}',
      }), env);
      assert.equal(valid.status, 200);
      assert.deepEqual(JSON.parse(new TextDecoder().decode(seen.body)), { note: "optional" });
      assert.equal(calls, 1);
      for (const contentType of ["text/plain", "application/jsonp"]) {
        assert.equal((await worker.fetch(request(route.path, {
          method: route.method, headers: { ...auth, "content-type": contentType }, body: "{}",
        }), env)).status, 415);
      }
      assert.equal((await worker.fetch(request(route.path, {
        method: route.method, headers: { ...auth, "content-type": "application/json" }, body: "{invalid-json",
      }), env)).status, 400);
      assert.equal((await worker.fetch(request(route.path, {
        method: route.method, headers: { ...auth, "content-type": "application/json" },
        body: new Uint8Array([0x7b, 0x22, 0x78, 0x22, 0x3a, 0x22, 0xff, 0x22, 0x7d]),
      }), env)).status, 400);
      assert.equal((await worker.fetch(request(route.path, {
        method: route.method, headers: { ...auth, "content-length": "1048577" },
      }), env)).status, 413);
      let cancelled = false;
      const body = new ReadableStream({
        start(controller) {
          controller.enqueue(new Uint8Array(524288));
          controller.enqueue(new Uint8Array(524288));
          controller.enqueue(new Uint8Array(1));
        },
        cancel() { cancelled = true; },
      });
      assert.equal((await worker.fetch(request(route.path, {
        method: route.method, headers: { ...auth, "content-type": "application/json" }, body, duplex: "half",
      }), env)).status, 413);
      assert.equal(cancelled, true);
      assert.equal(calls, 1, "Rejected optional bodies must never reach the backend");
    } finally { globalThis.fetch = original; }
  });
}

test("JSON-consuming routes still reject omitted payloads and wrong media types", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return Response.json({ ok: true }); };
  const routes = [
    { path: "/api/v1/workspace/knowledge", method: "POST" },
    { path: sourcePath, method: "PATCH" },
    { path: "/api/v1/workspace/keys", method: "POST" },
    { path: "/api/v1/workspace/ai/draft", method: "POST" },
    { path: "/mcp", method: "POST" },
  ];
  try {
    for (const route of routes) {
      const headers = { authorization: "Bearer operator-token" };
      assert.equal((await worker.fetch(request(route.path, { method: route.method, headers }), env)).status, 415);
      assert.equal((await worker.fetch(request(route.path, {
        method: route.method, headers: { ...headers, "content-type": "text/plain" }, body: "{}",
      }), env)).status, 415);
      assert.equal((await worker.fetch(request(route.path, {
        method: route.method, headers: { ...headers, "content-type": "application/json" },
      }), env)).status, 400);
    }
    assert.equal(calls, 0);
  } finally { globalThis.fetch = original; }
});

test("AI draft waits can consume the full backend budget while metadata remains bounded", async (t) => {
  const originalFetch = globalThis.fetch;
  const originalTimeout = AbortSignal.timeout;
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let deadline;
  let began;
  const started = () => new Promise((resolve) => { began = resolve; });
  AbortSignal.timeout = (milliseconds) => {
    deadline = milliseconds;
    const controller = new AbortController();
    setTimeout(() => controller.abort(new Error("bounded edge deadline")), milliseconds);
    return controller.signal;
  };
  let upstreamWait = 100000;
  globalThis.fetch = async (_, init) => {
    began();
    return new Promise((resolve, reject) => {
      init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
      setTimeout(() => resolve(Response.json({ draft: "Review-only draft", chats: [] })), upstreamWait);
    });
  };
  try {
    const draftBegan = started();
    const draft = worker.fetch(request("/api/v1/workspace/ai/draft", {
      method: "POST", headers: { authorization: "Bearer operator-token", "content-type": "application/json" },
      body: '{"query":"Support policy?"}',
    }), env);
    await draftBegan;
    assert.equal(deadline, 120000);
    t.mock.timers.tick(100000);
    const response = await draft;
    assert.equal(response.status, 200, "A valid draft within the 100s auth/storage/model budget must reach the operator");
    assert.equal((await response.json()).draft, "Review-only draft");

    const mcpBegan = started();
    const mcpDraft = worker.fetch(request("/mcp", {
      method: "POST", headers: { authorization: "Bearer scoped-key", "content-type": "application/json" },
      body: JSON.stringify({jsonrpc:"2.0",id:1,method:"tools/call",params:{name:"draft_reply",arguments:{query:"Support policy?"}}}),
    }), env);
    await mcpBegan;
    assert.equal(deadline, 120000);
    t.mock.timers.tick(100000);
    assert.equal((await mcpDraft).status, 200, "MCP drafts share the complete model budget");

    upstreamWait = 50000;
    for (const payload of [
      {method:"tools/call",params:{name:"list_accounts"}},
      {method:"tools/list",params:{name:"draft_reply"}},
      null,
    ]) {
      const callBegan = started();
      const call = worker.fetch(request("/mcp", {
        method:"POST", headers:{authorization:"Bearer scoped-key","content-type":"application/json"},
        body:JSON.stringify(payload),
      }), env);
      await callBegan;
      assert.equal(deadline, 45000, "Only the exact MCP draft action receives the larger budget");
      t.mock.timers.tick(50000);
      assert.equal((await call).status, 502);
    }

    upstreamWait = 50000;
    const metadataBegan = started();
    const metadata = worker.fetch(request("/api/v1/workspace/chats", {
      headers: { authorization: "Bearer operator-token" },
    }), env);
    await metadataBegan;
    assert.equal(deadline, 45000);
    t.mock.timers.tick(50000);
    assert.equal((await metadata).status, 502, "A hanging metadata request retains its existing short deadline");
  } finally {
    globalThis.fetch = originalFetch;
    AbortSignal.timeout = originalTimeout;
    t.mock.timers.reset();
  }
});

test('join-request reads reach the account-scoped backend while anonymous and write requests stay blocked',async()=>{
  const original=globalThis.fetch;let seen,calls=0;
  globalThis.fetch=async(url,init)=>{calls++;seen={url:String(url),...init};return Response.json({requests:[],capabilities:{read_supported:false,status:'bot_account_required'}});};
  const path='/api/v1/workspace/join-requests?account_id=account-one&chat_id=-10001&offset=200';
  try{
    assert.equal((await worker.fetch(request(path),env)).status,401);
    for(const method of ['POST','PATCH','DELETE'])assert.equal((await worker.fetch(request(path,{method,headers:{authorization:'Bearer read-token'}}),env)).status,405);
    assert.equal(calls,0);
    const response=await worker.fetch(request(path,{headers:{authorization:'Bearer read-token',cookie:'private'}}),env);
    assert.equal(response.status,200);assert.equal(seen.url,'https://backend.example.test'+path);
    assert.equal(seen.headers.authorization,'Bearer read-token');assert.equal(seen.headers.cookie,undefined);
    assert.equal(response.headers.get('cache-control'),'no-store');assert.equal((await response.json()).capabilities.status,'bot_account_required');
  }finally{globalThis.fetch=original;}
});

test('capabilities and scoped label routes retain bearer authentication and method limits',async()=>{
  const label='/api/v1/workspace/accounts/12345678-1234-1234-1234-123456789abc/label';
  assert.equal((await worker.fetch(request('/api/v1/workspace/capabilities'),env)).status,401);
  assert.equal((await worker.fetch(request(label,{method:'PATCH'}),env)).status,401);
  assert.equal((await worker.fetch(request(label,{method:'DELETE'}),env)).status,405);
  const old=globalThis.fetch;let seen;
  globalThis.fetch=async(url,options)=>{seen={url,options};return Response.json({label:'Operations'});};
  try{
    const response=await worker.fetch(request(label,{method:'PATCH',headers:{authorization:'Bearer scoped-key','content-type':'application/json'},body:JSON.stringify({label:'Operations'})}),env);
    assert.equal(response.status,200);assert.equal(String(seen.url),'https://backend.example.test'+label);
    assert.equal(new Headers(seen.options.headers).get('authorization'),'Bearer scoped-key');
    assert.equal(response.headers.get('cache-control'),'no-store');
  }finally{globalThis.fetch=old;}
});

test("persona routes are proxied (collection and item), not denied as /api/", async () => {
  const personas = "/api/v1/workspace/personas";
  const item = "/api/v1/workspace/personas/12345678-1234-1234-1234-123456789abc";
  // Allowed methods are recognized by the proxy: anonymous → 401 (auth needed),
  // never 403 (which is the /api/ fall-through denial this fix removes).
  assert.equal((await worker.fetch(request(personas), env)).status, 401);          // GET
  assert.equal((await worker.fetch(request(personas, { method: "POST" }), env)).status, 401);
  assert.equal((await worker.fetch(request(item, { method: "PATCH" }), env)).status, 401);
  assert.equal((await worker.fetch(request(item, { method: "DELETE" }), env)).status, 401);
  // Disallowed methods are rejected at the edge with 405, not forwarded.
  assert.equal((await worker.fetch(request(personas, { method: "DELETE" }), env)).status, 405);
  assert.equal((await worker.fetch(request(item, { method: "POST" }), env)).status, 405);
  assert.equal((await worker.fetch(request(item), env)).status, 405);              // GET on item
});

test("persona delete forwards a bodyless action with confirm to the backend", async () => {
  const item = "/api/v1/workspace/personas/12345678-1234-1234-1234-123456789abc?confirm=true";
  const original = globalThis.fetch;
  let seen;
  globalThis.fetch = async (url, init) => { seen = { url: String(url), ...init }; return new Response(null, { status: 204 }); };
  try {
    const result = await worker.fetch(request(item, {
      method: "DELETE", headers: { authorization: "Bearer operator-token", cookie: "session=private" },
    }), { ...env, API_ADMIN_TOKEN: "must-not-be-injected" });
    assert.equal(result.status, 204);
    assert.equal(seen.url, `https://backend.example.test${item}`);
    assert.equal(seen.method, "DELETE");
    assert.equal(seen.body, undefined);
    assert.equal(seen.headers.authorization, "Bearer operator-token");
    assert.equal(seen.headers.cookie, undefined);
  } finally { globalThis.fetch = original; }
});
