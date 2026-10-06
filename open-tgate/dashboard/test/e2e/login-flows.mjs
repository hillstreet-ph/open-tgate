// Opt-in end-to-end check of the operator console's login flows.
//
// This drives the real app.js in a headless browser against a stubbed Supabase
// client, so it verifies the DOM each worker-written status produces and the
// exact command row each action queues. It is NOT part of `npm test` because it
// needs a Chromium binary; run it explicitly:
//
//   npm install --no-save puppeteer-core
//   PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium node test/e2e/login-flows.mjs
//
import puppeteer from "puppeteer-core";
import http from "node:http";
import process from "node:process";
import { appHtml } from "../../src/app.js";

const executablePath = process.env.PUPPETEER_EXECUTABLE_PATH || "/usr/bin/chromium";
const html = appHtml
  .replaceAll("%SUPABASE_URL%", "https://demo.supabase.co")
  .replaceAll("%SUPABASE_KEY%", "sb_publishable_demo")
  .replaceAll("%OPEN_CONNECT_URL%", "https://open-connect.site/connections");

// Minimal Supabase stub. window.__status / window.__accountType drive the single
// account the console renders; inserts are recorded on window.__commands.
const stub = `
window.__commands = [];
window.__status = "pending";
window.__accountType = "user";
window.qrcode = function(){ return { addData(){}, make(){}, createImgTag(){ return "<img alt=qr>"; } }; };
window.supabase = {
  createClient(){
    function accounts(){
      return [{ id:"11111111-1111-1111-1111-111111111111", label:"Support", status:window.__status,
        needs:null, qr_link: window.__status==="awaiting_qr_scan" ? "tg://login?token=abc" : null,
        last_error: window.__status==="error" ? "PHONE_NUMBER_INVALID" : null,
        phone_masked:null, tg_first_name:null, tg_last_name:null, tg_username:null, sync_step:null,
        entity_counts:{}, updated_at:new Date().toISOString(), account_type:window.__accountType,
        bot_token_hint:null, bot_username:null, bot_can_read_messages:false,
        notion_synced_at:null, notion_sync_error:null }];
    }
    function table(name){
      const q = { name };
      const api = {
        select(){ return api; }, eq(){ return api; }, order(){ return api; }, limit(){ return api; },
        single(){ return api; },
        insert(row){ window.__commands.push(row); return api; },
        update(row){ window.__commands.push({__update:row}); return api; },
        then(resolve){
          let data=[];
          if(name==="open_tgate_operators") data=[{email:"op@example.com",role:"operator",is_active:true}];
          else if(name==="open_tgate_tg_accounts") data=accounts();
          resolve({data, error:null});
        }
      };
      return api;
    }
    return { from: table, auth: {
      getSession: () => Promise.resolve({data:{session:{user:{email:"op@example.com"}}}}),
      onAuthStateChange: () => ({data:{subscription:{unsubscribe(){}}}}),
      signOut: () => Promise.resolve({error:null}) } };
  }
};
`;

const browser = await puppeteer.launch({
  executablePath, headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
});
const server = http.createServer((_req, res) => {
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  res.end(html);
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}/app`;

const results = [];
const errors = [];
const check = (name, ok, extra) => results.push({ name, ok, extra: extra || "" });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function open(status, accountType) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1100, height: 900 });
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  await page.evaluateOnNewDocument(stub);
  await page.evaluateOnNewDocument(`window.__status=${JSON.stringify(status)};window.__accountType=${JSON.stringify(accountType)};`);
  await page.setRequestInterception(true);
  page.on("request", (req) => { if (req.url().includes("jsdelivr")) req.abort(); else req.continue(); });
  await page.goto(base, { waitUntil: "domcontentloaded" });
  await wait(900);
  await page.click("#sb-acct-list .sb-item");
  await wait(400);
  return page;
}
const text = (p) => p.evaluate(() => document.querySelector("#pane-body").textContent);
const cmds = (p) => p.evaluate(() => window.__commands);

// awaiting_phone
let p = await open("awaiting_phone", "user");
check("awaiting_phone shows phone input", (await p.$("#phone-11111111-1111-1111-1111-111111111111")) !== null);
await p.type("#phone-11111111-1111-1111-1111-111111111111", "+15551234567");
await p.click('[data-act="phone-send"]');
await wait(400);
check("phone-send queues start_phone", (await cmds(p)).some((c) => c.action === "start_phone" && c.payload.phone_number === "+15551234567"));
await p.evaluate(() => { document.getElementById("phone-11111111-1111-1111-1111-111111111111").value = "abc"; });
await p.click('[data-act="phone-send"]');
await wait(200);
check("invalid phone is rejected inline", (await text(p)).includes("international format"));
await p.close();

// awaiting_code
p = await open("awaiting_code", "user");
check("awaiting_code shows code input", (await p.$("#code-11111111-1111-1111-1111-111111111111")) !== null);
await p.evaluate(() => { document.getElementById("code-11111111-1111-1111-1111-111111111111").value = "ab"; });
await p.click('[data-act="code"]');
await wait(200);
check("invalid code is rejected inline", (await text(p)).includes("numeric login code"));
await p.evaluate(() => { document.getElementById("code-11111111-1111-1111-1111-111111111111").value = "12345"; });
await p.click('[data-act="code"]');
await wait(300);
await p.click('[data-act="resend"]');
await wait(300);
check("submit_code queued", (await cmds(p)).some((c) => c.action === "submit_code" && c.payload.code === "12345"));
check("resend_code queued", (await cmds(p)).some((c) => c.action === "resend_code"));
await p.close();

// awaiting_password (2FA)
p = await open("awaiting_password", "user");
check("2FA password form", (await p.$("#pw-11111111-1111-1111-1111-111111111111")) !== null);
await p.click('[data-act="password"]');
await wait(200);
check("empty password is rejected inline", (await text(p)).includes("two-step verification password"));
await p.type("#pw-11111111-1111-1111-1111-111111111111", "s3cret-cloud-pw");
await p.click('[data-act="password"]');
await wait(300);
check("submit_password queued", (await cmds(p)).some((c) => c.action === "submit_password" && c.payload.password === "s3cret-cloud-pw"));
await p.close();

// awaiting_qr_scan
p = await open("awaiting_qr_scan", "user");
check("QR rendered", (await p.$(".qr img")) !== null);
check("QR link fallback shown", (await text(p)).includes("tg://login?token=abc"));
await p.close();

// validating_token (bot)
p = await open("validating_token", "bot");
check("bot validating state", (await text(p)).includes("Validating the bot token"));
await p.close();

// logged_out bot -> token form directly
p = await open("logged_out", "bot");
check("logged_out bot shows token form", (await p.$("#bottoken-11111111-1111-1111-1111-111111111111")) !== null);
await p.type("#bottoken-11111111-1111-1111-1111-111111111111", "123456:ABC-DEF1234567890abcdefghij");
await p.click('[data-act="bot-token"]');
await wait(300);
check("start_bot_token queued", (await cmds(p)).some((c) => c.action === "start_bot_token"));
await p.close();

// error -> picker with retry note and the last error
p = await open("error", "user");
check("error state shows picker + message", (await text(p)).includes("last attempt failed"));
check("last_error surfaced", (await text(p)).includes("PHONE_NUMBER_INVALID"));
await p.close();

// authorized -> disconnect
p = await open("authorized", "user");
check("authorized shows disconnect", (await p.$('[data-act="logout"]')) !== null);

// Back button + mobile navigation on the account detail pane
const title = () => p.evaluate(() => document.getElementById("pane-title").textContent);
check("back button visible on account detail", await p.evaluate(() => document.getElementById("back-btn").classList.contains("visible")));
await p.goBack();
await wait(600);
check("browser back lands on dashboard", (await title()) === "Open-TGate");
await p.click("#sb-acct-list .sb-item");
await wait(500);
check("reopened account detail", (await title()) === "Support");
await p.click("#back-btn");
await wait(600);
check("back button returns to dashboard", (await title()) === "Open-TGate");
check("back button hidden on dashboard", !(await p.evaluate(() => document.getElementById("back-btn").classList.contains("visible"))));
await p.setViewport({ width: 390, height: 800 });
await wait(300);
await p.click("#menu-toggle");
await wait(300);
check("mobile menu opens sidebar", await p.evaluate(() => document.getElementById("sidebar").classList.contains("open")));
// The sidebar sits over the left of the full-screen overlay; tap the exposed
// area on the right, which is how a user dismisses it.
await p.mouse.click(360, 400);
await wait(300);
check("mobile overlay closes sidebar", !(await p.evaluate(() => document.getElementById("sidebar").classList.contains("open"))));
await p.close();

await browser.close();
server.close();

const failed = results.filter((r) => !r.ok);
for (const r of results) console.log(`${r.ok ? "✓" : "✗"} ${r.name}${r.ok ? "" : " :: " + r.extra}`);
for (const e of errors) console.log("! " + e);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length || errors.length ? 1 : 0);
