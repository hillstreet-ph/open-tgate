const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Open-TGate</title><style>body{font:16px system-ui;margin:0;background:#090b12;color:#edf1ff}main{max-width:900px;margin:64px auto;padding:24px}.card{background:#121725;border:1px solid #29324b;border-radius:16px;padding:24px}h1{color:#b69cff}.ok{color:#69e6a6}.warn{color:#ffd166}</style></head><body><main><h1>Open-TGate</h1><div class="card"><h2>Private Telegram Operations</h2><p id="status">Checking API…</p><p>Outbound Telegram sending is disabled until operator approval controls pass verification.</p></div></main><script>fetch('/healthz').then(r=>r.json()).then(()=>{status.textContent='API reachable';status.className='ok'}).catch(()=>{status.textContent='API unavailable';status.className='warn'})</script></body></html>`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/healthz") return fetch(`${env.API_BASE_URL}/healthz`);
    if (url.pathname.startsWith("/api/")) return new Response("Direct API access is disabled", { status: 403 });
    return new Response(html, { headers: { "content-type": "text/html; charset=utf-8", "content-security-policy": "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'" } });
  },
};

