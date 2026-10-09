import test from 'node:test';
import assert from 'node:assert/strict';
import edge from '../src/index.js';
const env={API_BASE_URL:'https://backend.test'};

test('OAuth discovery reaches upstream without a bearer, not the landing page',async()=>{
  const old=globalThis.fetch;
  globalThis.fetch=async(url,opts)=>{assert.equal(url.pathname,'/.well-known/oauth-protected-resource/mcp');assert.equal(opts.headers.authorization,'');return Response.json({resource:'https://open-tgate.site/mcp'});};
  try{const r=await edge.fetch(new Request('https://open-tgate.site/.well-known/oauth-protected-resource/mcp'),env);assert.equal(r.status,200);assert.equal((await r.json()).resource,'https://open-tgate.site/mcp');assert.equal(r.headers.get('cache-control'),'no-store');}finally{globalThis.fetch=old;}
});
test('unauthenticated MCP returns the discovery challenge',async()=>{
 const r=await edge.fetch(new Request('https://open-tgate.site/mcp',{method:'POST'}),env);
 assert.equal(r.status,401);assert.match(r.headers.get('www-authenticate'),/oauth-protected-resource\/mcp/);
});
test('OAuth token accepts a bounded form and preserves backend errors',async()=>{
 const old=globalThis.fetch;
 globalThis.fetch=async(url,opts)=>{assert.equal(url.pathname,'/oauth/token');assert.equal(opts.headers['content-type'],'application/x-www-form-urlencoded');return Response.json({detail:'invalid_grant'},{status:400});};
 try{const r=await edge.fetch(new Request('https://open-tgate.site/oauth/token',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:'code=test'}),env);assert.equal(r.status,400);assert.equal((await r.json()).detail,'invalid_grant');}finally{globalThis.fetch=old;}
});
test('OAuth streamed bodies are bounded before forwarding',async()=>{
 const r=await edge.fetch(new Request('https://open-tgate.site/oauth/register',{method:'POST',body:'x'.repeat(17000)}),env);assert.equal(r.status,413);
});


test('public OAuth registration retains retry delay on shared admission rejection',async()=>{
 const old=globalThis.fetch;
 globalThis.fetch=async()=>Response.json({error:'registration_rate_limited'},{status:429,headers:{'retry-after':'60'}});
 try{const r=await edge.fetch(new Request('https://open-tgate.site/oauth/register',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}),env);assert.equal(r.status,429);assert.equal(r.headers.get('retry-after'),'60');assert.equal(r.headers.get('cache-control'),'no-store');}finally{globalThis.fetch=old;}
});
