import { page } from "./site.js";
import { appHtml } from "./app.js";

// Base security headers shared by all responses.
const BASE_HEADERS = {
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
  "x-frame-options": "DENY",
};

// CSP for the public landing page: self + inline style/script only.
const CSP_LANDING = [
  "default-src 'self'",
  "base-uri 'self'",
  "img-src 'self' data:",
  "style-src 'self' 'unsafe-inline'",
  "script-src 'self' 'unsafe-inline'",
  "connect-src 'self'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");

// CSP for the operator console: also allows the Supabase JS SDK (jsDelivr) and
// XHR/fetch to the Supabase project origin for Auth + PostgREST.
function cspApp(supabaseUrl) {
  const origin = supabaseUrl ? new URL(supabaseUrl).origin : "";
  return [
    "default-src 'self'",
    "base-uri 'self'",
    "img-src 'self' data:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    `connect-src 'self'${origin ? " " + origin : ""}`,
    "font-src 'self'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join("; ");
}

// Only the authenticated workspace surface is proxied. The upstream verifies
// the operator JWT or scoped key; the edge never injects an admin/service key.
function workspaceMethods(path) {
  if (path === "/mcp") return ["POST"];
  const base = "/api/v1/workspace";
  if (["accounts", "chats", "messages", "contacts", "activity"].some((name) => path === `${base}/${name}`)) return ["GET"];
  if (path === `${base}/knowledge`) return ["GET", "POST"];
  if (/^\/api\/v1\/workspace\/knowledge\/[0-9a-f-]{36}$/i.test(path)) return ["PATCH", "DELETE"];
  if (path === `${base}/ai/draft`) return ["POST"];
  if (path === `${base}/keys`) return ["GET", "POST"];
  if (/^\/api\/v1\/workspace\/keys\/[0-9a-f-]{36}\/revoke$/i.test(path)) return ["POST"];
  return null;
}

async function proxyWorkspace(request, env, url, methods) {
  const headers = { ...BASE_HEADERS, "cache-control": "no-store" };
  if (!methods.includes(request.method)) return Response.json({ detail: "method_not_allowed" }, { status: 405, headers: { ...headers, allow: methods.join(", ") } });
  const authorization = request.headers.get("authorization") || "";
  if (!/^Bearer \S+$/.test(authorization)) return Response.json({ detail: "unauthorized" }, { status: 401, headers });
  if (!env.API_BASE_URL) return Response.json({ detail: "backend_not_configured" }, { status: 503, headers });
  const upstream = new URL(env.API_BASE_URL);
  if (upstream.protocol !== "https:" && upstream.hostname !== "localhost" && upstream.hostname !== "127.0.0.1") return Response.json({ detail: "invalid_backend" }, { status: 503, headers });
  upstream.pathname = url.pathname;
  upstream.search = url.search;
  let body;
  if (request.method !== "GET") {
    if (!(request.headers.get("content-type") || "").toLowerCase().startsWith("application/json")) return Response.json({ detail: "json_required" }, { status: 415, headers });
    if (Number(request.headers.get("content-length")) > 1048576) return Response.json({ detail: "request_too_large" }, { status: 413, headers });
    // Bound streamed bodies too, including requests without Content-Length.
    const reader = request.body?.getReader();
    const chunks = []; let size = 0;
    if (reader) {
      while (true) {
        const part = await reader.read(); if (part.done) break;
        size += part.value.byteLength;
        if (size > 1048576) { await reader.cancel(); return Response.json({ detail: "request_too_large" }, { status: 413, headers }); }
        chunks.push(part.value);
      }
    }
    body = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  }
  try {
    const response = await fetch(upstream, {
      method: request.method, body, redirect: "manual", signal: AbortSignal.timeout(45000),
      headers: { authorization, "content-type": "application/json", accept: "application/json" },
    });
    if (response.status >= 300 && response.status < 400) return Response.json({ detail: "unexpected_backend_redirect" }, { status: 502, headers });
    return new Response(response.body, { status: response.status, headers: { ...headers, "content-type": "application/json; charset=utf-8" } });
  } catch {
    return Response.json({ detail: "backend_unavailable" }, { status: 502, headers });
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    const methods = workspaceMethods(url.pathname);
    if (methods) return proxyWorkspace(request, env, url, methods);

    // Public health proxy to the Zeabur API.
    if (url.pathname === "/healthz") {
      const base = env.API_BASE_URL;
      if (!base) {
        return Response.json({ status: "unconfigured", detail: "API_BASE_URL is not set" }, { status: 503 });
      }
      try {
        const upstream = await fetch(`${base.replace(/\/$/, "")}/healthz`, { headers: { accept: "application/json" } });
        return new Response(upstream.body, {
          status: upstream.status,
          headers: { "content-type": "application/json; charset=utf-8" },
        });
      } catch {
        return Response.json({ status: "unavailable", detail: "upstream health check failed" }, { status: 502 });
      }
    }

    // Never expose data API paths from the edge dashboard.
    if (url.pathname.startsWith("/api/")) {
      return new Response("Direct API access is disabled", { status: 403 });
    }

    // Operator console (login-gated in the browser via Supabase Auth).
    if (url.pathname === "/app" || url.pathname.startsWith("/app/")) {
      const supabaseUrl = env.SUPABASE_URL || "";
      const supabaseKey = env.SUPABASE_PUBLISHABLE_KEY || "";
      const html = appHtml
        .replaceAll("%SUPABASE_URL%", supabaseUrl)
        .replaceAll("%SUPABASE_KEY%", supabaseKey)
        .replaceAll("%OPEN_CONNECT_URL%", env.OPEN_CONNECT_URL || "");
      return new Response(html, {
        headers: {
          "content-type": "text/html; charset=utf-8",
          "cache-control": "no-store",
          "content-security-policy": cspApp(supabaseUrl),
          ...BASE_HEADERS,
        },
      });
    }

    // Public landing page.
    return new Response(page, {
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "public, max-age=300",
        "content-security-policy": CSP_LANDING,
        ...BASE_HEADERS,
      },
    });
  },
};
