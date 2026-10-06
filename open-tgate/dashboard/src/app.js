// Open-TGate operator console (/app).
//
// ChatGPT-style layout: left sidebar lists connected accounts, right pane
// shows detail for the selected account. Auth is Supabase Auth (email magic
// link, password, Google, GitHub) using the project's PUBLISHABLE key.
//
// %SUPABASE_URL% and %SUPABASE_KEY% are replaced by the Worker from its vars.

export const appHtml = `<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="color-scheme" content="dark light" />
<title>Open-TGate — Operator Console</title>
<meta name="robots" content="noindex, nofollow" />
<style>
  :root{
    --bg:#080a11; --card:#121727; --card2:#161c2f; --line:#242c44; --line2:#1b2236;
    --fg:#eef2ff; --muted:#9aa6c6; --faint:#6c789c;
    --brand:#b69cff; --brand2:#7c74ff; --accent:#5ad1ff;
    --ok:#5fe3a1; --warn:#ffd166; --bad:#ff7a8a; --radius:16px;
    --shadow:0 20px 50px -24px rgba(0,0,0,.75);
    --sidebar-w:280px;
  }
  html[data-theme="light"]{
    --bg:#f6f8ff; --card:#fff; --card2:#f4f6ff; --line:#e2e7f5; --line2:#edf0fb;
    --fg:#101528; --muted:#4d5981; --faint:#7683a8; --brand:#6b4df6; --accent:#0aa5da;
    --shadow:0 20px 50px -30px rgba(30,40,90,.35);
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    color:var(--fg);background:var(--bg);-webkit-font-smoothing:antialiased;height:100vh;overflow:hidden}
  a{color:var(--accent);text-decoration:none}

  /* ---- Layout shell (sidebar + pane) ---- */
  .shell{display:flex;height:100vh}

  /* Sidebar */
  .sidebar{width:var(--sidebar-w);min-width:var(--sidebar-w);height:100vh;display:flex;flex-direction:column;
    background:var(--card);border-right:1px solid var(--line);overflow:hidden;transition:transform .25s ease}
  .sb-head{padding:14px 16px 10px;border-bottom:1px solid var(--line2);display:flex;align-items:center;gap:10px}
  .sb-brand{display:flex;align-items:center;gap:8px;font-weight:800;font-size:15px;color:inherit;flex:1;min-width:0}
  .sb-logo{width:26px;height:26px;border-radius:7px;background:linear-gradient(135deg,var(--brand2),var(--accent));
    display:grid;place-items:center;color:#0a0c16;font-weight:900;font-size:12px;flex-shrink:0}
  .sb-actions{display:flex;gap:6px}
  .sb-btn{width:32px;height:32px;border-radius:8px;border:1px solid var(--line);background:transparent;
    color:var(--fg);cursor:pointer;display:grid;place-items:center;font-size:14px;transition:.15s}
  .sb-btn:hover{border-color:var(--brand);background:var(--card2)}

  .sb-section{padding:8px 12px 4px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--faint)}
  .sb-list{flex:1;overflow-y:auto;padding:4px 0}
  .sb-item{display:flex;align-items:center;gap:10px;padding:10px 16px;cursor:pointer;
    border-radius:0;border-left:3px solid transparent;transition:.12s;font-size:14px}
  .sb-item:hover{background:var(--card2)}
  .sb-item.active{background:color-mix(in srgb,var(--brand) 12%,transparent);border-left-color:var(--brand)}
  .sb-avatar{width:34px;height:34px;border-radius:50%;flex-shrink:0;display:grid;place-items:center;
    font-weight:700;font-size:14px;color:#fff}
  .sb-avatar.personal{background:linear-gradient(135deg,var(--brand2),var(--accent))}
  .sb-avatar.bot{background:linear-gradient(135deg,#ff7a8a,var(--warn))}
  .sb-info{flex:1;min-width:0}
  .sb-name{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:14px}
  .sb-status{font-size:12px;display:flex;align-items:center;gap:5px}
  .sb-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
  .sb-dot.ok{background:var(--ok)} .sb-dot.warn{background:var(--warn)} .sb-dot.bad{background:var(--bad)} .sb-dot.off{background:var(--faint)}

  .sb-foot{padding:10px 12px;border-top:1px solid var(--line2);display:flex;flex-direction:column;gap:6px}
  .sb-add{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:10px;border:1px dashed var(--line);
    background:transparent;color:var(--muted);cursor:pointer;font-size:13px;font-weight:600;transition:.15s;width:100%}
  .sb-add:hover{border-color:var(--brand);color:var(--fg);background:var(--card2)}
  .sb-user{font-size:12px;color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 4px}

  /* Main pane */
  .pane{flex:1;height:100vh;overflow-y:auto;display:flex;flex-direction:column}
  .pane-head{padding:16px 24px;border-bottom:1px solid var(--line2);display:flex;align-items:center;gap:12px;
    background:color-mix(in srgb,var(--bg) 90%,transparent);backdrop-filter:blur(10px);position:sticky;top:0;z-index:5}
  .pane-title{font-weight:700;font-size:17px;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .pane-body{flex:1;padding:24px;overflow-y:auto}

  /* Shared components */
  .btn{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);background:var(--card);
    color:var(--fg);font-weight:600;padding:9px 15px;border-radius:10px;cursor:pointer;font-size:13.5px;transition:.15s}
  .btn:hover{border-color:var(--brand);transform:translateY(-1px)}
  .btn.primary{background:linear-gradient(135deg,var(--brand2),var(--brand));border-color:transparent;color:#0a0c16}
  html[data-theme="light"] .btn.primary{color:#fff}
  .btn[disabled]{opacity:.55;cursor:default;transform:none}
  .btn.sm{padding:6px 12px;font-size:12.5px;border-radius:8px}
  .btn.danger{border-color:color-mix(in srgb,var(--bad) 40%,transparent);color:var(--bad)}
  .btn.danger:hover{background:color-mix(in srgb,var(--bad) 12%,transparent)}
  .card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:22px;box-shadow:var(--shadow)}
  h2{font-size:17px;font-weight:700;margin:0 0 4px}
  .sub{color:var(--muted);margin:0 0 16px;font-size:14px}
  label{display:block;font-size:12.5px;color:var(--muted);margin:0 0 5px;font-weight:600}
  input[type=email],input[type=password],input[type=text],input[type=tel]{width:100%;padding:10px 13px;border-radius:10px;
    border:1px solid var(--line);background:var(--bg);color:var(--fg);font-size:14px}
  input:focus{outline:none;border-color:var(--brand)}
  .row{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
  .msg{margin-top:12px;font-size:13px;padding:9px 12px;border-radius:9px;border:1px solid var(--line2);display:none}
  .msg.show{display:block}
  .msg.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,transparent)}
  .msg.err{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,transparent)}
  .msg.info{color:var(--muted)}
  .hidden{display:none!important}
  .pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:var(--muted);
    border:1px solid var(--line);background:var(--card2);padding:4px 10px;border-radius:999px}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--faint)}
  .dot.ok{background:var(--ok)} .dot.warn{background:var(--warn)} .dot.bad{background:var(--bad)}

  /* Profile strip */
  .profile{display:flex;align-items:center;gap:14px;padding:14px 16px;
    background:var(--card2);border:1px solid var(--line2);border-radius:12px}
  .profile-avatar{width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg,var(--brand2),var(--accent));
    display:grid;place-items:center;color:#fff;font-weight:800;font-size:18px;flex-shrink:0}
  .profile-info{flex:1;min-width:0}
  .profile-name{font-weight:700;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .profile-user{font-size:13px;color:var(--accent)}
  .profile-phone{font-size:12.5px;color:var(--faint);font-family:ui-monospace,Menlo,Consolas,monospace}

  /* Entity counts chips */
  .entity-counts{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 0}
  .entity-chip{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;
    border:1px solid var(--line2);background:var(--card2);padding:5px 12px;border-radius:999px;color:var(--muted)}
  .entity-chip b{color:var(--fg)}
  .entity-chip .icon{font-size:14px;line-height:1}

  /* Sync progress stepper */
  .sync-stepper{display:flex;align-items:center;gap:0;margin:14px 0 6px;overflow-x:auto}
  .sync-step{display:flex;align-items:center;gap:5px;font-size:12px;font-weight:600;color:var(--faint);
    padding:5px 10px;border-radius:7px;white-space:nowrap;transition:.2s}
  .sync-step.active{color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,transparent)}
  .sync-step.done{color:var(--ok)}
  .sync-step .check{font-size:12px}
  .sync-arrow{color:var(--line);font-size:10px;margin:0 2px}

  /* Entity browser */
  .entity-browser{margin:14px 0 0;border:1px solid var(--line2);border-radius:11px;overflow:hidden}
  .entity-tabs{display:flex;gap:0;border-bottom:1px solid var(--line2);overflow-x:auto;background:var(--bg)}
  .entity-tab{padding:7px 13px;font-size:12px;font-weight:600;color:var(--faint);cursor:pointer;
    border:none;background:transparent;border-bottom:2px solid transparent;transition:.15s;white-space:nowrap}
  .entity-tab:hover{color:var(--fg)}
  .entity-tab.active{color:var(--accent);border-bottom-color:var(--accent)}
  .entity-list{max-height:360px;overflow-y:auto;padding:0}
  .entity-row{display:flex;align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid var(--line2);font-size:13px}
  .entity-row:last-child{border-bottom:none}
  .entity-row .ename{font-weight:600;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .entity-row .euser{color:var(--accent);font-size:12px;flex-shrink:0}
  .entity-row .emeta{color:var(--faint);font-size:11.5px;flex-shrink:0}

  .step{margin-top:14px;border-top:1px solid var(--line2);padding-top:14px}
  .qr{display:inline-block;background:#fff;padding:12px;border-radius:12px;margin-top:10px}
  .qr img{display:block;width:200px;height:200px;image-rendering:pixelated}
  .hint{font-size:13px;color:var(--muted);margin:6px 0 0}

  /* Label editing */
  .label-edit{display:flex;align-items:center;gap:8px}
  .label-text{font-weight:700;font-size:16px;cursor:pointer;padding:2px 6px;border-radius:6px;border:1px solid transparent;transition:.15s}
  .label-text:hover{border-color:var(--line);background:var(--card2)}
  .label-input{font-size:15px;font-weight:600;padding:4px 8px;border-radius:6px;border:1px solid var(--brand);
    background:var(--bg);color:var(--fg);width:200px}

  /* Section header */
  .section{margin-bottom:20px}
  .section-title{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--faint);margin:0 0 10px}

  /* Stats grid */
  .stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:0 0 20px}
  .stat{background:var(--card2);border:1px solid var(--line2);border-radius:12px;padding:14px}
  .stat .k{font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.08em}
  .stat .v{font-size:20px;font-weight:800;margin-top:3px}

  /* Login panel */
  .login-panel{max-width:440px;margin:8vh auto 0}
  .login-panel h1{font-size:24px;font-weight:800;letter-spacing:-.4px;margin:0 0 4px}

  /* Responsive */
  .menu-toggle,.back-btn{display:none}
  @media(max-width:900px){
    .sidebar{position:fixed;left:0;top:0;z-index:20;transform:translateX(-100%);width:min(86vw,320px);min-width:0}
    .pane{width:100%;min-width:0}
    .pane-head{padding:10px 12px;gap:8px}
    .pane-body{padding:16px;width:100%;overflow-x:hidden}
    .pane-title{font-size:15px}
    .nav-right .btn{padding:7px 9px}
    .back-btn.visible{display:grid;place-items:center;width:36px;height:36px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer;font-size:18px}
    .card{padding:16px}
    .profile{align-items:flex-start}
    .qr{display:block;width:min(100%,280px);margin:12px auto 0}
    .qr img{width:100%;height:auto}
    .row.login-options{display:grid;grid-template-columns:1fr;gap:10px}
    .row.login-options .btn{width:100%;justify-content:center;min-height:44px}
    input[type=email],input[type=password],input[type=text],input[type=tel]{font-size:16px;min-height:44px}
    .sidebar.open{transform:translateX(0)}
    .menu-toggle{display:grid;place-items:center;width:36px;height:36px;border-radius:8px;
      border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer;font-size:16px}
    .stats{grid-template-columns:1fr}
    .sb-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:19}
    .sb-overlay.open{display:block}
  }
  @media(max-width:480px){
    .pane-body{padding:12px}
    .stats{grid-template-columns:1fr}
    .pane-head{padding:8px 10px}
    .nav-right #refresh-btn{font-size:0}
    .nav-right #refresh-btn::before{content:"↻";font-size:18px}
  }
</style>
</head>
<body>
<div class="sb-overlay" id="sb-overlay"></div>
<div class="shell">
  <!-- ===== LEFT SIDEBAR ===== -->
  <aside class="sidebar" id="sidebar">
    <div class="sb-head">
      <a class="sb-brand" href="/"><span class="sb-logo">t</span>Open-TGate</a>
      <div class="sb-actions">
        <button class="sb-btn" id="theme" title="Toggle theme">◐</button>
      </div>
    </div>
    <div class="sb-section">Accounts</div>
    <div class="sb-list" id="sb-acct-list"></div>
    <div class="sb-foot">
      <button class="sb-add" id="sb-add-personal" title="Connect personal account">+ Personal account</button>
      <button class="sb-add" id="sb-add-bot" title="Add Telegram bot">+ Bot token</button>
      <div class="sb-user" id="sb-user"></div>
      <button class="btn sm" id="signout" style="width:100%;justify-content:center">Sign out</button>
    </div>
  </aside>

  <!-- ===== RIGHT PANE ===== -->
  <div class="pane" id="pane">
    <div class="pane-head">
      <button class="menu-toggle" id="menu-toggle" title="Accounts" aria-label="Open accounts">☰</button>
      <button class="back-btn" id="back-btn" title="Back" aria-label="Back">←</button>
      <span class="pane-title" id="pane-title">Open-TGate</span>
      <div class="nav-right" style="margin-left:auto;display:flex;gap:8px">
        <button class="btn sm" id="refresh-btn" title="Refresh">↻ Refresh</button>
      </div>
    </div>
    <div class="pane-body" id="pane-body">
      <!-- Pane content rendered by JS -->
    </div>
  </div>
</div>

<!-- Views rendered off-screen, switched by JS -->
<template id="tmpl-loading"><div class="card" style="max-width:460px;margin:8vh auto"><p style="color:var(--muted)">Loading console…</p></div></template>

<template id="tmpl-login">
<div class="login-panel">
  <div class="card">
    <h1 id="login-title">Operator sign in</h1>
    <p class="sub">Sign in with your authorized email. Password, magic link, Google, and GitHub are supported.</p>
    <div class="row" style="margin:0 0 14px;gap:8px">
      <button class="btn" id="oauth-google" style="flex:1;justify-content:center;font-size:13px">Continue with Google</button>
      <button class="btn" id="oauth-github" style="flex:1;justify-content:center;font-size:13px">Continue with GitHub</button>
    </div>
    <div style="display:flex;align-items:center;gap:10px;color:var(--faint);font-size:11px;margin:0 0 12px">
      <span style="flex:1;height:1px;background:var(--line2)"></span>OR<span style="flex:1;height:1px;background:var(--line2)"></span>
    </div>
    <form id="login-form" autocomplete="on">
      <label for="email">Email address</label>
      <input id="email" type="email" autocomplete="email" placeholder="you@example.com" required />
      <label for="password" style="margin-top:10px">Password</label>
      <input id="password" type="password" autocomplete="current-password" placeholder="Your password" />
      <div class="row">
        <button class="btn primary" id="btn-password" type="submit" style="flex:1;justify-content:center">Sign in</button>
        <button class="btn" id="btn-magic" type="button" style="flex:1;justify-content:center">Email me a link</button>
      </div>
    </form>
    <div class="row" style="margin-top:8px">
      <button class="btn" id="btn-reset" type="button" style="flex:1;justify-content:center;font-size:12.5px">Set / reset password</button>
    </div>
    <div class="msg" id="login-msg"></div>
    <footer style="color:var(--faint);font-size:12px;margin-top:14px">Access restricted to allowlisted operators. Sessions handled by Supabase Auth.</footer>
  </div>
</div>
</template>

<template id="tmpl-recovery">
<div class="login-panel">
  <div class="card">
    <h1>Set your password</h1>
    <p class="sub">Choose a password for your operator account. You reached this screen from a verified email link.</p>
    <form id="recovery-form">
      <label for="new-password">New password</label>
      <input id="new-password" type="password" autocomplete="new-password" placeholder="At least 8 characters" required />
      <div class="row"><button class="btn primary" type="submit" style="flex:1;justify-content:center">Save password</button></div>
    </form>
    <div class="msg" id="recovery-msg"></div>
  </div>
</div>
</template>

<template id="tmpl-denied">
<div class="login-panel">
  <div class="card">
    <h1>Not authorized</h1>
    <p class="sub" id="denied-sub">This account is signed in but is not an Open-TGate operator.</p>
    <button class="btn" id="denied-signout">Sign out</button>
  </div>
</div>
</template>

<template id="tmpl-add-personal">
<div class="card" style="max-width:460px;margin:0 auto">
  <h2>Connect personal account</h2>
  <p class="sub">Connect a Telegram account via phone or QR code. Sync is read-only and ban-safe.</p>
  <label for="add-label">Account label</label>
  <input id="add-label" type="text" placeholder="e.g. Support line" maxlength="80" autocomplete="off" />
  <div class="row">
    <button class="btn primary" id="add-create" style="flex:1;justify-content:center">Create</button>
    <button class="btn" id="add-cancel" style="flex:1;justify-content:center">Cancel</button>
  </div>
  <div class="msg" id="add-msg"></div>
</div>
</template>

<template id="tmpl-add-bot">
<div class="card" style="max-width:460px;margin:0 auto">
  <h2>Add Telegram bot</h2>
  <p class="sub">Add a bot via its Bot API token. The token is used once and never stored.</p>
  <label for="bot-label">Bot label</label>
  <input id="bot-label" type="text" placeholder="e.g. Notification bot" maxlength="80" autocomplete="off" />
  <label for="bot-token" style="margin-top:10px">Bot token</label>
  <input id="bot-token" type="password" placeholder="123456:ABC-DEF1234..." autocomplete="off" />
  <p class="hint">Get this from @BotFather in Telegram. The full token is used to validate the bot and then discarded — only the last 4 chars are kept for identification.</p>
  <div class="row">
    <button class="btn primary" id="bot-create" style="flex:1;justify-content:center">Add bot</button>
    <button class="btn" id="bot-cancel" style="flex:1;justify-content:center">Cancel</button>
  </div>
  <div class="msg" id="bot-msg"></div>
</div>
</template>

<template id="tmpl-dashboard">
<div class="stats">
  <div class="stat"><div class="k">Workers</div><div class="v" id="stat-workers">—</div></div>
  <div class="stat"><div class="k">Live (≤2 min)</div><div class="v" id="stat-live">—</div></div>
  <div class="stat"><div class="k">Accounts</div><div class="v" id="stat-accounts">—</div></div>
</div>
<div class="card" style="margin-bottom:16px">
  <h2>Worker heartbeats</h2>
  <p class="sub">Live from Supabase (RLS-restricted to operators).</p>
  <div id="hb-wrap">
    <table id="hb-table" class="hidden" style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr>
        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--line2);color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.06em">Worker</th>
        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--line2);color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.06em">Service</th>
        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--line2);color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.06em">Status</th>
        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--line2);color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.06em">Last seen</th>
      </tr></thead>
      <tbody id="hb-body"></tbody>
    </table>
    <div class="empty" id="hb-empty" style="color:var(--muted);padding:16px;text-align:center;font-size:13px">
      No heartbeats yet — worker has not reported in.</div>
  </div>
</div>
<p style="color:var(--faint);font-size:12px">Outbound Telegram sending disabled until operator approval controls pass verification. Console performs read-only operations.</p>
</template>

<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.58.0/dist/umd/supabase.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js"></script>
<script>
(function(){
  var CFG = { url: "%SUPABASE_URL%", key: "%SUPABASE_KEY%" };
  var root = document.documentElement, TKEY = "otg-theme";
  try{ var t = localStorage.getItem(TKEY); if(t) root.setAttribute("data-theme", t); }catch(e){}
  document.getElementById("theme").addEventListener("click", function(){
    var n = root.getAttribute("data-theme")==="dark" ? "light":"dark";
    root.setAttribute("data-theme", n); try{ localStorage.setItem(TKEY, n); }catch(e){}
  });

  // Helpers
  function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,function(c){
    return ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]; }); }
  function msg(id,text,kind){ var el=document.getElementById(id);
    if(!el) return; el.textContent=text; el.className="msg show "+(kind||"info"); }
  function clearMsg(id){ var el=document.getElementById(id); if(!el) return; el.className="msg"; el.textContent=""; }
  function $(id){ return document.getElementById(id); }

  // Template renderer
  function renderTemplate(tmplId){
    var tmpl = document.getElementById(tmplId);
    if(!tmpl) return "";
    return tmpl.innerHTML;
  }

  // Pane management
  var currentPane = null;
  function showPane(name, html, title){
    var body = $("pane-body");
    body.innerHTML = html || renderTemplate("tmpl-"+name);
    $("pane-title").textContent = title || "Open-TGate";
    currentPane = name;
    var back = $("back-btn");
    if(back) back.classList.toggle("visible", name==="account-detail" || name==="add-personal" || name==="add-bot");
  }

  // Mobile sidebar toggle
  var sidebar = $("sidebar"), overlay = $("sb-overlay");
  $("menu-toggle").addEventListener("click", function(){ sidebar.classList.toggle("open"); overlay.classList.toggle("open"); });
  overlay.addEventListener("click", function(){ sidebar.classList.remove("open"); overlay.classList.remove("open"); });
  $("back-btn").addEventListener("click", function(){ showDashboard(); closeMobile(); });
  function closeMobile(){ sidebar.classList.remove("open"); overlay.classList.remove("open"); }

  // Init check
  if(!window.supabase || CFG.url.indexOf("%SUPABASE")===0){
    showPane("login"); msg("login-msg","Console is not configured (SUPABASE_URL / key missing).","err");
    return;
  }
  var sb = window.supabase.createClient(CFG.url, CFG.key, { auth:{ persistSession:true, autoRefreshToken:true, detectSessionInUrl:true } });
  var REDIRECT = window.location.origin + "/app";

  // State
  var recovering = false;
  var accounts = [];
  var selectedId = null;
  var tgTimer = null;
  var openTabs = {};

  // Constants
  var STATUS_LABEL = {
    pending:"Not connected", initializing:"Starting…", awaiting_phone:"Enter phone number",
    awaiting_qr_scan:"Scan QR code", awaiting_code:"Enter login code",
    awaiting_password:"Enter 2FA password", authorized:"Connected", logged_out:"Signed out", error:"Needs attention",
    validating_token:"Validating…", bot_authorized:"Active"
  };
  var ENTITY_ICONS = { contact:"👤", user:"👤", group:"👥", channel:"📢", bot:"🤖", file:"📎" };
  var ENTITY_LABELS = { contact:"Contacts", user:"Users", group:"Groups", channel:"Channels", bot:"Bots", file:"Files" };
  var SYNC_STEPS = ["profile","chats","archived","contacts","complete"];
  var SYNC_LABELS = { profile:"Profile", chats:"Chats", archived:"Archive", contacts:"Contacts", complete:"Done" };

  // ---- Auth handlers ----
  function bindLoginEvents(){
    var form = $("login-form"); if(!form) return;
    form.addEventListener("submit", async function(e){
      e.preventDefault();
      var email = $("email").value.trim(), password = $("password").value;
      if(!email){ msg("login-msg","Enter your email.","err"); return; }
      if(!password){ msg("login-msg","Enter your password, or use the other options.","err"); return; }
      var btn = $("btn-password"); btn.disabled=true; clearMsg("login-msg"); msg("login-msg","Signing in…","info");
      var r = await sb.auth.signInWithPassword({ email: email, password: password });
      btn.disabled=false;
      if(r.error){ msg("login-msg", (r.error.message||"Sign-in failed")+". First time? Use magic link or Google/GitHub.","err"); }
    });
    $("btn-magic").addEventListener("click", async function(){
      var email = $("email").value.trim();
      if(!email){ msg("login-msg","Enter your email first.","err"); return; }
      clearMsg("login-msg"); msg("login-msg","Sending magic link…","info");
      var r = await sb.auth.signInWithOtp({ email: email, options:{ emailRedirectTo: REDIRECT } });
      if(r.error){ msg("login-msg", r.error.message||"Could not send link.","err"); }
      else{ msg("login-msg","Check your inbox for a secure sign-in link.","ok"); }
    });
    $("btn-reset").addEventListener("click", async function(){
      var email = $("email").value.trim();
      if(!email){ msg("login-msg","Enter your email first.","err"); return; }
      clearMsg("login-msg"); msg("login-msg","Sending password-set link…","info");
      var r = await sb.auth.resetPasswordForEmail(email, { redirectTo: REDIRECT });
      if(r.error){ msg("login-msg", r.error.message||"Could not send.","err"); }
      else{ msg("login-msg","If an account exists, a set-password link was sent.","ok"); }
    });
    var ggl = $("oauth-google"); if(ggl) ggl.addEventListener("click",function(){ oauth("google"); });
    var gh = $("oauth-github"); if(gh) gh.addEventListener("click",function(){ oauth("github"); });
  }
  function bindRecoveryEvents(){
    var form = $("recovery-form"); if(!form) return;
    form.addEventListener("submit", async function(e){
      e.preventDefault();
      var np = $("new-password").value;
      if(!np || np.length < 8){ msg("recovery-msg","Password must be at least 8 characters.","err"); return; }
      msg("recovery-msg","Saving…","info");
      var r = await sb.auth.updateUser({ password: np });
      if(r.error){ msg("recovery-msg", r.error.message||"Could not set.","err"); return; }
      recovering = false;
      var s = await sb.auth.getSession();
      renderFor(s.data.session);
    });
  }
  async function oauth(provider){
    msg("login-msg","Redirecting to "+provider+"…","info");
    var r = await sb.auth.signInWithOAuth({ provider: provider, options:{ redirectTo: REDIRECT } });
    if(r.error){ msg("login-msg", (r.error.message||"OAuth failed")+" — provider may not be enabled.","err"); }
  }

  // ---- Sign out ----
  async function doSignOut(){ if(tgTimer){ clearInterval(tgTimer); tgTimer=null; } await sb.auth.signOut(); location.replace("/app"); }

  // ---- Main render ----
  async function renderFor(session){
    if(recovering){
      sidebar.classList.add("hidden");
      showPane("recovery","",null); bindRecoveryEvents(); return;
    }
    if(!session){
      sidebar.classList.add("hidden");
      showPane("login","",null); bindLoginEvents(); return;
    }
    var email = (session.user && session.user.email) || "";
    var op = await sb.from("open_tgate_operators").select("email,role,is_active").limit(1);
    if(op.error || !op.data || !op.data[0] || !op.data[0].is_active){
      sidebar.classList.add("hidden");
      showPane("denied","",null);
      var sub = $("denied-sub");
      if(sub) sub.textContent = "Signed in as "+email+", but not an active operator.";
      var dso = $("denied-signout"); if(dso) dso.addEventListener("click", doSignOut);
      return;
    }
    // Authenticated operator
    sidebar.classList.remove("hidden");
    $("sb-user").textContent = email;
    $("signout").addEventListener("click", doSignOut);
    showDashboard();
    startPolling();
  }

  // ---- Dashboard (home) ----
  function showDashboard(){
    selectedId = null;
    showPane("dashboard","","Open-TGate");
    updateSidebar();
    loadHeartbeats();
    bindDashboardEvents();
  }
  function bindDashboardEvents(){
    var rb = $("refresh-btn"); if(rb) rb.onclick = function(){ loadHeartbeats(); refreshAccounts(); };
  }
  async function loadHeartbeats(){
    var r = await sb.from("open_tgate_worker_heartbeats")
      .select("worker_id,service,status,last_seen_at,metadata")
      .order("last_seen_at",{ascending:false}).limit(200);
    if(r.error) return;
    var rows = r.data || [];
    var body = $("hb-body"); if(!body) return;
    body.innerHTML="";
    var now = Date.now(), live = 0;
    rows.forEach(function(h){
      var seen = h.last_seen_at ? new Date(h.last_seen_at) : null;
      var isLive = seen && (now - seen.getTime() < 120000); if(isLive) live++;
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td style="padding:8px;border-bottom:1px solid var(--line2);font-family:ui-monospace,Menlo,monospace;font-size:12px">'+esc(h.worker_id)+'</td>'+
        '<td style="padding:8px;border-bottom:1px solid var(--line2)">'+esc(h.service||"")+'</td>'+
        '<td style="padding:8px;border-bottom:1px solid var(--line2)"><span class="dot '+(isLive?"ok":"warn")+'" style="display:inline-block;margin-right:6px"></span>'+esc(h.status||"")+'</td>'+
        '<td style="padding:8px;border-bottom:1px solid var(--line2)">'+(seen?seen.toLocaleString():"—")+'</td>';
      body.appendChild(tr);
    });
    var sw = $("stat-workers"); if(sw) sw.textContent = rows.length;
    var sl = $("stat-live"); if(sl) sl.textContent = live;
    var sa = $("stat-accounts"); if(sa) sa.textContent = accounts.length;
    var ht = $("hb-table"); if(ht) ht.classList.toggle("hidden", rows.length===0);
    var he = $("hb-empty"); if(he) he.classList.toggle("hidden", rows.length>0);
  }

  // ---- Sidebar management ----
  function statusDot(s){
    if(s==="authorized"||s==="bot_authorized") return "ok";
    if(s==="error"||s==="logged_out") return "bad";
    return "warn";
  }
  function sidebarLabel(acc){
    if(acc.status==="authorized"||acc.status==="bot_authorized"){
      if(acc.tg_username) return "@"+acc.tg_username;
      return STATUS_LABEL[acc.status]||acc.status;
    }
    return STATUS_LABEL[acc.status]||acc.status;
  }
  function updateSidebar(){
    var list = $("sb-acct-list");
    if(!list) return;
    if(accounts.length===0){
      list.innerHTML = '<div style="padding:16px;color:var(--faint);font-size:13px;text-align:center">No accounts yet</div>';
      return;
    }
    var html = '';
    for(var i=0;i<accounts.length;i++){
      var a = accounts[i];
      var isBot = a.account_type === "bot";
      var initials = isBot ? "🤖" : ((a.tg_first_name||a.label||"?")[0]||"?").toUpperCase();
      var active = a.id === selectedId ? " active" : "";
      html += '<div class="sb-item'+active+'" data-acct="'+a.id+'">';
      html += '<div class="sb-avatar '+(isBot?"bot":"personal")+'">'+esc(initials)+'</div>';
      html += '<div class="sb-info">';
      html += '<div class="sb-name">'+esc(a.label)+'</div>';
      html += '<div class="sb-status"><span class="sb-dot '+statusDot(a.status)+'"></span>'+esc(sidebarLabel(a))+'</div>';
      html += '</div></div>';
    }
    list.innerHTML = html;
    // Bind clicks
    list.querySelectorAll("[data-acct]").forEach(function(el){
      el.addEventListener("click", function(){
        var id = el.getAttribute("data-acct");
        selectAccount(id);
        closeMobile();
      });
    });
  }

  // ---- Add account forms ----
  function bindAddPersonalForm(){
    $("sb-add-personal").addEventListener("click", function(){
      showPane("add-personal","","Connect account");
      bindAddPersonalEvents();
    });
  }
  function bindAddBotForm(){
    $("sb-add-bot").addEventListener("click", function(){
      showPane("add-bot","","Add bot");
      bindAddBotEvents();
    });
  }
  function bindAddPersonalEvents(){
    var cr = $("add-create"); if(!cr) return;
    cr.addEventListener("click", async function(){
      var label = ($("add-label").value||"").trim();
      if(!label){ msg("add-msg","Enter a label.","err"); return; }
      clearMsg("add-msg"); msg("add-msg","Creating…","info");
      var r = await sb.from("open_tgate_tg_accounts").insert({ label:label, status:"pending", created_via:"app", account_type:"personal" });
      if(r.error){ msg("add-msg","Failed: "+r.error.message,"err"); return; }
      await refreshAccounts();
      // Select the newly created account (last in list)
      if(accounts.length>0) selectAccount(accounts[accounts.length-1].id);
    });
    var cn = $("add-cancel"); if(cn) cn.addEventListener("click", showDashboard);
  }
  function bindAddBotEvents(){
    var cr = $("bot-create"); if(!cr) return;
    cr.addEventListener("click", async function(){
      var label = ($("bot-label").value||"").trim();
      var token = ($("bot-token").value||"").trim();
      if(!label){ msg("bot-msg","Enter a label.","err"); return; }
      if(!token || token.indexOf(":")===-1){ msg("bot-msg","Enter a valid bot token (format: 123456:ABC...).","err"); return; }
      clearMsg("bot-msg"); msg("bot-msg","Creating bot account…","info");
      var hint = token.slice(-4);
      var r = await sb.from("open_tgate_tg_accounts").insert({
        label:label, status:"pending", created_via:"app", account_type:"bot", bot_token_hint:hint
      });
      if(r.error){ msg("bot-msg","Failed: "+r.error.message,"err"); return; }
      // Get the new account id and queue the start_bot_token command
      var accts = await sb.from("open_tgate_tg_accounts").select("id").eq("bot_token_hint",hint).eq("account_type","bot").order("created_at",{ascending:false}).limit(1);
      if(accts.data && accts.data[0]){
        var newId = accts.data[0].id;
        await sb.from("open_tgate_login_commands").insert({
          account_id: newId, action:"start_bot_token", payload:{ bot_token: token }, status:"pending"
        });
      }
      await refreshAccounts();
      if(accounts.length>0) selectAccount(accounts[accounts.length-1].id);
    });
    var cn = $("bot-cancel"); if(cn) cn.addEventListener("click", showDashboard);
  }

  // ---- Account detail pane ----
  function selectAccount(id){
    selectedId = id;
    var acc = accounts.find(function(a){ return a.id===id; });
    if(!acc){ showDashboard(); return; }
    updateSidebar();
    renderAccountDetail(acc);
  }

  function renderAccountDetail(acc){
    var isBot = acc.account_type === "bot";
    var dot = statusDot(acc.status);
    var html = '';

    // Label + edit
    html += '<div class="section">';
    html += '<div class="label-edit" id="label-edit">';
    html += '<span class="label-text" id="label-display" title="Click to edit">'+esc(acc.label)+'</span>';
    html += '<button class="btn sm" id="label-edit-btn" title="Edit label">✎</button>';
    html += '</div>';
    html += '<div style="display:flex;align-items:center;gap:8px;margin-top:6px">';
    html += '<span class="pill"><span class="dot '+dot+'"></span>'+esc(STATUS_LABEL[acc.status]||acc.status)+'</span>';
    if(isBot && acc.bot_token_hint) html += '<span class="pill">Token: •••'+esc(acc.bot_token_hint)+'</span>';
    if(isBot && acc.bot_username) html += '<span class="pill">@'+esc(acc.bot_username)+'</span>';
    if(acc.phone_masked) html += '<span class="pill">'+esc(acc.phone_masked)+'</span>';
    html += '</div></div>';

    // Profile (personal accounts)
    if(!isBot && (acc.tg_first_name || acc.tg_username)){
      var name = ((acc.tg_first_name||"")+" "+(acc.tg_last_name||"")).trim();
      var initials = (acc.tg_first_name||"?")[0].toUpperCase();
      html += '<div class="profile">';
      html += '<div class="profile-avatar">'+esc(initials)+'</div>';
      html += '<div class="profile-info">';
      if(name) html += '<div class="profile-name">'+esc(name)+'</div>';
      if(acc.tg_username) html += '<div class="profile-user">@'+esc(acc.tg_username)+'</div>';
      if(acc.phone_masked) html += '<div class="profile-phone">'+esc(acc.phone_masked)+'</div>';
      html += '</div></div>';
    }

    // Sync stepper
    if(!isBot && acc.sync_step){
      html += renderSyncStepper(acc.sync_step);
    }

    // Entity counts
    html += renderEntityCounts(acc.entity_counts);

    // Entity browser
    html += renderEntityBrowser(acc);

    // Login / action area
    if(acc.last_error) html += '<p class="hint" style="color:var(--bad);margin-top:12px">'+esc(acc.last_error)+'</p>';
    html += actionsFor(acc, isBot);

    // Notion sync status
    if(acc.notion_synced_at){
      html += '<div class="section" style="margin-top:20px">';
      html += '<div class="section-title">Notion sync</div>';
      html += '<div style="font-size:13px;color:var(--muted)">Last synced: '+esc(new Date(acc.notion_synced_at).toLocaleString())+'</div>';
      if(acc.notion_sync_error) html += '<div style="font-size:13px;color:var(--bad);margin-top:4px">Error: '+esc(acc.notion_sync_error)+'</div>';
      html += '</div>';
    }

    showPane("account-detail", html, acc.label);
    bindAccountDetailEvents(acc);
  }

  function bindAccountDetailEvents(acc){
    // Label editing
    var disp = $("label-display"), editBtn = $("label-edit-btn");
    if(disp && editBtn){
      function startEdit(){
        var le = $("label-edit"); if(!le) return;
        le.innerHTML = '<input class="label-input" id="label-input" type="text" value="'+esc(acc.label)+'" maxlength="80" />'
          +'<button class="btn sm primary" id="label-save">Save</button>'
          +'<button class="btn sm" id="label-cancel-btn">✕</button>';
        $("label-input").focus();
        $("label-save").addEventListener("click", saveLabel);
        $("label-cancel-btn").addEventListener("click", function(){ renderAccountDetail(acc); });
        $("label-input").addEventListener("keydown",function(e){ if(e.key==="Enter") saveLabel(); if(e.key==="Escape") renderAccountDetail(acc); });
      }
      async function saveLabel(){
        var val = ($("label-input").value||"").trim();
        if(!val) return;
        var r = await sb.from("open_tgate_tg_accounts").update({ label: val }).eq("id", acc.id);
        if(!r.error){ acc.label = val; $("pane-title").textContent = val; }
        renderAccountDetail(acc);
      }
      disp.addEventListener("click", startEdit);
      editBtn.addEventListener("click", startEdit);
    }

    // Login actions
    bindAcctActions(acc);
    // Entity browser tabs
    bindBrowserTabs(acc.id);
    // Auto-load first tab
    autoLoadEntities(acc);
  }

  function actionsFor(acc, isBot){
    var s = acc.status;
    if(s==="authorized" || s==="bot_authorized"){
      var btns = '<div class="step">';
      btns += '<button class="btn sm danger" data-act="logout" data-id="'+acc.id+'">'+(isBot?"Revoke bot":"Disconnect")+'</button>';
      btns += '</div>';
      return btns;
    }
    if(isBot) return ''; // Bot pending states handled by worker
    if(s==="awaiting_qr_scan"){
      var body = acc.qr_link ? renderQr(acc.qr_link) : '<p class="hint">Generating QR code…</p>';
      return '<div class="step">'+body+'<p class="hint">In Telegram: Settings → Devices → Link Desktop Device, then scan.</p></div>';
    }
    if(s==="awaiting_code"){
      return '<div class="step"><label>Login code (sent in Telegram)</label>'+
        '<input type="tel" inputmode="numeric" id="code-'+acc.id+'" placeholder="12345" autocomplete="off" />'+
        '<div class="row"><button class="btn primary sm" data-act="code" data-id="'+acc.id+'">Submit code</button></div></div>';
    }
    if(s==="awaiting_password"){
      return '<div class="step"><label>Two-step verification password</label>'+
        '<input type="password" id="pw-'+acc.id+'" autocomplete="off" />'+
        '<div class="row"><button class="btn primary sm" data-act="password" data-id="'+acc.id+'">Submit password</button></div></div>';
    }
    return '<div class="step" id="login-'+acc.id+'">'+
      '<div class="section-title">Connect Telegram</div>'+
      '<p class="hint" style="margin:0 0 12px">Choose one login method for this account.</p>'+
      '<div class="row login-options" style="margin-top:0">'+
      '<button class="btn sm" data-act="phone-open" data-id="'+acc.id+'">Phone number</button>'+
      '<button class="btn primary sm" data-act="qr" data-id="'+acc.id+'">QR code</button></div></div>';
  }

  function bindAcctActions(acc){
    document.querySelectorAll("#pane-body [data-act]").forEach(function(btn){
      if(btn._b) return; btn._b=true;
      btn.addEventListener("click", async function(){
        var id = btn.getAttribute("data-id"), act = btn.getAttribute("data-act");
        if(act==="qr"){ await tgCommand(id,"start_qr",null); }
        else if(act==="logout"){
          var isBot = acc.account_type === "bot";
          await tgCommand(id, isBot?"revoke_bot":"logout", null);
        }
        else if(act==="phone-open"){
          var host = $("login-"+id); if(!host) return;
          host.innerHTML = '<label>Phone number (international format)</label>'+
            '<input type="tel" id="phone-'+id+'" placeholder="+15551234567" autocomplete="off" />'+
            '<div class="row"><button class="btn primary sm" data-act="phone-send" data-id="'+id+'">Send code</button>'+
            '<button class="btn sm" data-act="login-cancel" data-id="'+id+'">Back to login options</button></div>';
          bindAcctActions(acc); $("phone-"+id).focus();
        }
        else if(act==="login-cancel"){ renderAccountDetail(acc); }
        else if(act==="phone-send"){
          var phone=($("phone-"+id).value||"").trim();
          if(!/^\\+\\d{7,15}$/.test(phone)) return;
          await tgCommand(id,"start_phone",{ phone_number: phone });
        }
        else if(act==="code"){
          var code=($("code-"+id).value||"").trim();
          if(!/^\\d{3,8}$/.test(code)) return;
          await tgCommand(id,"submit_code",{ code: code });
        }
        else if(act==="password"){
          var pw=$("pw-"+id).value;
          if(!pw) return;
          await tgCommand(id,"submit_password",{ password: pw });
        }
      });
    });
  }

  async function tgCommand(accountId, action, payload){
    var row = { account_id: accountId, action: action, status:"pending" };
    if(payload) row.payload = payload;
    var r = await sb.from("open_tgate_login_commands").insert(row);
    if(!r.error){
      await refreshAccounts();
      setTimeout(refreshAccounts, 700);
      return true;
    }
    return false;
  }

  function renderQr(link){
    try{
      var qr = window.qrcode(0, "M"); qr.addData(link); qr.make();
      return '<div class="qr">'+qr.createImgTag(4,0)+'</div>';
    }catch(e){ return '<p class="hint">QR ready — scan with Telegram.</p>'; }
  }

  function renderSyncStepper(currentStep){
    if(!currentStep) return "";
    var idx = SYNC_STEPS.indexOf(currentStep);
    if(idx === -1) return "";
    var html = '<div class="sync-stepper">';
    for(var i=0;i<SYNC_STEPS.length;i++){
      var step = SYNC_STEPS[i];
      var cls = i < idx ? "done" : (i === idx ? (step==="complete"?"done":"active") : "");
      var check = i < idx || (i===idx && step==="complete") ? '<span class="check">✓</span>' : "";
      var spinner = (i===idx && step!=="complete") ? '<span class="check">◌</span>' : "";
      if(i>0) html += '<span class="sync-arrow">→</span>';
      html += '<span class="sync-step '+cls+'">'+spinner+check+esc(SYNC_LABELS[step]||step)+'</span>';
    }
    html += '</div>';
    return html;
  }

  function renderEntityCounts(counts){
    if(!counts || typeof counts !== "object") return "";
    var kinds = ["contact","user","group","channel","bot","file"];
    var html = '<div class="entity-counts">', any = false;
    for(var i=0;i<kinds.length;i++){
      var k = kinds[i], v = counts[k] || 0;
      if(v > 0){
        any = true;
        html += '<span class="entity-chip"><span class="icon">'+(ENTITY_ICONS[k]||"")+"</span><b>"+v+"</b> "+(ENTITY_LABELS[k]||k)+"</span>";
      }
    }
    html += '</div>';
    return any ? html : "";
  }

  function renderEntityBrowser(acc){
    if(acc.status !== "authorized" && acc.status !== "bot_authorized") return "";
    var counts = acc.entity_counts || {};
    var kinds = ["contact","user","group","channel","bot","file"];
    var available = kinds.filter(function(k){ return (counts[k]||0) > 0; });
    if(available.length === 0) return "";
    var activeTab = openTabs[acc.id] || available[0];
    if(available.indexOf(activeTab)===-1) activeTab = available[0];
    var html = '<div class="entity-browser" id="browser-'+acc.id+'">';
    html += '<div class="entity-tabs">';
    for(var i=0;i<available.length;i++){
      var k = available[i];
      html += '<button class="entity-tab '+(k===activeTab?"active":"")+'" data-browser="'+acc.id+'" data-kind="'+k+'">'
        +(ENTITY_ICONS[k]||"")+" "+(ENTITY_LABELS[k]||k)+' ('+counts[k]+')</button>';
    }
    html += '</div><div class="entity-list" id="elist-'+acc.id+'"><div style="padding:20px;color:var(--muted);text-align:center;font-size:13px">Loading…</div></div></div>';
    return html;
  }

  function bindBrowserTabs(accId){
    document.querySelectorAll("[data-browser=\\""+accId+"\\\"]").forEach(function(tab){
      if(tab._b) return; tab._b=true;
      tab.addEventListener("click", async function(){
        var kind = tab.getAttribute("data-kind");
        openTabs[accId] = kind;
        var browser = $("browser-"+accId);
        if(browser) browser.querySelectorAll(".entity-tab").forEach(function(t){
          t.classList.toggle("active", t.getAttribute("data-kind")===kind);
        });
        var listEl = $("elist-"+accId);
        if(listEl) listEl.innerHTML = '<div style="padding:20px;color:var(--muted);text-align:center;font-size:13px">Loading…</div>';
        var entities = await loadEntities(accId, kind);
        if(listEl) listEl.innerHTML = renderEntityList(entities, kind);
      });
    });
  }

  async function autoLoadEntities(acc){
    if(acc.status!=="authorized" && acc.status!=="bot_authorized") return;
    var counts = acc.entity_counts || {};
    var kinds = ["contact","user","group","channel","bot","file"];
    var firstKind = openTabs[acc.id] || null;
    if(!firstKind){ for(var i=0;i<kinds.length;i++){ if((counts[kinds[i]]||0)>0){ firstKind=kinds[i]; break; } } }
    if(!firstKind) return;
    openTabs[acc.id] = firstKind;
    var listEl = $("elist-"+acc.id);
    if(listEl){
      var entities = await loadEntities(acc.id, firstKind);
      listEl.innerHTML = renderEntityList(entities, firstKind);
    }
  }

  async function loadEntities(accountId, kind){
    var r = await sb.from("open_tgate_tg_entities")
      .select("tg_id,kind,title,username,meta,last_message_date,is_archived")
      .eq("account_id", accountId).eq("kind", kind)
      .order("title",{ascending:true}).limit(200);
    return (r.data || []);
  }

  function renderEntityList(entities, kind){
    if(!entities || entities.length===0) return '<div style="padding:16px;color:var(--muted);text-align:center;font-size:13px">No '+esc(ENTITY_LABELS[kind]||kind)+' synced yet.</div>';
    var html = '';
    for(var i=0;i<entities.length;i++){
      var e = entities[i], meta = e.meta || {};
      var badge = "";
      if(meta.is_verified) badge = ' <span title="Verified" style="color:var(--accent)">✓</span>';
      if(meta.is_premium) badge += ' <span title="Premium" style="color:var(--warn)">★</span>';
      if(meta.is_bot) badge = ' <span title="Bot" style="color:var(--faint)">🤖</span>';
      var username = e.username ? '@'+esc(e.username) : '';
      var metaStr = "";
      if(kind==="group"||kind==="channel"){ if(meta.member_count) metaStr=meta.member_count+' members'; if(meta.unread_count) metaStr+=(metaStr?' · ':'')+meta.unread_count+' unread'; }
      if(kind==="contact"||kind==="user"){ if(meta.phone_masked) metaStr=meta.phone_masked; }
      if(kind==="file"){ if(meta.mime_type) metaStr=meta.mime_type; if(meta.size) metaStr+=(metaStr?' · ':'')+formatSize(meta.size); }
      html += '<div class="entity-row"><span class="ename">'+esc(e.title||"(no name)")+badge+'</span>'
        +(username?'<span class="euser">'+username+'</span>':'')
        +(metaStr?'<span class="emeta">'+esc(metaStr)+'</span>':'')+'</div>';
    }
    return html;
  }

  function formatSize(b){
    if(!b||b<=0) return "";
    if(b<1024) return b+" B";
    if(b<1048576) return (b/1024).toFixed(1)+" KB";
    return (b/1048576).toFixed(1)+" MB";
  }

  // ---- Polling ----
  async function refreshAccounts(){
    var r = await sb.from("open_tgate_tg_accounts")
      .select("id,label,status,needs,qr_link,last_error,phone_masked,tg_first_name,tg_last_name,tg_username,sync_step,entity_counts,updated_at,account_type,bot_token_hint,bot_username,bot_can_read_messages,notion_synced_at,notion_sync_error")
      .order("created_at",{ascending:true});
    if(r.error) return;
    accounts = r.data || [];
    updateSidebar();
    // Update stats if on dashboard
    var sa = $("stat-accounts"); if(sa) sa.textContent = accounts.length;
    // If viewing an account, refresh its detail
    if(selectedId){
      var acc = accounts.find(function(a){ return a.id===selectedId; });
      if(acc && currentPane==="account-detail") renderAccountDetail(acc);
    }
  }

  function startPolling(){
    refreshAccounts();
    if(tgTimer) clearInterval(tgTimer);
    tgTimer = setInterval(refreshAccounts, 3000);
    // Bind sidebar add buttons
    bindAddPersonalForm();
    bindAddBotForm();
  }

  // ---- Boot ----
  showPane("loading");
  if(/(?:^|[#&?])type=recovery(?:&|$)/.test(window.location.hash || "")){
    recovering = true;
    sidebar.classList.add("hidden");
    showPane("recovery","",null); bindRecoveryEvents();
  }
  sb.auth.getSession().then(function(res){ if(!recovering) renderFor(res.data.session); });
  sb.auth.onAuthStateChange(function(evt, session){
    if(evt === "PASSWORD_RECOVERY"){ recovering = true; sidebar.classList.add("hidden"); showPane("recovery","",null); bindRecoveryEvents(); return; }
    if(recovering) return;
    renderFor(session);
  });
})();
</script>
</body>
</html>`;
