// Contract tests for the operator console served at /app.
//
// These assert the multi-account navigation and login affordances the console
// depends on, and that the Worker substitutes the public config placeholders
// (including OPEN_CONNECT_URL) instead of leaking them into the response.
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

import worker from "../src/index.js";
import { appHtml } from "../src/app.js";

const ENV = {
  SUPABASE_URL: "https://demo.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_demo",
  OPEN_CONNECT_URL: "https://open-connect.site/connections",
  API_BASE_URL: "https://api.example.test",
};

async function getApp(env) {
  const res = await worker.fetch(new Request("https://console.test/app"), env);
  return { res, html: await res.text() };
}

test("/app renders the console with every placeholder substituted", async () => {
  const { res, html } = await getApp(ENV);
  assert.equal(res.status, 200);
  assert.match(res.headers.get("content-type"), /text\/html/);
  assert.ok(!html.includes("%SUPABASE_URL%"), "SUPABASE_URL placeholder must be replaced");
  assert.ok(!html.includes("%SUPABASE_KEY%"), "SUPABASE_KEY placeholder must be replaced");
  assert.ok(!html.includes("%OPEN_CONNECT_URL%"), "OPEN_CONNECT_URL placeholder must be replaced");
  assert.ok(html.includes(ENV.SUPABASE_URL));
  assert.ok(html.includes(ENV.OPEN_CONNECT_URL));
});

test("/app hides the Open-Connect link when OPEN_CONNECT_URL is unset", async () => {
  const { html } = await getApp({ ...ENV, OPEN_CONNECT_URL: undefined });
  assert.ok(!html.includes("%OPEN_CONNECT_URL%"));
  assert.ok(!html.includes("open-connect.site"));
});

test("console exposes the multi-account navigation hooks", () => {
  for (const hook of [
    'id="menu-toggle"',
    'id="sidebar"',
    'id="sb-overlay"',
    'id="back-btn"',
    'id="sb-acct-list"',
    'id="sb-add-personal"',
    'id="sb-add-bot"',
    'id="sb-open-connect"',
  ]) {
    assert.ok(appHtml.includes(hook), `missing ${hook}`);
  }
});

test("back button and sidebar wire browser history and mobile state", () => {
  assert.ok(appHtml.includes('addEventListener("popstate"'));
  assert.ok(appHtml.includes("history.pushState"));
  assert.ok(appHtml.includes("history.replaceState"));
  assert.ok(appHtml.includes("#account="), "deep-link hash format is required");
  assert.ok(/\.back-btn\.visible\{display:grid\}/.test(appHtml), "back button must be visible on mobile too");
});

test("login actions cover personal and bot accounts for multiple accounts", () => {
  assert.ok(appHtml.includes('data-act="phone-open"'));
  assert.ok(appHtml.includes('data-act="qr"'));
  assert.ok(appHtml.includes('"start_bot_token"'), "bot token login action");
  assert.ok(appHtml.includes('"revoke_bot"'), "bot revoke action");
  assert.ok(appHtml.includes('account_type:"user"'), "personal accounts use the 'user' vocabulary");
  assert.ok(appHtml.includes("resetAccount"), "stuck logins can be reset without duplicates");
});

test("console never bundles a secret key", () => {
  assert.ok(!/sb_secret_|service_role|SUPABASE_SECRET_KEY/.test(appHtml));
});

test("inline console script is syntactically valid", () => {
  const scripts = [...appHtml.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  assert.ok(scripts.length > 0, "expected at least one inline script");
  scripts.forEach((src, i) => new vm.Script(src, { filename: `app-inline-${i}.js` }));
});

test("/healthz reports unconfigured without API_BASE_URL", async () => {
  const res = await worker.fetch(new Request("https://console.test/healthz"), {});
  assert.equal(res.status, 503);
});

test("/api/ is not reachable through the dashboard", async () => {
  const res = await worker.fetch(new Request("https://console.test/api/anything"), ENV);
  assert.equal(res.status, 403);
});
