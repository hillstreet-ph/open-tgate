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
    background:var(--card);border-right:1px solid var(--line);overflow:hidden;transition:transform .25s ease,width .2s ease,min-width .2s ease}
  .sb-head{padding:14px 16px 10px;border-bottom:1px solid var(--line2);display:flex;align-items:center;gap:10px}
  .sb-brand{display:flex;align-items:center;gap:8px;font-weight:800;font-size:15px;color:inherit;flex:1;min-width:0}
  .sb-logo{width:26px;height:26px;border-radius:7px;background:linear-gradient(135deg,var(--brand2),var(--accent));
    display:grid;place-items:center;color:#0a0c16;font-weight:900;font-size:12px;flex-shrink:0}
  .sb-actions{display:flex;gap:6px}
  .sb-btn{width:32px;height:32px;border-radius:8px;border:1px solid var(--line);background:transparent;
    color:var(--fg);cursor:pointer;display:grid;place-items:center;font-size:14px;transition:.15s}
  .sb-btn:hover{border-color:var(--brand);background:var(--card2)}
  .sb-btn:focus-visible,.sb-nav-item:focus-visible,.sb-item:focus-visible,.sb-add:focus-visible{
    outline:2px solid var(--accent);outline-offset:2px
  }

  .sb-section{padding:12px 14px 6px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.11em;color:var(--faint)}
  .sb-section-row{display:flex;align-items:center;justify-content:space-between}
  .sb-count{font-size:10px;color:var(--faint);background:var(--card2);border:1px solid var(--line2);border-radius:99px;padding:1px 7px}
  .sb-nav{padding:4px 9px 10px;border-bottom:1px solid var(--line2)}
  .sb-nav-item{display:flex;align-items:center;gap:11px;padding:9px 11px;width:100%;cursor:pointer;
    border:1px solid transparent;border-radius:10px;background:transparent;color:var(--muted);text-align:left;
    transition:.15s;font-family:inherit;font-size:13.5px;font-weight:600;line-height:1.35}
  .sb-nav-item:hover,.sb-nav-item.active{background:var(--card2);color:var(--fg)}
  .sb-nav-item.active{border-color:var(--line)}
  .sb-nav-icon{width:20px;display:grid;place-items:center;font-size:16px;flex-shrink:0}
  .sb-list{flex:1;overflow-y:auto;padding:3px 8px 12px}
  .sb-item{display:flex;align-items:center;gap:10px;padding:8px 9px;width:100%;cursor:pointer;
    border:1px solid transparent;border-radius:11px;background:transparent;color:var(--fg);text-align:left;
    transition:.12s;font:inherit}
  .sb-item:hover{background:var(--card2)}
  .sb-item.active{background:color-mix(in srgb,var(--brand) 12%,transparent);border-color:color-mix(in srgb,var(--brand) 28%,transparent)}
  .sb-avatar{width:34px;height:34px;border-radius:50%;flex-shrink:0;display:grid;place-items:center;
    font-weight:700;font-size:14px;color:#fff}
  .sb-avatar.personal{background:linear-gradient(135deg,var(--brand2),var(--accent))}
  .sb-avatar.bot{background:linear-gradient(135deg,#ff7a8a,var(--warn))}
  .sb-info{flex:1;min-width:0}
  .sb-name{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:14px}
  .sb-status{font-size:12px;display:flex;align-items:center;gap:5px}
  .sb-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
  .sb-dot.ok{background:var(--ok)} .sb-dot.warn{background:var(--warn)} .sb-dot.bad{background:var(--bad)} .sb-dot.off{background:var(--faint)}

  .sb-foot{padding:10px 10px 12px;border-top:1px solid var(--line2);display:flex;flex-direction:column;gap:6px}
  .sb-add{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:10px;border:1px dashed var(--line);
    background:transparent;color:var(--muted);cursor:pointer;font-size:13px;font-weight:600;transition:.15s;width:100%}
  .sb-add:hover{border-color:var(--brand);color:var(--fg);background:var(--card2)}
  .sb-user{font-size:12px;color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 4px}
  @media(min-width:901px){
    .sidebar.collapsed{width:76px;min-width:76px}
    .sidebar.collapsed .sb-head{padding:12px 9px;flex-direction:column}
    .sidebar.collapsed .sb-brand{flex:none}
    .sidebar.collapsed .sb-wordmark,.sidebar.collapsed .sb-section,.sidebar.collapsed .sb-info,
    .sidebar.collapsed .sb-add-label,.sidebar.collapsed .sb-nav-label,.sidebar.collapsed .sb-user{display:none}
    .sidebar.collapsed .sb-actions{flex-direction:column}
    .sidebar.collapsed .sb-nav{padding:6px 8px 10px}
    .sidebar.collapsed .sb-nav-item{justify-content:center;padding:10px 0}
    .sidebar.collapsed .sb-list{padding:8px 7px}
    .sidebar.collapsed .sb-item{justify-content:center;padding:8px 0}
    .sidebar.collapsed .sb-add{justify-content:center;padding:9px 0;border-style:solid}
    .sidebar.collapsed .sb-foot{padding:10px 8px}
    .sidebar.collapsed #signout{font-size:0;min-height:36px}
    .sidebar.collapsed #signout::before{content:"↪";font-size:16px}
  }

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
  .menu-toggle{display:none}
  /* Back is available on every viewport, not just mobile, and returns to the
     previous in-app view (history-backed) rather than leaving the console. */
  .back-btn{display:none;place-items:center;width:34px;height:34px;border-radius:8px;
    border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer;font-size:18px;line-height:1;flex-shrink:0}
  .back-btn.visible{display:grid}
  .back-btn:hover{border-color:var(--brand);background:var(--card2)}
  @media(max-width:900px){
    .sidebar{position:fixed;left:0;top:0;z-index:20;transform:translateX(-100%);width:min(86vw,320px);min-width:0}
    .pane{width:100%;min-width:0}
    .pane-head{padding:10px 12px;gap:8px}
    .pane-body{padding:16px;width:100%;overflow-x:hidden}
    .pane-title{font-size:15px}
    .nav-right .btn{padding:7px 9px}
    .card{padding:16px}
    .profile{align-items:flex-start}
    .qr{display:block;width:min(100%,280px);margin:12px auto 0}
    .qr img{width:100%;height:auto}
    .row.login-options{display:grid;grid-template-columns:1fr;gap:10px}
    .row.login-options .btn{width:100%;justify-content:center;min-height:44px}
    input[type=email],input[type=password],input[type=text],input[type=tel]{font-size:16px;min-height:44px}
    .sidebar.open{transform:translateX(0)}
    .sidebar.collapsed{width:min(86vw,320px);min-width:0}
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

  /* Telegram workspace: restrained navy surfaces and violet selection. */
  :root{--bg:#101725;--card:#1c2636;--card2:#263246;--line:#344056;--line2:#273348;--brand:#a99fff;--brand2:#7464ed;--accent:#91baff;--sidebar-w:244px;--radius:12px}
  body{font-family:"Aptos","Trebuchet MS",sans-serif}
  .sidebar{background:#080e19}.sb-head{padding:19px 15px}.sb-logo{background:#8171f1;color:#fff;border-radius:8px}
  .sb-nav-item.active{background:#222d40;border-color:transparent}.sb-nav-item{min-height:42px}
  html[data-theme="light"] .sidebar{background:var(--card)}
  html[data-theme="light"] .sb-nav-item.active{background:var(--card2)}
  html[data-theme="light"] .message-bubble.outgoing{background:#eeebff;border-color:#ddd5ff}
  .pane-head{min-height:66px}.pane-title{letter-spacing:-.02em}.pane-body{max-width:1600px;width:100%;margin:auto}
  .card{box-shadow:none}.btn{font-family:inherit}.btn.primary{background:var(--brand2);color:#fff}
  .workspace-intro{margin-bottom:22px}.workspace-intro h2{font-size:23px;letter-spacing:-.04em}.eyebrow{color:var(--accent);font-size:10px;letter-spacing:.16em;text-transform:uppercase;margin-bottom:7px}
  .workspace-tools{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
  select,textarea{border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--fg);font:inherit;padding:10px 12px;max-width:100%}
  select:focus-visible,textarea:focus-visible,.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  textarea{width:100%;resize:vertical;min-height:120px}select{min-height:42px}input[type=checkbox]{accent-color:var(--brand2)}
  .workspace-tools input[type=text]{flex:1;min-width:140px}.workspace-tools label{margin:0;display:flex;gap:7px;align-items:center;font-size:12px}
  .inbox-shell{display:grid;grid-template-columns:minmax(240px,330px) minmax(0,1fr);border:1px solid var(--line);border-radius:12px;overflow:hidden;height:calc(100dvh - 175px);min-height:380px;background:var(--bg)}
  .conversation-column{display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--line);background:var(--card)}
  .conversation-column .workspace-tools{padding:14px;margin:0;border-bottom:1px solid var(--line2)}
  .conversation-list{overflow-y:auto;flex:1}.conversation-row{display:flex;align-items:center;gap:11px;padding:14px 13px;border:0;border-bottom:1px solid var(--line2);background:transparent;color:var(--fg);width:100%;cursor:pointer;text-align:left;font:inherit}
  .conversation-row:hover,.conversation-row.active{background:var(--card2)}.conversation-row.active{box-shadow:inset 3px 0 var(--brand2)}
  .chat-avatar{display:grid;place-items:center;width:38px;height:38px;border-radius:50%;background:#4567a5;color:#fff;flex-shrink:0;font-weight:700}
  .conversation-copy{flex:1;min-width:0}.conversation-title{font-size:13px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.conversation-preview{font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.conversation-account{font-size:10px;color:var(--faint);margin-top:3px}
  .unread-badge{background:var(--brand2);color:#fff;font-size:10px;border-radius:20px;padding:2px 6px;flex-shrink:0}.chat-panel{display:flex;flex-direction:column;min-width:0;min-height:0}
  .chat-header{padding:15px 18px;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center}.chat-header h2{font-size:15px}.chat-header .sub{font-size:11px;margin:0}.chat-scroll{flex:1;min-height:0;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}
  .message-bubble{max-width:86%;padding:11px 13px;background:var(--card);border:1px solid var(--line2);border-radius:12px 12px 12px 3px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;align-self:flex-start}.message-bubble.outgoing{align-self:flex-end;background:#353450;border-color:#474262;border-radius:12px 12px 3px 12px}.message-meta{font-size:10px;color:var(--muted);margin-top:7px}.chat-foot{border-top:1px solid var(--line2);padding:12px 18px;display:flex;gap:12px;align-items:center;color:var(--faint);font-size:11px}.chat-foot .btn{margin-left:auto}
  .empty-state{padding:35px 20px;text-align:center;color:var(--muted);font-size:13px}.empty-state strong{display:block;color:var(--fg);font-size:15px;margin-bottom:6px}.chat-panel>.empty-state{margin:auto}.workspace-error{padding:16px;color:var(--bad);font-size:13px}
  .workspace-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px}.workspace-stack{display:flex;flex-direction:column;gap:18px}.source-row{border-top:1px solid var(--line2);padding:14px 0}.source-row:first-child{border:0}.source-actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}.source-title{font-weight:700;font-size:14px}.source-meta{font-size:11px;color:var(--faint)}.source-excerpt{font-size:12px;color:var(--muted);margin-top:6px;white-space:pre-wrap;overflow-wrap:anywhere}
  .workspace-table{width:100%;border-collapse:collapse;font-size:13px}.workspace-table th{font-size:10px;text-transform:uppercase;letter-spacing:.08em;text-align:left;color:var(--faint);padding:12px}.workspace-table td{padding:12px;border-top:1px solid var(--line2)}.table-scroll{overflow:auto}.workspace-code{display:block;white-space:pre-wrap;overflow-wrap:anywhere;padding:12px;background:var(--bg);border:1px solid var(--line);border-radius:8px;font-size:12px}.chat-mobile-back{display:none}.note{font-size:12px;color:var(--faint);margin-top:12px}
  @media(max-width:1050px){.workspace-grid{grid-template-columns:1fr}.entity-row .emeta{display:none}}
  @media(max-width:700px){.inbox-shell{display:block;height:calc(100dvh - 170px)}.conversation-column{height:100%;border-right:0}.chat-panel{display:none;height:100%}.inbox-shell.chat-open .conversation-column{display:none}.inbox-shell.chat-open .chat-panel{display:flex}.chat-mobile-back{display:inline-flex}.chat-scroll{padding:12px}.message-bubble{max-width:93%}.workspace-tools select{width:100%}.workspace-intro h2{font-size:21px}.chat-foot{padding:10px;flex-wrap:wrap}.pane-head{min-height:55px}.workspace-table th,.workspace-table td{padding:10px}.pane-body{padding:12px}}
  @media(prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto!important}}

</style>
</head>
<body>
<div class="sb-overlay" id="sb-overlay"></div>
<div class="shell">
  <!-- ===== LEFT SIDEBAR ===== -->
  <aside class="sidebar" id="sidebar">
    <div class="sb-head">
      <a class="sb-brand" href="/" title="Open-TGate home"><span class="sb-logo">t</span><span class="sb-wordmark">Open-TGate</span></a>
      <div class="sb-actions">
        <button class="sb-btn" id="theme" title="Toggle theme" aria-label="Toggle theme">◐</button>
        <button class="sb-btn" id="sb-collapse" title="Collapse sidebar" aria-label="Collapse sidebar" aria-expanded="true">«</button>
      </div>
    </div>
    <nav class="sb-nav" aria-label="Workspace navigation">
      <div class="sb-section">Workspace</div>
      <button class="sb-nav-item" id="nav-dashboard" type="button" title="Dashboard" aria-current="false">
        <span class="sb-nav-icon" aria-hidden="true">⌂</span><span class="sb-nav-label">Dashboard</span>
      </button>
      <button class="sb-nav-item" id="nav-inbox" type="button" aria-current="false"><span class="sb-nav-icon" aria-hidden="true">▤</span><span class="sb-nav-label">Inbox</span></button>
      <button class="sb-nav-item" id="nav-contacts" type="button" aria-current="false"><span class="sb-nav-icon" aria-hidden="true">♧</span><span class="sb-nav-label">Contacts</span></button>
      <button class="sb-nav-item" id="nav-activity" type="button" aria-current="false"><span class="sb-nav-icon" aria-hidden="true">◴</span><span class="sb-nav-label">Activity</span></button>
      <button class="sb-nav-item" id="nav-knowledge" type="button" aria-current="false"><span class="sb-nav-icon" aria-hidden="true">✦</span><span class="sb-nav-label">AI knowledge</span></button>
      <button class="sb-nav-item" id="nav-settings" type="button" aria-current="false"><span class="sb-nav-icon" aria-hidden="true">⚙</span><span class="sb-nav-label">Settings · API / MCP</span></button>
    </nav>
    <div class="sb-section sb-section-row"><span>Telegram accounts</span><span class="sb-count" id="sb-count">0</span></div>
    <div class="sb-list" id="sb-acct-list" role="list" aria-label="Connected Telegram accounts"></div>
    <div class="sb-foot">
      <button class="sb-add" id="sb-add-personal" title="Connect personal account" type="button"><span aria-hidden="true">＋</span><span class="sb-add-label">Personal account</span></button>
      <button class="sb-add" id="sb-add-bot" title="Add Telegram bot" type="button"><span aria-hidden="true">＋</span><span class="sb-add-label">Bot token</span></button>
      <a class="sb-add hidden" id="sb-open-connect" href="#" target="_blank" rel="noopener"
         title="Open Open-Connect to manage connected accounts"><span aria-hidden="true">↗</span><span class="sb-add-label">Open-Connect</span></a>
      <div class="sb-user" id="sb-user"></div>
      <button class="btn sm" id="signout" style="width:100%;justify-content:center"><span aria-hidden="true">↪</span><span class="sb-signout-label">Sign out</span></button>
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
  <p class="sub">Connect a Telegram account via phone or QR code. Sync reads the chats and contacts available to this account.</p>
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
  // Panes that participate in browser history. Non-navigational panes (loading,
  // login, recovery, denied) must NOT rewrite the URL: doing so would strip a
  // deep link (#account=…) before renderFor() has a chance to read it.
  var NAV_PANES = { "dashboard":1, "account-detail":1, "add-personal":1, "add-bot":1, "inbox":1, "contacts":1, "activity":1, "knowledge":1, "settings":1 };
  function showPane(name, html, title, opts){
    opts = opts || {};
    if(currentPane!==name){workspaceEpoch++;chatEpoch++;}
    var body = $("pane-body");
    body.innerHTML = html || renderTemplate("tmpl-"+name);
    $("pane-title").textContent = title || "Open-TGate";
    currentPane = name;
    // Keep the URL hash in step so the browser Back button and a reload land on
    // the same view. noHistory means "do not add an entry"; we still replace the
    // current one, so a stale hash (e.g. #account=... left after Back to home)
    // can never disagree with what is on screen.
    if(NAV_PANES[name]){
      try{
        var hash = name==="account-detail" && selectedId ? "#account="+selectedId : (name==="dashboard" ? "#home" : "#"+name);
        var url = location.pathname + location.search + hash;
        var state = {otgPane:name, accountId: selectedId || null, chatAccountId: activeChat ? activeChat.account_id : null, chatId: activeChat ? activeChat.chat_id : null};
        if(opts.noHistory || opts.replace) history.replaceState(state, "", url);
        else history.pushState(state, "", url);
      }catch(e){}
    }
    updateBack();
  }
  function updateBack(){
    var back = $("back-btn"); if(!back) return;
    back.classList.toggle("visible", currentPane==="account-detail" || currentPane==="add-personal" || currentPane==="add-bot" || (currentPane==="inbox" && !!activeChat));
  }

  // Mobile sidebar toggle
  var sidebar = $("sidebar"), overlay = $("sb-overlay");
  var sidebarCollapsed = false;
  try { sidebarCollapsed = localStorage.getItem("open-tgate-sidebar-collapsed") === "1"; } catch(e) {}
  function applySidebarMode(collapsed, persist){
    var compact = window.innerWidth > 900 && !!collapsed;
    sidebar.classList.toggle("collapsed", compact);
    var toggle = $("sb-collapse");
    if(toggle){
      toggle.textContent = compact ? "»" : "«";
      toggle.setAttribute("aria-expanded", compact ? "false" : "true");
      toggle.setAttribute("aria-label", compact ? "Expand sidebar" : "Collapse sidebar");
      toggle.title = compact ? "Expand sidebar" : "Collapse sidebar";
    }
    if(persist){
      sidebarCollapsed = compact;
      try { localStorage.setItem("open-tgate-sidebar-collapsed", compact ? "1" : "0"); } catch(e) {}
    }
  }
  applySidebarMode(sidebarCollapsed, false);
  $("sb-collapse").addEventListener("click", function(){ applySidebarMode(!sidebar.classList.contains("collapsed"), true); });
  window.addEventListener("resize", function(){ applySidebarMode(sidebarCollapsed, false); });
  function setMobileSidebarOpen(open){
    sidebar.classList.toggle("open", !!open);
    overlay.classList.toggle("open", !!open);
    $("menu-toggle").setAttribute("aria-expanded", open ? "true" : "false");
  }
  $("menu-toggle").addEventListener("click", function(){ setMobileSidebarOpen(!sidebar.classList.contains("open")); });
  overlay.addEventListener("click", function(){ setMobileSidebarOpen(false); });
  $("nav-dashboard").addEventListener("click", function(){ showDashboard(); closeMobile(); });
  // Back: if we are on a pushed in-app view, unwind history (so the browser
  // Back button and this control behave identically); otherwise return home.
  $("back-btn").addEventListener("click", function(){
    closeMobile();
    var st = history.state;
    if(st && st.otgPane && st.otgPane!=="dashboard") history.back();
    else showDashboard(true);
  });
  function closeMobile(){ setMobileSidebarOpen(false); }
  window.addEventListener("popstate", function(e){
    if(!authenticated) return;
    var st = (e && e.state) || null;
    closeMobile();
    if(!st || !st.otgPane || st.otgPane==="dashboard"){ showDashboard(true); return; }
    if(st.otgPane==="inbox"){ showInbox(true, st.chatAccountId, st.chatId); return; }
    if(WORKSPACE_PANES.indexOf(st.otgPane)!==-1){ showWorkspace(st.otgPane, true); return; }
    if(st.otgPane==="account-detail" && st.accountId){
      var acc = accounts.find(function(a){ return a.id===st.accountId; });
      if(acc){ selectAccount(st.accountId, "none"); return; }
    }
    showDashboard(true);
  });

  // Init check
  if(!window.supabase || CFG.url.indexOf("%SUPABASE")===0){
    showPane("login","",null,{noHistory:true}); msg("login-msg","Console is not configured (SUPABASE_URL / key missing).","err");
    return;
  }
  var sb = window.supabase.createClient(CFG.url, CFG.key, { auth:{ persistSession:true, autoRefreshToken:true, detectSessionInUrl:true } });
  var REDIRECT = window.location.origin + "/app";

  // State
  var recovering = false;
  var authenticated = false;
  var accounts = [];
  var selectedId = null;
  var activeChat = null;
  var WORKSPACE_PANES = ["inbox","contacts","activity","knowledge","settings"];
  var workspaceEpoch = 0;
  var chatEpoch = 0;
  var inboxRows = [], messageRows = [], inboxOffset = 0, messagesHaveMore = false;
  var inboxFilters = {account:"",search:"",unread:false,archive:false};
  var contactsOffset = 0;
  var operatorRole = "operator";
  var pendingAccountId = null;
  var tgTimer = null;
  var openTabs = {};
  // Whether a worker heartbeat was seen in the last two minutes. Used to explain
  // a login that is not progressing (worker offline) instead of leaving the
  // operator staring at a spinner.
  var workerLive = false;
  // Optional Open-Connect integration endpoint (Composio-backed managed
  // connections). Empty means the option stays hidden; no secrets are ever
  // placed in the dashboard bundle.
  var OPEN_CONNECT_URL = "%OPEN_CONNECT_URL%";

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
      authenticated = false;
      sidebar.classList.add("hidden");
      showPane("recovery","",null,{noHistory:true}); bindRecoveryEvents(); return;
    }
    if(!session){
      authenticated = false;
      sidebar.classList.add("hidden");
      showPane("login","",null,{noHistory:true}); bindLoginEvents(); return;
    }
    var email = (session.user && session.user.email) || "";
    var op = await sb.from("open_tgate_operators").select("email,role,is_active").limit(1);
    if(op.error || !op.data || !op.data[0] || !op.data[0].is_active){
      authenticated = false;
      sidebar.classList.add("hidden");
      showPane("denied","",null,{noHistory:true});
      var sub = $("denied-sub");
      if(sub) sub.textContent = "Signed in as "+email+", but not an active operator.";
      var dso = $("denied-signout"); if(dso) dso.addEventListener("click", doSignOut);
      return;
    }
    // Auth refresh must not replace an operator form or close the keyboard.
    operatorRole = op.data[0].role;
    if(authenticated) return;
    // Authenticated operator
    authenticated = true;
    sidebar.classList.remove("hidden");
    $("sb-user").textContent = email;
    $("signout").addEventListener("click", doSignOut);
    bindOpenConnect();
    // Deep link: /app#account=<id> opens that account after the list loads.
    var deep = (location.hash||"").match(/^#account=([0-9a-f-]{8,})$/i);
    if(deep) pendingAccountId = deep[1];
    await startPolling();
    if(pendingAccountId){
      var pid = pendingAccountId; pendingAccountId = null;
      if(accounts.some(function(a){ return a.id===pid; })) selectAccount(pid, "replace");
      else showDashboard(true);
    } else {
      var workspaceDeep = (location.hash||"").slice(1);
      if(WORKSPACE_PANES.indexOf(workspaceDeep)!==-1) showWorkspace(workspaceDeep,true);
      else showDashboard();
    }
  }

  // ---- Open-Connect integration (Composio-backed managed connections) ----
  function bindOpenConnect(){
    var link = $("sb-open-connect");
    if(!link) return;
    var url = String(OPEN_CONNECT_URL||"").trim();
    if(!url || url.indexOf("%OPEN_CONNECT")===0){ link.classList.add("hidden"); return; }
    link.href = url;
    link.classList.remove("hidden");
  }

  // ---- Dashboard (home) ----
  function showDashboard(noHistory){
    selectedId = null; activeChat = null; workspaceEpoch++; chatEpoch++;
    showPane("dashboard","","Open-TGate", noHistory ? {noHistory:true} : {replace:true});
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
    if(r.error){var unavailable=$("hb-empty");if(unavailable){unavailable.classList.remove("hidden");unavailable.textContent="Unable to load worker health: "+r.error.message;}return;}
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
    workerLive = live > 0;
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
    var count = $("sb-count"); if(count) count.textContent = accounts.length;
    WORKSPACE_PANES.forEach(function(name){ var el=$("nav-"+name); if(el){ el.classList.toggle("active", currentPane===name); el.setAttribute("aria-current",currentPane===name?"page":"false"); } });
    var dashboardNav = $("nav-dashboard");
    if(dashboardNav){
      var onDashboard = currentPane === "dashboard" && !selectedId;
      dashboardNav.classList.toggle("active", onDashboard);
      dashboardNav.setAttribute("aria-current", onDashboard ? "page" : "false");
    }
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
      html += '<div role="listitem"><button type="button" class="sb-item'+active+'" data-acct="'+esc(a.id)+'" title="'+esc(a.label+' · '+sidebarLabel(a))+'" aria-pressed="'+(a.id===selectedId?'true':'false')+'">';
      html += '<span class="sb-avatar '+(isBot?"bot":"personal")+'" aria-hidden="true">'+esc(initials)+'</span>';
      html += '<div class="sb-info">';
      html += '<div class="sb-name">'+esc(a.label)+'</div>';
      html += '<div class="sb-status"><span class="sb-dot '+statusDot(a.status)+'"></span>'+esc(sidebarLabel(a))+'</div>';
      html += '</div></button></div>';
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
      // account_type must match the production vocabulary: 'user' | 'bot'
      // (see 20260929120000_open_tgate_account_vocab_forward.sql).
      var r = await sb.from("open_tgate_tg_accounts")
        .insert({ label:label, status:"pending", created_via:"app", account_type:"user" })
        .select("id").single();
      if(r.error){ msg("add-msg","Failed: "+r.error.message,"err"); return; }
      await refreshAccounts();
      if(r.data && r.data.id) selectAccount(r.data.id, "replace");
    });
    var cn = $("add-cancel"); if(cn) cn.addEventListener("click", showDashboard);
  }
  function bindAddBotEvents(){
    var cr = $("bot-create"); if(!cr) return;
    cr.addEventListener("click", async function(){
      var label = ($("bot-label").value||"").trim();
      var token = ($("bot-token").value||"").trim();
      if(!label){ msg("bot-msg","Enter a label.","err"); return; }
      if(!/^\\d{6,}:[A-Za-z0-9_-]{20,}$/.test(token)){ msg("bot-msg","Enter a valid bot token (format: 123456:ABC...).","err"); return; }
      clearMsg("bot-msg"); msg("bot-msg","Creating bot account…","info");
      var hint = token.slice(-4);
      var r = await sb.from("open_tgate_tg_accounts")
        .insert({ label:label, status:"pending", created_via:"app", account_type:"bot", bot_token_hint:hint })
        .select("id").single();
      if(r.error){ msg("bot-msg","Failed: "+r.error.message,"err"); return; }
      var newId = r.data && r.data.id;
      if(newId){
        // Queue the transient token; the worker validates it and clears it.
        await queueBotToken(newId, token);
      }
      await refreshAccounts();
      if(newId) selectAccount(newId, "replace");
    });
    var cn = $("bot-cancel"); if(cn) cn.addEventListener("click", showDashboard);
  }

  async function queueBotToken(accountId, token){
    return await sb.from("open_tgate_login_commands").insert({
      account_id: accountId, action:"start_bot_token", payload:{ bot_token: token }, status:"pending"
    });
  }

  // ---- Account detail pane ----
  // hist: undefined = push a new history entry; "replace" = replace it;
  //        "none" = leave history untouched (polling / popstate re-render).
  function selectAccount(id, hist){
    selectedId = id; activeChat = null; workspaceEpoch++; chatEpoch++;
    var acc = accounts.find(function(a){ return a.id===id; });
    if(!acc){ showDashboard(hist!=="push"); return; }
    updateSidebar();
    renderAccountDetail(acc, hist);
  }

  function renderAccountDetail(acc, hist){
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
    if(!workerLive && acc.status!=="authorized" && acc.status!=="bot_authorized"){
      html += '<p class="hint" style="color:var(--warn);margin-top:12px">No worker heartbeat in the last 2 minutes — logins stay queued until the TDLib worker is running.</p>';
    }
    html += actionsFor(acc, isBot);

    // Notion sync status
    if(acc.notion_synced_at){
      html += '<div class="section" style="margin-top:20px">';
      html += '<div class="section-title">Notion sync</div>';
      html += '<div style="font-size:13px;color:var(--muted)">Last synced: '+esc(new Date(acc.notion_synced_at).toLocaleString())+'</div>';
      if(acc.notion_sync_error) html += '<div style="font-size:13px;color:var(--bad);margin-top:4px">Error: '+esc(acc.notion_sync_error)+'</div>';
      html += '</div>';
    }

    var paneOpts = hist==="replace" ? {replace:true} : (hist==="none" ? {noHistory:true} : undefined);
    showPane("account-detail", html, acc.label, paneOpts);
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
        $("label-cancel-btn").addEventListener("click", function(){ renderAccountDetail(acc, "none"); });
        $("label-input").addEventListener("keydown",function(e){ if(e.key==="Enter") saveLabel(); if(e.key==="Escape") renderAccountDetail(acc, "none"); });
      }
      async function saveLabel(){
        var val = ($("label-input").value||"").trim();
        if(!val) return;
        var r = await sb.from("open_tgate_tg_accounts").update({ label: val }).eq("id", acc.id);
        if(!r.error){ acc.label = val; $("pane-title").textContent = val; }
        renderAccountDetail(acc, "none");
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

  // A compact progress trail so the operator always knows where the login is,
  // and a single reusable method picker + forms so every status can reach every
  // login method (phone, QR, bot token) without getting stuck.
  var LOGIN_FLOW = ["pending","initializing","awaiting_phone","awaiting_code","awaiting_password","authorized"];
  function loginTrail(acc){
    var s = acc.status, isBot = acc.account_type==="bot";
    var steps = isBot ? ["pending","validating_token","bot_authorized"] : LOGIN_FLOW;
    if(steps.indexOf(s)===-1) return "";
    var labels = { pending:"Start", initializing:"Connect", awaiting_phone:"Phone",
      awaiting_code:"Code", awaiting_password:"2FA", authorized:"Connected",
      validating_token:"Token", bot_authorized:"Active" };
    var idx = steps.indexOf(s);
    var html = '<div class="sync-stepper" style="margin-top:12px">';
    for(var i=0;i<steps.length;i++){
      var cls = i<idx ? "sync-step done" : (i===idx ? "sync-step active" : "sync-step");
      html += '<div class="'+cls+'"><span class="check">'+(i<idx?"✓":(i===idx?"●":""))+'</span>'+esc(labels[steps[i]]||steps[i])+'</div>';
      if(i<steps.length-1) html += '<span class="sync-arrow">→</span>';
    }
    return html+'</div>';
  }

  function phoneForm(acc, back){
    return '<label>Phone number (international format)</label>'+
      '<input type="tel" id="phone-'+acc.id+'" placeholder="+15551234567" autocomplete="off" />'+
      '<div class="row"><button class="btn primary sm" data-act="phone-send" data-id="'+acc.id+'">Send code</button>'+
      (back?'<button class="btn sm" data-act="login-cancel" data-id="'+acc.id+'">Back</button>':'')+'</div>';
  }
  function botForm(acc){
    return '<div class="section-title">Bot token</div>'+
      '<p class="hint" style="margin:0 0 12px">Paste the BotFather token '+(acc.status==="error"?"again to retry":"to connect")+'.</p>'+
      '<label for="bottoken-'+acc.id+'">Bot token</label>'+
      '<input type="password" id="bottoken-'+acc.id+'" placeholder="123456:ABC-DEF1234…" autocomplete="off" />'+
      '<div class="row"><button class="btn primary sm" data-act="bot-token" data-id="'+acc.id+'">Save token</button></div>';
  }
  function methodPicker(acc){
    var s = acc.status;
    var note = s==="error" ? "The last attempt failed — pick a method to try again."
             : s==="logged_out" ? "This account was signed out. Connect it again:"
             : "Choose one login method for this account.";
    return '<div class="step" id="login-'+acc.id+'">'+
      '<div class="section-title">'+(s==="logged_out"?"Reconnect Telegram":"Connect Telegram")+'</div>'+
      '<p class="hint" style="margin:0 0 12px">'+esc(note)+'</p>'+
      '<div class="row login-options" style="margin-top:0">'+
      '<button class="btn sm" data-act="phone-open" data-id="'+acc.id+'">Phone number</button>'+
      '<button class="btn primary sm" data-act="qr" data-id="'+acc.id+'">QR code</button>'+
      '</div></div>';
  }

  function actionsFor(acc, isBot){
    var s = acc.status;
    var trail = loginTrail(acc);
    if(s==="authorized" || s==="bot_authorized"){
      return trail + '<div class="step">'+
        '<button class="btn sm danger" data-act="logout" data-id="'+acc.id+'">'+(isBot?"Revoke bot":"Disconnect")+'</button>'+
        '</div>';
    }
    if(isBot){
      if(s==="validating_token"){
        return trail + '<div class="step"><p class="hint">Validating the bot token…</p>'+
          '<div class="row"><button class="btn sm" data-act="reset" data-id="'+acc.id+'">Cancel</button></div></div>';
      }
      // pending / error / logged_out: enter or re-enter the token.
      return trail + '<div class="step" id="login-'+acc.id+'">'+botForm(acc)+
        '<div class="row" style="margin-top:8px"><button class="btn sm" data-act="reset" data-id="'+acc.id+'">Clear</button></div></div>';
    }
    var body;
    if(s==="awaiting_qr_scan"){
      body = '<div class="step" id="login-'+acc.id+'">'+(acc.qr_link ? renderQr(acc.qr_link) : '<p class="hint">Generating QR code…</p>')+
        '<p class="hint">In Telegram: Settings → Devices → Link Desktop Device, then scan.</p>'+
        (acc.qr_link ? '<p class="hint">Cannot scan? Paste this link into Telegram: <code style="word-break:break-all">'+esc(acc.qr_link)+'</code></p>' : '')+
        '<div class="row"><button class="btn sm" data-act="reset" data-id="'+acc.id+'">Use another method</button></div></div>';
    } else if(s==="awaiting_code"){
      body = '<div class="step" id="login-'+acc.id+'"><label>Login code (sent in Telegram)</label>'+
        '<input type="tel" inputmode="numeric" id="code-'+acc.id+'" placeholder="12345" autocomplete="off" />'+
        '<div class="row"><button class="btn primary sm" data-act="code" data-id="'+acc.id+'">Submit code</button>'+
        '<button class="btn sm" data-act="resend" data-id="'+acc.id+'">Resend code</button>'+
        '<button class="btn sm" data-act="reset" data-id="'+acc.id+'">Start over</button></div></div>';
    } else if(s==="awaiting_password"){
      body = '<div class="step" id="login-'+acc.id+'"><label>Two-step verification password</label>'+
        '<input type="password" id="pw-'+acc.id+'" autocomplete="off" />'+
        '<p class="hint">This is your Telegram cloud password, not your phone unlock code.</p>'+
        '<div class="row"><button class="btn primary sm" data-act="password" data-id="'+acc.id+'">Submit password</button>'+
        '<button class="btn sm" data-act="reset" data-id="'+acc.id+'">Start over</button></div></div>';
    } else if(s==="awaiting_phone"){
      body = '<div class="step" id="login-'+acc.id+'">'+phoneForm(acc,false)+'</div>';
    } else if(s==="initializing"){
      body = '<div class="step"><p class="hint">Starting the Telegram session…</p>'+
        '<div class="row"><button class="btn sm" data-act="reset" data-id="'+acc.id+'">Start over</button></div></div>';
    } else {
      // pending / logged_out / error — pick a method.
      body = methodPicker(acc);
    }
    return trail + body;
  }

  function bindAcctActions(acc){
    document.querySelectorAll("#pane-body [data-act]").forEach(function(btn){
      if(btn._b) return; btn._b=true;
      btn.addEventListener("click", async function(){
        var id = btn.getAttribute("data-id"), act = btn.getAttribute("data-act");
        if(act==="qr"){ await tgCommand(id,"start_qr",null); }
        else if(act==="bot-token"){
          var bt=($("bottoken-"+id).value||"").trim();
          if(!/^\\d{6,}:[A-Za-z0-9_-]{20,}$/.test(bt)){ showActionMsg(id,"Enter a valid bot token (format 123456:ABC…).","err"); return; }
          await tgCommand(id,"start_bot_token",{ bot_token: bt });
        }
        else if(act==="reset"){ await resetAccount(id); }
        else if(act==="logout"){
          var isBot = acc.account_type === "bot";
          await tgCommand(id, isBot?"revoke_bot":"logout", null);
        }
        else if(act==="phone-open"){
          var host = $("login-"+id); if(!host) return;
          host.innerHTML = phoneForm(acc,true);
          bindAcctActions(acc); $("phone-"+id).focus();
        }
        else if(act==="login-cancel"){ renderAccountDetail(acc, "none"); }
        else if(act==="phone-send"){
          var phone=($("phone-"+id).value||"").trim();
          if(!/^\\+\\d{7,15}$/.test(phone)){ showActionMsg(id,"Enter a phone in international format, e.g. +15551234567.","err"); return; }
          await tgCommand(id,"start_phone",{ phone_number: phone });
        }
        else if(act==="code"){
          var code=($("code-"+id).value||"").trim();
          if(!/^\\d{3,8}$/.test(code)){ showActionMsg(id,"Enter the numeric login code Telegram sent.","err"); return; }
          await tgCommand(id,"submit_code",{ code: code });
        }
        else if(act==="resend"){
          await tgCommand(id,"resend_code",null);
        }
        else if(act==="password"){
          var pw=$("pw-"+id).value;
          if(!pw){ showActionMsg(id,"Enter your two-step verification password.","err"); return; }
          await tgCommand(id,"submit_password",{ password: pw });
        }
      });
    });
  }

  // Inline, per-form error/status text. Falls back to the account-level hint so
  // a validation error is never silent.
  function showActionMsg(id, text, kind){
    var host = $("login-"+id);
    var box = host ? host.querySelector(".action-msg") : null;
    if(!box && host){ box = document.createElement("div"); box.className="msg action-msg"; host.appendChild(box); }
    if(!box) return;
    box.textContent = text; box.className = "msg show action-msg "+(kind||"info");
  }

  // Clear a stuck login: reset the account row to a clean 'pending' state so the
  // operator can pick a different method without creating a duplicate account.
  async function resetAccount(id){
    var r = await sb.from("open_tgate_tg_accounts")
      .update({ status:"pending", needs:null, qr_link:null, last_error:null, sync_step:null })
      .eq("id", id);
    if(!r.error){ await refreshAccounts(); }
    return !r.error;
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
    showActionMsg(accountId, "Could not send “"+action+"”: "+(r.error.message||"unknown error")+".", "err");
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
    document.querySelectorAll('[data-browser="' + accId + '"]').forEach(function(tab){
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

  // ---- Telegram inbox and workspace ----
  WORKSPACE_PANES.forEach(function(name){
    $("nav-"+name).addEventListener("click",function(){ if(authenticated){ showWorkspace(name); closeMobile(); } });
  });
  function workspaceIntro(kicker,title,description){
    return '<div class="workspace-intro"><p class="eyebrow">'+esc(kicker)+'</p><h2>'+esc(title)+'</h2><p class="sub">'+esc(description)+'</p></div>';
  }
  function accountOptions(){
    return '<option value="">All accounts</option>'+accounts.map(function(a){ return '<option value="'+esc(a.id)+'">'+esc(a.label)+'</option>'; }).join('');
  }
  function accountLabel(id){ var a=accounts.find(function(row){return row.id===id;}); return a?a.label:'Telegram account'; }
  function readableDate(value){ if(!value) return '—'; var d=new Date(value); return isNaN(d.getTime())?'—':d.toLocaleString(); }
  function emptyState(title,detail){ return '<div class="empty-state"><strong>'+esc(title)+'</strong>'+esc(detail)+'</div>'; }
  function loadError(el,error){ if(el) el.innerHTML='<div class="workspace-error" role="alert">'+esc(error && error.message || error || 'Unable to load data.')+'<p class="note">Refresh to retry. If the table is unavailable, apply the workspace migration and check operator access.</p></div>'; }
  function showWorkspace(name,noHistory){
    if(name==='inbox'){ showInbox(noHistory); return; }
    selectedId=null; activeChat=null; workspaceEpoch++; chatEpoch++;
    if(name==='contacts') showContacts(noHistory);
    else if(name==='activity') showActivity(noHistory);
    else if(name==='knowledge') showKnowledge(noHistory);
    else if(name==='settings') showSettings(noHistory);
    updateSidebar();
  }
  function showInbox(noHistory,accountId,chatId){
    selectedId=null; activeChat=null; workspaceEpoch++; chatEpoch++;
    var html='<div class="workspace-tools"><span class="pill"><span class="dot ok"></span>Telegram inbox</span><select id="inbox-account" aria-label="Filter inbox by account">'+accountOptions()+'</select><label><input type="checkbox" id="inbox-unread">Unread only</label><label><input type="checkbox" id="inbox-archive">Include archived</label></div>';
    html+='<div class="inbox-shell" id="inbox-shell"><section class="conversation-column" aria-label="Conversations"><div class="workspace-tools"><input id="inbox-search" type="text" placeholder="Search chats…" aria-label="Search chats" autocomplete="off"></div><div class="conversation-list" id="conversation-list">'+emptyState('Loading conversations','Reading the latest synchronized inbox…')+'</div><button class="btn sm hidden" id="inbox-more">Load more chats</button></section><section class="chat-panel" id="chat-panel" aria-label="Conversation history">'+emptyState('Your conversations, together','Choose a chat to see its synchronized history. Connect a Telegram account from the sidebar to get started.')+'</section></div>';
    showPane('inbox',html,'Inbox',noHistory?{noHistory:true}:undefined); updateSidebar();
    $('inbox-account').value=inboxFilters.account;
    $('inbox-search').value=inboxFilters.search;
    $('inbox-unread').checked=inboxFilters.unread;
    $('inbox-archive').checked=inboxFilters.archive;
    ['inbox-account','inbox-unread','inbox-archive'].forEach(function(id){ $(id).addEventListener('change',function(){ inboxFilters.account=$('inbox-account').value; inboxFilters.unread=$('inbox-unread').checked; inboxFilters.archive=$('inbox-archive').checked; loadChats(false); }); });
    var searchTimer;
    $('inbox-search').addEventListener('input',function(){ inboxFilters.search=this.value; clearTimeout(searchTimer); searchTimer=setTimeout(function(){loadChats(false);},250); });
    $('inbox-more').onclick=function(){loadChats(true);};
    $('refresh-btn').onclick=function(){ loadChats(false); if(activeChat) loadMessages(false); refreshAccounts(); };
    var restoreEpoch=workspaceEpoch,restoreSelectionEpoch=chatEpoch;
    loadChats(false).then(function(){ if(accountId && chatId && restoreEpoch===workspaceEpoch && restoreSelectionEpoch===chatEpoch && currentPane==='inbox') restoreConversation(accountId,chatId,restoreEpoch); });
  }
  // Browser Back must restore a selected chat even if it is beyond page one
  // or excluded by the current list filters. RLS still controls this lookup.
  async function restoreConversation(accountId,chatId,epoch){
    var restoreChatEpoch=chatEpoch;
    var row=inboxRows.find(function(c){return c.account_id===accountId && String(c.chat_id)===String(chatId);});
    if(!row){
      var r=await sb.from('open_tgate_tg_chats').select('account_id,chat_id,title,kind,unread_count,is_marked_unread,last_message,last_message_at,is_archived,synced_at,history_complete,history_note,recent_complete,recent_note,is_visible').eq('is_visible',true).eq('account_id',accountId).eq('chat_id',String(chatId)).limit(1);
      if(epoch!==workspaceEpoch || restoreChatEpoch!==chatEpoch || currentPane!=='inbox')return;
      if(r.error){loadError($('chat-panel'),r.error);return;}
      row=(r.data||[])[0];
    }
    if(epoch!==workspaceEpoch || restoreChatEpoch!==chatEpoch || currentPane!=='inbox')return;
    if(row)openConversation(row,true);
    else $('chat-panel').innerHTML=emptyState('Conversation unavailable','This conversation is no longer synchronized or accessible to your account.');
  }
  var inboxRequest = 0;
  async function loadChats(append){
    var epoch=workspaceEpoch, request=++inboxRequest;
    if(!append){inboxOffset=0;inboxRows=[];}
    var q=sb.from('open_tgate_tg_chats').select('account_id,chat_id,title,kind,unread_count,is_marked_unread,last_message,last_message_at,is_archived,synced_at,history_complete,history_note,recent_complete,recent_note,is_visible').eq('is_visible',true).order('last_message_at',{ascending:false,nullsFirst:false}).order('chat_id',{ascending:false}).order('account_id',{ascending:true}).range(inboxOffset,inboxOffset+49);
    if(inboxFilters.account) q=q.eq('account_id',inboxFilters.account);
    if(inboxFilters.unread) q=q.or('unread_count.gt.0,is_marked_unread.eq.true');
    if(!inboxFilters.archive) q=q.eq('is_archived',false);
    if(inboxFilters.search.trim()) q=q.ilike('title','%'+inboxFilters.search.trim()+'%');
    var r=await q;
    if(epoch!==workspaceEpoch || request!==inboxRequest || currentPane!=='inbox') return;
    if(r.error){loadError($('conversation-list'),r.error);return;}
    var rows=r.data||[];
    // Revalidate previously loaded pages before retaining them. Telegram may
    // remove a chat from both supported lists while its history stays stored.
    if(append && inboxRows.length){
      var groups=new Map(),visibleKeys=new Set();
      inboxRows.forEach(function(c){var ids=groups.get(c.account_id)||[];ids.push(String(c.chat_id));groups.set(c.account_id,ids);});
      for(var group of groups){
        for(var start=0;start<group[1].length;start+=100){
          var checked=await sb.from('open_tgate_tg_chats').select('account_id,chat_id,is_visible').eq('account_id',group[0]).in('chat_id',group[1].slice(start,start+100)).limit(100);
          if(epoch!==workspaceEpoch || request!==inboxRequest || currentPane!=='inbox')return;
          if(checked.error){loadError($('conversation-list'),checked.error);return;}
          (checked.data||[]).forEach(function(c){if(c.is_visible)visibleKeys.add(c.account_id+':'+c.chat_id);});
        }
      }
      inboxRows=inboxRows.filter(function(c){return visibleKeys.has(c.account_id+':'+c.chat_id);});
    }
    inboxRows=inboxRows.concat(rows.filter(function(c){return c.is_visible!==false;})); inboxOffset+=rows.length;
    $('inbox-more').classList.toggle('hidden',rows.length<50);
    if(activeChat){
      var current=activeChat,refreshed=inboxRows.find(function(c){return c.account_id===current.account_id && String(c.chat_id)===String(current.chat_id);});
      if(refreshed)activeChat=refreshed;
      else {
        var checked=await sb.from('open_tgate_tg_chats').select('account_id,chat_id,is_visible,history_complete,history_note,recent_complete,recent_note').eq('account_id',current.account_id).eq('chat_id',String(current.chat_id)).limit(1);
        if(epoch!==workspaceEpoch || request!==inboxRequest || currentPane!=='inbox')return;
        if(activeChat && activeChat.account_id===current.account_id && String(activeChat.chat_id)===String(current.chat_id)){
          if(checked.error){loadError($('chat-panel'),checked.error);}
          else if(!(checked.data||[])[0] || checked.data[0].is_visible===false){
            activeChat=null;messageRows=[];chatEpoch++;updateBack();$('inbox-shell').classList.remove('chat-open');
            $('chat-panel').innerHTML=emptyState('Conversation removed','Telegram removed this chat from the supported lists. Its stored history remains available to authorized backend tools.');
          }else{activeChat=Object.assign({},current,checked.data[0]);}
        }
      }
    }
    renderConversationList();
  }
  function renderConversationList(){
    var host=$('conversation-list');if(!host)return;
    host.innerHTML=inboxRows.length?inboxRows.map(function(c,i){
      var chosen=activeChat && c.account_id===activeChat.account_id && String(c.chat_id)===String(activeChat.chat_id);
      return '<button class="conversation-row'+(chosen?' active':'')+'" data-chat-index="'+i+'" aria-pressed="'+(chosen?'true':'false')+'"><span class="chat-avatar" aria-hidden="true">'+esc((c.title||'?').slice(0,1).toUpperCase())+'</span><span class="conversation-copy"><span class="conversation-title" style="display:block">'+esc(c.title||'Untitled conversation')+'</span><span class="conversation-preview" style="display:block">'+esc(c.last_message||'No message preview')+'</span><span class="conversation-account" style="display:block">'+esc(accountLabel(c.account_id))+' · '+esc(c.kind||'chat')+(c.is_archived?' · Archived':'')+'</span></span>'+(c.unread_count||c.is_marked_unread?'<span class="unread-badge" aria-label="Unread">'+esc(c.unread_count>99?'99+':c.unread_count||'•')+'</span>':'')+'</button>';
    }).join(''):emptyState('No conversations yet','Clear your filters or connect an account. Chats appear after the backend synchronizes Telegram.');
    host.querySelectorAll('[data-chat-index]').forEach(function(el){el.onclick=function(){openConversation(inboxRows[Number(el.getAttribute('data-chat-index'))]);};});
  }
  function openConversation(chat,noHistory){
    activeChat=chat; messageRows=[]; chatEpoch++; updateBack(); renderConversationList();
    $('inbox-shell').classList.add('chat-open');
    $('chat-panel').innerHTML='<div class="chat-header"><button class="btn sm chat-mobile-back" id="chat-back" aria-label="Back to conversations">←</button><div><h2>'+esc(chat.title||'Conversation')+'</h2><p class="sub">'+esc(accountLabel(chat.account_id))+' · Last sync '+esc(readableDate(chat.synced_at))+'</p></div></div><div class="chat-scroll" id="message-list" aria-label="Synchronized messages">'+emptyState('Loading history','Reading synchronized messages…')+'</div><div class="chat-foot"><span id="history-note">Read-only synchronized history</span><button class="btn sm" id="chat-draft">✦ Draft with AI</button></div>';
    $('chat-back').onclick=function(){ activeChat=null;chatEpoch++;$('inbox-shell').classList.remove('chat-open');renderConversationList();updateBack();history.replaceState({otgPane:'inbox'},'',location.pathname+location.search+'#inbox'); };
    $('chat-draft').onclick=function(){var context={account_id:chat.account_id,chat_id:String(chat.chat_id)};showWorkspace('knowledge');aiContext=context;$('ai-query').focus();};
    var state={otgPane:'inbox',chatAccountId:chat.account_id,chatId:String(chat.chat_id)};
    if(noHistory)history.replaceState(state,'',location.pathname+location.search+'#inbox');
    else history.pushState(state,'',location.pathname+location.search+'#inbox');
    loadMessages(false);
  }
  var messageRequest=0;
  async function loadMessages(older){
    if(!activeChat)return;var chat=activeChat,epoch=chatEpoch,request=++messageRequest,cachedRows=messageRows.slice(),previousHasMore=messagesHaveMore;
    var q=sb.from('open_tgate_tg_messages').select('message_id::text,text,content_type,sender_id,is_outgoing,sent_at,edited_at,deleted,meta').eq('account_id',chat.account_id).eq('chat_id',String(chat.chat_id)).order('message_id',{ascending:false}).limit(50);
    if(older && messageRows.length)q=q.lt('message_id',messageRows[0].message_id);
    var r=await q;
    if(epoch!==chatEpoch || request!==messageRequest || currentPane!=='inbox')return;
    if(r.error){loadError($('message-list'),r.error);return;}
    var rows=(r.data||[]).reverse();
    // Revalidate every displayed older ID: RLS hides deletion tombstones, so
    // retaining rows absent from a fresh query would keep deleted text on screen.
    var visibleCached=[];
    if(!older && cachedRows.length){
      var latestIds=new Set(rows.map(function(m){return String(m.message_id);}));
      var cachedIds=cachedRows.filter(function(m){return !latestIds.has(String(m.message_id));}).map(function(m){return String(m.message_id);});
      for(var start=0;start<cachedIds.length;start+=100){
        var checked=await sb.from('open_tgate_tg_messages').select('message_id::text,text,content_type,sender_id,is_outgoing,sent_at,edited_at,deleted,meta').eq('account_id',chat.account_id).eq('chat_id',String(chat.chat_id)).in('message_id',cachedIds.slice(start,start+100)).limit(100);
        if(epoch!==chatEpoch || request!==messageRequest || currentPane!=='inbox')return;
        if(checked.error){messageRows=[];loadError($('message-list'),checked.error);return;}
        visibleCached=visibleCached.concat(checked.data||[]);
      }
    }
    messagesHaveMore=!older && cachedRows.length?(previousHasMore || rows.length===50):rows.length===50;
    if(older) messageRows=rows.concat(messageRows);
    else { messageRows=visibleCached.concat(rows); messageRows.sort(function(a,b){var left=BigInt(a.message_id),right=BigInt(b.message_id);return left<right?-1:left>right?1:0;}); }
    renderMessages(older);
  }
  function renderMessages(older){
    var host=$('message-list');if(!host)return;var oldHeight=host.scrollHeight,oldTop=host.scrollTop,nearBottom=host.scrollHeight-host.scrollTop-host.clientHeight<100;
    var html=messagesHaveMore?'<button class="btn sm" id="history-more" style="align-self:center">Load earlier messages</button>':'';
    html+=messageRows.length?messageRows.map(function(m){
      var body=m.deleted?'Message deleted':m.text||'['+(m.content_type||'Message without text')+']';
      return '<article class="message-bubble'+(m.is_outgoing?' outgoing':'')+'"><div>'+esc(body)+'</div><div class="message-meta">'+esc(m.is_outgoing?'Sent':m.sender_id?'Sender '+m.sender_id:'Received')+' · '+esc(readableDate(m.sent_at))+(m.edited_at?' · Edited':'')+'</div></article>';
    }).join(''):emptyState('History is still synchronizing','The backend will add messages as it reads this account. Refresh to check progress.');
    host.innerHTML=html;
    if($('history-more'))$('history-more').onclick=function(){loadMessages(true);};
    host.scrollTop=older?oldTop+host.scrollHeight-oldHeight:(nearBottom||oldHeight===0?host.scrollHeight:oldTop);
    $('history-note').textContent=chatSyncProgress(activeChat);
  }
  function chatSyncProgress(chat){
    var account=accounts.find(function(a){return a.id===chat.account_id;});
    if(account && account.account_type==='bot')return 'Bot history is limited to received updates · Read-only';
    var pending=[];
    if(!chat.recent_complete)pending.push('Recent messages catching up'+(chat.recent_note?' — '+chat.recent_note:''));
    if(!chat.history_complete)pending.push('Older history sync in progress'+(chat.history_note?' — '+chat.history_note:''));
    return (pending.length?pending.join(' · '):'Available history synchronized')+' · Read-only';
  }
  function showContacts(noHistory){
    contactsOffset=0;
    showPane('contacts',workspaceIntro('Telegram directory','Contacts','Browse contacts synchronized from your connected accounts.')+'<div class="workspace-tools"><select id="contacts-account" aria-label="Filter contacts by account">'+accountOptions()+'</select><input id="contacts-search" type="text" placeholder="Search contact names…" aria-label="Search contacts"></div><div class="card"><div id="contacts-list"></div><button class="btn sm hidden" id="contacts-more" style="margin-top:12px">Load more contacts</button></div>','Contacts',noHistory?{noHistory:true}:undefined);
    $('contacts-account').onchange=function(){loadContacts(false);};var timer;
    $('contacts-search').oninput=function(){clearTimeout(timer);timer=setTimeout(function(){loadContacts(false);},250);};
    $('contacts-more').onclick=function(){loadContacts(true);};$('refresh-btn').onclick=function(){loadContacts(false);refreshAccounts();};loadContacts(false);
  }
  var contactRequest=0;
  async function loadContacts(append){
    var epoch=workspaceEpoch,request=++contactRequest;if(!append)contactsOffset=0;
    var q=sb.from('open_tgate_tg_entities').select('account_id,tg_id,title,username,meta').eq('kind','contact').order('title',{ascending:true}).order('tg_id',{ascending:true}).order('account_id',{ascending:true}).range(contactsOffset,contactsOffset+99);
    var account=$('contacts-account').value,search=$('contacts-search').value.trim();
    if(account)q=q.eq('account_id',account);if(search)q=q.ilike('title','%'+search+'%');
    var r=await q;if(epoch!==workspaceEpoch||request!==contactRequest||currentPane!=='contacts')return;
    if(r.error){loadError($('contacts-list'),r.error);return;}
    var rows=r.data||[],html=rows.map(function(c){return '<div class="entity-row"><span class="chat-avatar" aria-hidden="true">'+esc((c.title||'?')[0])+'</span><span class="ename">'+esc(c.title||'Unnamed contact')+'<span class="source-meta" style="display:block">'+esc(accountLabel(c.account_id))+'</span></span><span class="euser">'+esc(c.username?'@'+c.username:'')+'</span><span class="emeta">'+esc(c.meta && c.meta.phone_masked || '')+'</span></div>';}).join('');
    if(append)$('contacts-list').insertAdjacentHTML('beforeend',html);else $('contacts-list').innerHTML=html||emptyState('No contacts found','Contacts appear after Telegram sync. Try another account or search.');
    contactsOffset+=rows.length;$('contacts-more').classList.toggle('hidden',rows.length<100);
  }
  function showActivity(noHistory){
    showPane('activity',workspaceIntro('Account monitoring','Activity & sync','Current account connections and backend health. Only synchronized state is shown.')+renderTemplate('tmpl-dashboard')+'<div class="card" style="margin-top:18px"><h2>Account activity</h2><p class="sub">Connection and sync progress across all backends.</p><div id="activity-accounts"></div></div>','Activity',noHistory?{noHistory:true}:undefined);
    renderActivity();loadHeartbeats();$('refresh-btn').onclick=function(){refreshAccounts().then(renderActivity);loadHeartbeats();};
  }
  function renderActivity(){
    var host=$('activity-accounts');if(!host)return;
    host.innerHTML=accounts.length?accounts.map(function(a){return '<div class="source-row"><div class="source-title">'+esc(a.label)+' <span class="pill"><span class="dot '+statusDot(a.status)+'"></span>'+esc(STATUS_LABEL[a.status]||a.status)+'</span></div><p class="source-meta">Connection: '+esc(a.connection_state||'Not reported')+' · Last activity '+esc(readableDate(a.last_activity_at))+' · Last account update '+esc(readableDate(a.updated_at))+'</p>'+renderSyncStepper(a.sync_step)+renderEntityCounts(a.entity_counts)+(a.last_error?'<p class="workspace-error">'+esc(a.last_error)+'</p>':'')+'</div>';}).join(''):emptyState('No accounts connected','Connect a Telegram account from the sidebar to monitor its activity.');
  }
  async function workspaceAPI(path,options){
    var auth=await sb.auth.getSession(),session=auth.data && auth.data.session;
    if(!session || !session.access_token)throw new Error('Your session expired. Sign in again.');
    options=options||{};var r=await fetch('/api/v1/workspace'+path,{method:options.method||'GET',headers:{'Authorization':'Bearer '+session.access_token,'Content-Type':'application/json'},body:options.body?JSON.stringify(options.body):undefined});
    if(r.status===204 && r.ok)return {};
    var data;try{data=await r.json();}catch(e){throw new Error('The workspace API is unavailable. Check the backend deployment.');}
    if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:data.message||'Workspace API request failed ('+r.status+').');return data;
  }
  var aiContext=null,knowledgeOffset=0,keysOffset=0,knowledgeRequest=0,keysRequest=0;
  function showKnowledge(noHistory){
    aiContext=null;knowledgeOffset=0;
    var html=workspaceIntro('Knowledge & assistance','AI knowledge','Add approved business facts, then draft replies grounded in those sources. Drafts are never sent automatically.');
    html+='<div class="workspace-grid"><div class="workspace-stack"><section class="card"><h2>Add a source</h2><p class="sub">Plain text and UTF-8 TXT or Markdown files up to 1 MiB and 100,000 characters.</p><form id="knowledge-form"><label for="source-title">Source title</label><input id="source-title" type="text" required maxlength="160" placeholder="e.g. Support policy"><label for="source-file" style="margin-top:12px">Import TXT / Markdown (optional)</label><input id="source-file" type="file" accept=".txt,.md,text/plain,text/markdown"><label for="source-content" style="margin-top:12px">Approved facts and guidance</label><textarea id="source-content" required maxlength="100000" placeholder="Paste product details, policies, FAQs, or support guidance…"></textarea><div class="row"><label><input id="source-approved" type="checkbox" checked> Approved for AI use</label><button class="btn primary" type="submit" id="source-save">Add source</button></div></form><div class="msg" id="knowledge-msg"></div></section><section class="card"><h2>Sources</h2><p class="sub">Only approved, enabled sources ground AI drafts.</p><div id="knowledge-sources"></div></section></div><section class="card"><h2>Draft with AI</h2><p class="sub">Ask for a reply using your approved knowledge. Configure an Open-Connect model gateway to enable drafts.</p><form id="ai-form"><label for="ai-query">Your question or reply instructions</label><textarea id="ai-query" required maxlength="4000" placeholder="What would you like to draft?"></textarea><div class="row"><button class="btn primary" id="ai-generate" type="submit">Generate draft</button></div></form><div class="msg" id="ai-msg"></div><div id="ai-result" style="margin-top:14px" aria-live="polite"></div></section></div>';
    showPane('knowledge',html,'AI knowledge',noHistory?{noHistory:true}:undefined);
    $('source-file').onchange=async function(){var file=this.files[0];if(!file)return;if(file.size>1048576||!/[.](txt|md)$/i.test(file.name)){msg('knowledge-msg','Choose a TXT or Markdown file up to 1 MiB and 100,000 characters.','err');this.value='';return;}try{var content=await file.text();if(Array.from(content).length>100000){msg('knowledge-msg','This file exceeds the 100,000 character limit. Shorten it before importing.','err');this.value='';return;}$('source-content').value=content;if(!$('source-title').value)$('source-title').value=file.name.slice(0,160);}catch(e){msg('knowledge-msg','Unable to read this file. Paste the text instead.','err');}};
    $('knowledge-form').onsubmit=async function(e){e.preventDefault();if(Array.from($('source-content').value.trim()).length>100000){msg('knowledge-msg','Sources must be 100,000 characters or fewer. Shorten the content and retry.','err');return;}var epoch=workspaceEpoch,btn=$('source-save');btn.disabled=true;clearMsg('knowledge-msg');try{await workspaceAPI('/knowledge',{method:'POST',body:{title:$('source-title').value.trim(),content:$('source-content').value.trim(),source_type:$('source-file').files.length?'file':'text',approved:$('source-approved').checked,enabled:true}});if(epoch!==workspaceEpoch)return;$('knowledge-form').reset();knowledgeOffset=0;msg('knowledge-msg','Source saved.','ok');loadKnowledge();}catch(error){if(epoch===workspaceEpoch)msg('knowledge-msg',error.message,'err');}finally{btn.disabled=false;}};
    $('ai-form').onsubmit=generateDraft;$('refresh-btn').onclick=loadKnowledge;loadKnowledge();
  }
  async function loadKnowledge(){
    var epoch=workspaceEpoch,offset=knowledgeOffset,request=++knowledgeRequest;try{var r=await workspaceAPI('/knowledge?limit=100&offset='+offset);if(epoch!==workspaceEpoch||request!==knowledgeRequest||currentPane!=='knowledge')return;
    var sources=r.sources||[];
    $('knowledge-sources').innerHTML=sources.length?sources.map(function(s){return '<div class="source-row"><div class="source-title">'+esc(s.title)+'</div><div class="source-meta">'+esc(s.source_type||'text')+' · '+(s.approved?'Approved':'Pending approval')+' · '+(s.enabled?'Enabled':'Disabled')+'</div><p class="source-excerpt">'+esc((s.content||'').slice(0,180))+'</p><div class="source-actions"><button class="btn sm" data-source-toggle="'+esc(s.id)+'" data-enabled="'+(s.enabled?'true':'false')+'">'+(s.enabled?'Disable':'Enable')+'</button><button class="btn sm" data-source-approve="'+esc(s.id)+'" data-approved="'+(s.approved?'true':'false')+'">'+(s.approved?'Remove approval':'Approve')+'</button><button class="btn sm danger" data-source-delete="'+esc(s.id)+'">Delete</button></div></div>';}).join(''):emptyState('No knowledge sources yet','Add approved business facts to ground your AI drafts.');
    $('knowledge-sources').innerHTML+=workspacePagination('knowledge',offset,r.has_more);
    bindWorkspacePagination('knowledge',offset,r.next_offset,loadKnowledge);
    $('knowledge-sources').querySelectorAll('[data-source-toggle],[data-source-approve],[data-source-delete]').forEach(function(btn){btn.onclick=async function(){var id=btn.getAttribute('data-source-toggle')||btn.getAttribute('data-source-approve')||btn.getAttribute('data-source-delete'),body={},method='PATCH';if(btn.hasAttribute('data-source-delete')){if(!confirm('Delete this knowledge source?'))return;method='DELETE';}else if(btn.hasAttribute('data-source-toggle'))body.enabled=btn.getAttribute('data-enabled')!=='true';else body.approved=btn.getAttribute('data-approved')!=='true';btn.disabled=true;try{await workspaceAPI('/knowledge/'+encodeURIComponent(id),{method:method,body:method==='PATCH'?body:undefined});if(epoch===workspaceEpoch)loadKnowledge();}catch(error){if(epoch===workspaceEpoch)msg('knowledge-msg',error.message,'err');}finally{btn.disabled=false;}};});
    }catch(error){if(epoch===workspaceEpoch && request===knowledgeRequest && currentPane==='knowledge')loadError($('knowledge-sources'),error);}
  }
  async function generateDraft(e){
    e.preventDefault();var epoch=workspaceEpoch,btn=$('ai-generate');btn.disabled=true;msg('ai-msg','Generating a grounded draft…','info');
    try{var body={query:$('ai-query').value.trim()};if(aiContext){body.account_id=aiContext.account_id;body.chat_id=aiContext.chat_id;}var r=await workspaceAPI('/ai/draft',{method:'POST',body:body});if(epoch!==workspaceEpoch)return;
      if(!r.configured){msg('ai-msg',r.message||'AI is not configured. Configure the Open-Connect model gateway, then retry.','err');$('ai-result').innerHTML='';return;}
      if(!r.draft){msg('ai-msg',r.message||'No draft was returned. Add approved knowledge and retry.','info');$('ai-result').innerHTML='';return;}
      clearMsg('ai-msg');$('ai-result').innerHTML='<label for="ai-draft">Review your draft</label><textarea id="ai-draft" readonly></textarea><div class="row"><button class="btn sm" id="draft-copy">Copy draft</button></div><p class="note">Sources: '+esc((r.sources||[]).map(function(s){return s.title;}).join(', ')||'No matching sources')+'</p>';$('ai-draft').value=r.draft||'';$('draft-copy').onclick=function(){copyText($('ai-draft').value,'ai-msg');};
    }catch(error){if(epoch===workspaceEpoch)msg('ai-msg',error.message,'err');}finally{btn.disabled=false;}
  }
  function showSettings(noHistory){
    keysOffset=0;
    var html=workspaceIntro('Workspace settings','API & MCP','Connect trusted tools to your synchronized Telegram workspace with scoped access.');
    html+='<div class="workspace-stack"><section class="card"><h2>API keys</h2><p class="sub">Create a read-only key for inbox access and approved AI knowledge. A key is shown once; store it securely.</p><form id="key-form"><label for="key-name">Key name</label><input id="key-name" type="text" required maxlength="80" placeholder="e.g. Open-Connect agent"><div class="row"><label><input id="key-read" type="checkbox" checked> Read inbox, contacts and activity</label><label><input id="key-knowledge" type="checkbox" checked> Read approved knowledge</label></div><div class="row"><button class="btn primary" id="key-create" type="submit">Create key</button></div></form><div class="msg" id="key-msg"></div><div class="hidden" id="key-secret-wrap" style="margin-top:12px"><label for="key-secret">Copy this key now — it will not be displayed again</label><input id="key-secret" type="password" readonly autocomplete="off"><div class="row"><button class="btn sm" id="key-copy">Copy key</button><button class="btn sm" id="key-hide">Dismiss key</button></div></div><div id="key-list" style="margin-top:16px"></div></section><section class="card"><h2>Connect via MCP</h2><p class="sub">Use the server URL and supply your scoped API key in the Authorization header.</p><code class="workspace-code" id="mcp-url">https://open-tgate.site/mcp</code><div class="row"><button class="btn sm" id="mcp-copy">Copy MCP URL</button></div><code class="workspace-code" style="margin-top:12px">Authorization: Bearer YOUR_API_KEY</code><p class="note">A supported MCP client can read chats, message history, contacts, account activity, and approved knowledge. Sending is disabled.</p><a href="https://github.com/hillstreet-ph/open-tgate/blob/master/docs/INBOX_WORKSPACE.md" target="_blank" rel="noopener">Open API documentation ↗</a><div class="msg" id="mcp-msg"></div></section></div>';
    showPane('settings',html,'Settings · API / MCP',noHistory?{noHistory:true}:undefined);
    $('key-form').onsubmit=async function(e){e.preventDefault();var scopes=[];if($('key-read').checked)scopes.push('read');if($('key-knowledge').checked)scopes.push('knowledge:read');if(!scopes.length){msg('key-msg','Choose at least one permission.','err');return;}var epoch=workspaceEpoch,btn=$('key-create');btn.disabled=true;try{var r=await workspaceAPI('/keys',{method:'POST',body:{name:$('key-name').value.trim(),scopes:scopes}});if(epoch!==workspaceEpoch)return;$('key-secret').value=r.key||'';$('key-secret-wrap').classList.remove('hidden');msg('key-msg','Key created. Copy it before leaving this screen.','ok');keysOffset=0;loadKeys();}catch(error){if(epoch===workspaceEpoch)msg('key-msg',error.message,'err');}finally{btn.disabled=false;}};
    $('key-copy').onclick=function(){copyText($('key-secret').value,'key-msg');};$('key-hide').onclick=function(){$('key-secret').value='';$('key-secret-wrap').classList.add('hidden');};$('mcp-copy').onclick=function(){copyText($('mcp-url').textContent,'mcp-msg');};$('refresh-btn').onclick=loadKeys;loadKeys();
  }
  async function loadKeys(){
    var epoch=workspaceEpoch,offset=keysOffset,request=++keysRequest;try{var r=await workspaceAPI('/keys?limit=100&offset='+offset);if(epoch!==workspaceEpoch||request!==keysRequest||currentPane!=='settings')return;var keys=r.keys||[];
      $('key-list').innerHTML=keys.length?keys.map(function(k){return '<div class="source-row"><div class="source-title">'+esc(k.name)+'</div><p class="source-meta">'+esc(k.prefix)+'… · '+esc((k.scopes||[]).join(', '))+' · '+(k.revoked_at?'Revoked':'Active')+'</p>'+(k.revoked_at?'':'<button class="btn sm danger" data-key-revoke="'+esc(k.id)+'">Revoke key</button>')+'</div>';}).join(''):emptyState('No API keys yet','Create a scoped key to connect your tools.');
      $('key-list').innerHTML+=workspacePagination('keys',offset,r.has_more);
      bindWorkspacePagination('keys',offset,r.next_offset,loadKeys);
      $('key-list').querySelectorAll('[data-key-revoke]').forEach(function(btn){btn.onclick=async function(){if(!confirm('Revoke this API key? Connected tools using it will lose access.'))return;btn.disabled=true;try{await workspaceAPI('/keys/'+encodeURIComponent(btn.getAttribute('data-key-revoke'))+'/revoke',{method:'POST'});if(epoch===workspaceEpoch)loadKeys();}catch(error){if(epoch===workspaceEpoch)msg('key-msg',error.message,'err');}finally{btn.disabled=false;}};});
    }catch(error){if(epoch===workspaceEpoch && request===keysRequest && currentPane==='settings')loadError($('key-list'),error);}
  }
  function workspacePagination(prefix,offset,hasMore){
    if(!offset && !hasMore)return '';
    return '<div class="row" aria-label="'+esc(prefix)+' pagination">'+(offset?'<button class="btn sm" id="'+prefix+'-previous">← Previous</button>':'')+(hasMore?'<button class="btn sm" id="'+prefix+'-next">Next →</button>':'')+'</div>';
  }
  function bindWorkspacePagination(prefix,offset,nextOffset,loader){
    var previous=$(prefix+'-previous'),next=$(prefix+'-next');
    if(previous)previous.onclick=function(){if(prefix==='knowledge')knowledgeOffset=Math.max(0,offset-100);else keysOffset=Math.max(0,offset-100);return loader();};
    if(next)next.onclick=function(){var cursor=nextOffset==null?offset+100:nextOffset;if(prefix==='knowledge')knowledgeOffset=cursor;else keysOffset=cursor;return loader();};
  }
  async function copyText(text,id){try{await navigator.clipboard.writeText(text);msg(id,'Copied.','ok');}catch(e){msg(id,'Clipboard unavailable. Select the value and copy it manually.','err');}}

  var lastWorkspacePoll=0;
  // ---- Polling ----
  async function refreshAccounts(){
    var r = await sb.from("open_tgate_tg_accounts")
      .select("id,label,status,needs,qr_link,last_error,phone_masked,tg_first_name,tg_last_name,tg_username,sync_step,entity_counts,updated_at,account_type,bot_token_hint,bot_username,bot_can_read_messages,notion_synced_at,notion_sync_error,connection_state,last_activity_at")
      .order("created_at",{ascending:true});
    if(r.error) return;
    accounts = r.data || [];
    updateSidebar();
    // Update stats if on dashboard
    var sa = $("stat-accounts"); if(sa) sa.textContent = accounts.length;
    // Keep the current DOM while an operator is entering a login field. Removing
    // a focused input closes the Android keyboard and loses the partially typed value.
    var paneBody = $("pane-body");
    var active = document.activeElement;
    if(paneBody && active && paneBody.contains(active) &&
       /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) return;
    if(currentPane==="activity"){renderActivity();loadHeartbeats();}
    if(currentPane==="inbox" && Date.now()-lastWorkspacePoll>15000){lastWorkspacePoll=Date.now();loadChats(false);if(activeChat)loadMessages(false);}
    // If viewing an account, refresh its detail in place (no new history entry)
    if(selectedId){
      var acc = accounts.find(function(a){ return a.id===selectedId; });
      if(acc && currentPane==="account-detail") renderAccountDetail(acc, "none");
    }
  }

  async function startPolling(){
    await refreshAccounts();
    if(tgTimer) clearInterval(tgTimer);
    tgTimer = setInterval(refreshAccounts, 3000);
    // Bind sidebar add buttons
    bindAddPersonalForm();
    bindAddBotForm();
  }

  // ---- Boot ----
  showPane("loading","",null,{noHistory:true});
  if(/(?:^|[#&?])type=recovery(?:&|$)/.test(window.location.hash || "")){
    recovering = true;
    sidebar.classList.add("hidden");
    showPane("recovery","",null,{noHistory:true}); bindRecoveryEvents();
  }
  sb.auth.getSession().then(function(res){ if(!recovering) renderFor(res.data.session); });
  sb.auth.onAuthStateChange(function(evt, session){
    if(evt === "PASSWORD_RECOVERY"){ recovering = true; sidebar.classList.add("hidden"); showPane("recovery","",null,{noHistory:true}); bindRecoveryEvents(); return; }
    if(recovering) return;
    renderFor(session);
  });
})();
</script>
</body>
</html>`;
