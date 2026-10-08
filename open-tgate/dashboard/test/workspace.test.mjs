import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {appHtml} from '../src/app.js';

// Execute the shipped console functions with a deterministic DOM/query adapter.
// Requests and DB results stay local; no real Telegram account is accessed.
function harness() {
  const nodes=new Map(), queries=[], requests=[];
  const node=(id)=> {
    if(!nodes.has(id)) nodes.set(id,{id,innerHTML:'',value:'',textContent:'',checked:false,files:[],scrollHeight:0,scrollTop:0,clientHeight:500,tagName:'DIV',disabled:false,
      classList:{add(){},remove(){},toggle(){},contains(){return false;}},setAttribute(){},getAttribute(){},addEventListener(){},querySelectorAll(){return [];},contains(){return false;},focus(){},insertAdjacentHTML(_p,s){this.innerHTML+=s;},appendChild(){}});
    return nodes.get(id);
  };
  const context={document:{documentElement:node('root'),getElementById:node,activeElement:null,querySelectorAll(){return [];}},localStorage:{getItem(){return null;},setItem(){}},history:{state:null,pushState(){},replaceState(){}},location:{pathname:'/app',search:'',hash:'',origin:'https://open-tgate.site'},navigator:{clipboard:{writeText:async()=>{}}},confirm:()=>true,
    setTimeout,clearTimeout,setInterval,clearInterval,Date,Set,BigInt,console};
  context.window={innerWidth:1200,location:context.location,addEventListener(){},supabase:{createClient(){return {auth:{getSession(){return new Promise(()=>{});},onAuthStateChange(){}},from(name){
    const query={name,calls:[],result:{data:[],error:null}};queries.push(query);
    const chain={};for(const op of ['select','eq','order','range','limit','gt','lt','ilike','in'])chain[op]=(...args)=>{query.calls.push([op,...args]);return chain;};
    chain.then=(resolve)=>query.pending?query.pending.then(resolve):Promise.resolve(context.__dbResult?context.__dbResult(query):query.result).then(resolve);return chain;
  }}}}};
  context.fetch=async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({sources:[]})};};
  const script=[...appHtml.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script[^>]*>/gi)].map(m=>m[1]).find(s=>s.includes('function loadChats'));
  const instrumented=script.replace('  // ---- Boot ----',`globalThis.consoleTest={loadChats,loadMessages,loadKnowledge,generateDraft,workspaceAPI,refreshAccounts,showWorkspace,
    configure(values){if(values.pane)currentPane=values.pane;if(values.accounts)accounts=values.accounts;if(values.selectedId)selectedId=values.selectedId;if(values.chat)activeChat=values.chat;if(values.filters)inboxFilters=values.filters;if(values.rows)messageRows=values.rows;},
    navigate(){workspaceEpoch++;chatEpoch++;},messages(){return messageRows;},auth(session){sb.auth.getSession=async()=>({data:{session}});}};\n  // ---- Boot ----`).replace('%SUPABASE_URL%','https://example.supabase.co');
  vm.createContext(context);vm.runInContext(instrumented,context);
  return {api:context.consoleTest,nodes,node,queries,requests,context};
}
const turn=()=>new Promise(resolve=>setImmediate(resolve));

test('inbox filters and pagination read only account-scoped synced records',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'account-one',search:'Support',unread:true,archive:false}});
  const p=h.api.loadChats(false);const q=h.queries.at(-1);q.result.data=Array.from({length:50},(_,i)=>({account_id:'account-one',chat_id:String(i),title:'Support',last_message:'hello'}));await p;
  assert.equal(q.name,'open_tgate_tg_chats');assert.deepEqual(q.calls.find(c=>c[0]==='range'),['range',0,49]);
  assert.ok(q.calls.some(c=>c[0]==='eq'&&c[1]==='account_id'&&c[2]==='account-one'));
  assert.ok(q.calls.some(c=>c[0]==='gt'&&c[1]==='unread_count'));assert.ok(q.calls.some(c=>c[0]==='ilike'&&c[2]==='%Support%'));
  const next=h.api.loadChats(true);await next;assert.deepEqual(h.queries.at(-1).calls.find(c=>c[0]==='range'),['range',50,99]);
});

test('message pagination retains 64-bit cursors, escapes content, and filters both chat and account',async()=>{
  const h=harness();h.api.configure({pane:'inbox',chat:{account_id:'account-one',chat_id:'-10001',history_complete:false},rows:[{message_id:'9223372036854775000',text:'old'}]});
  const p=h.api.loadMessages(true);const q=h.queries.at(-1);q.result.data=[{message_id:'9223372036854774999',text:'<img src=x onerror=alert(1)>',sent_at:'2026-10-08T00:00:00Z'}];await p;
  assert.ok(q.calls.some(c=>c[0]==='lt'&&c[2]==='9223372036854775000'));
  assert.ok(q.calls.some(c=>c[0]==='eq'&&c[1]==='account_id'&&c[2]==='account-one'));
  assert.ok(q.calls.some(c=>c[0]==='eq'&&c[1]==='chat_id'&&c[2]==='-10001'));
  assert.equal(h.api.messages().length,2);assert.ok(h.node('message-list').innerHTML.includes('&lt;img'));assert.ok(!h.node('message-list').innerHTML.includes('<img'));
});

test('late inbox response cannot overwrite a newly navigated pane',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'',search:'',unread:false,archive:false}});
  const p=h.api.loadChats(false);h.api.navigate();h.node('conversation-list').innerHTML='new pane';await p;
  assert.equal(h.node('conversation-list').innerHTML,'new pane');
});

test('workspace management uses operator bearer auth and rejects expired sessions',async()=>{
  const h=harness();h.api.auth({access_token:'test-token'});await h.api.workspaceAPI('/knowledge',{method:'POST',body:{title:'Policy',content:'Approved facts'}});
  const r=h.requests[0];assert.equal(r.url,'/api/v1/workspace/knowledge');assert.equal(r.options.headers.Authorization,'Bearer test-token');assert.equal(r.options.headers['Content-Type'],'application/json');assert.equal(JSON.parse(r.options.body).title,'Policy');
  h.api.auth(null);await assert.rejects(h.api.workspaceAPI('/keys'),/session expired/i);assert.equal(h.requests.length,1);
});

test('knowledge content is rendered as text, including untrusted source titles',async()=>{
  const h=harness();h.api.configure({pane:'knowledge'});h.api.auth({access_token:'test-token'});
  h.context.fetch=async()=>({ok:true,json:async()=>({sources:[{id:'source-id',title:'<script>alert(1)</script>',content:'<iframe src=x>',approved:true,enabled:true}]})});
  await h.api.loadKnowledge();assert.ok(h.node('knowledge-sources').innerHTML.includes('&lt;script&gt;'));assert.ok(!h.node('knowledge-sources').innerHTML.includes('<iframe'));
});

test('API UI offers scoped read keys and header authentication without auto sending',()=>{
  assert.ok(appHtml.includes("scopes.push('knowledge:read')"));const endpoint=new URL(appHtml.match(/id="mcp-url">([^<]+)</)[1]);assert.equal(endpoint.origin,'https://open-tgate.site');assert.equal(endpoint.pathname,'/mcp');assert.ok(appHtml.includes('Authorization: Bearer YOUR_API_KEY'));
  assert.ok(!appHtml.includes('mcp?key='));assert.ok(appHtml.includes("if(!r.configured)"));assert.ok(appHtml.includes('Drafts are never sent automatically.'));
});


test('polling retains the focused Android login field and partially typed value',async()=>{
  const h=harness();h.api.configure({pane:'account-detail',selectedId:'account-one'});
  h.node('pane-body').innerHTML='existing phone form';const input=h.node('phone-input');input.tagName='INPUT';input.value='+639';
  h.context.document.activeElement=input;h.node('pane-body').contains=el=>el===input;
  const p=h.api.refreshAccounts();h.queries.at(-1).result.data=[{id:'account-one',label:'Support',status:'awaiting_phone'}];await p;
  assert.equal(input.value,'+639');assert.equal(h.node('pane-body').innerHTML,'existing phone form');
});

test('successful source deletion accepts HTTP 204 without an invalid JSON error',async()=>{
  const h=harness();h.api.auth({access_token:'test-token'});h.context.fetch=async()=>({ok:true,status:204,json:async()=>{throw new Error('No body');}});
  const result=await h.api.workspaceAPI('/knowledge/source-id',{method:'DELETE'});assert.equal(Object.keys(result).length,0);
});

test('unconfigured AI provides actionable status and never manufactures a draft',async()=>{
  const h=harness();h.api.configure({pane:'knowledge'});h.api.auth({access_token:'test-token'});h.node('ai-query').value='How do I request support?';
  h.context.fetch=async()=>({ok:true,status:200,json:async()=>({configured:false,draft:null,sources:[],message:'Configure AI_API_KEY and AI_MODEL on the API service.'})});
  await h.api.generateDraft({preventDefault(){}});assert.match(h.node('ai-msg').textContent,/Configure AI_API_KEY/);assert.equal(h.node('ai-result').innerHTML,'');assert.equal(h.node('ai-generate').disabled,false);
});


test('polling removes RLS-hidden deleted messages from latest and paginated older history',async()=>{
  const h=harness();h.api.configure({pane:'inbox',chat:{account_id:'account-one',chat_id:'-10001',history_complete:false},rows:[
    {message_id:'1',text:'deleted older private text'}, {message_id:'2',text:'retained older text'}, {message_id:'3',text:'deleted latest text'}]});
  h.context.__dbResult=q=>({error:null,data:q.calls.some(c=>c[0]==='in')?[{message_id:'2',text:'retained older text edited'}]:[{message_id:'4',text:'new message'}]});
  await h.api.loadMessages(false);const validation=h.queries.at(-1);
  assert.ok(validation.calls.some(c=>c[0]==='in'&&c[1]==='message_id'&&c[2].join(',')==='1,2,3'));
  // The RLS response returns only the retained older record; hidden IDs disappear.
  assert.deepEqual(Array.from(h.api.messages(),m=>m.message_id),['2','4']);
  assert.ok(h.node('message-list').innerHTML.includes('retained older text edited'));
  assert.ok(!h.node('message-list').innerHTML.includes('deleted older private text'));
  assert.ok(!h.node('message-list').innerHTML.includes('deleted latest text'));
});


test('earlier-page control appears when an initially short history grows to a full refreshed page',async()=>{
  const h=harness();h.api.configure({pane:'inbox',chat:{account_id:'account-one',chat_id:'-10001',history_complete:false},rows:[{message_id:'1',text:'initial short history'}]});
  h.context.__dbResult=q=>({error:null,data:q.calls.some(c=>c[0]==='in')?[{message_id:'1',text:'initial short history'}]:Array.from({length:50},(_,i)=>({message_id:String(51-i),text:'new synced message'}))});
  await h.api.loadMessages(false);
  assert.ok(h.node('message-list').innerHTML.includes('id="history-more"'));
  assert.equal(h.api.messages().length,51);
});
