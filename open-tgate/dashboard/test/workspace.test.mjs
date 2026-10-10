import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {appHtml} from '../src/app.js';

// Execute the shipped console functions with a deterministic DOM/query adapter.
// Requests and DB results stay local; no real Telegram account is accessed.
function harness(options={}) {
  const nodes=new Map(), queries=[], requests=[],windowEvents=new Map();
  const node=(id)=> {
    if(!nodes.has(id)) nodes.set(id,{id,innerHTML:'',value:'',textContent:'',checked:false,files:[],options:[],scrollHeight:0,scrollTop:0,clientHeight:500,tagName:'DIV',disabled:false,
      classList:{add(){},remove(){},toggle(){},contains(){return false;}},setAttribute(){},getAttribute(){},addEventListener(type,handler){this.events=this.events||{};this.events[type]=handler;},querySelectorAll(){return [];},contains(){return false;},focus(){},insertAdjacentHTML(_p,s){this.innerHTML+=s;},appendChild(){}});
    return nodes.get(id);
  };
  const context={document:{documentElement:node('root'),getElementById:node,activeElement:null,querySelectorAll(){return [];}},localStorage:{getItem(){return null;},setItem(){}},history:{state:null,pushState(state){this.state=state;},replaceState(state){this.state=state;}},URL,URLSearchParams,sessionStorage:options.storage||{getItem(){return null;},setItem(){},removeItem(){}},location:{href:options.href||'https://open-tgate.site/app',pathname:'/app',search:options.search||'',hash:'',origin:'https://open-tgate.site'},navigator:{clipboard:{writeText:async()=>{}}},confirm:()=>true,
    setTimeout,clearTimeout,setInterval(){return 1;},clearInterval(){},Date,Set,BigInt,console};
  context.window={innerWidth:1200,location:context.location,addEventListener(type,handler){windowEvents.set(type,handler);},supabase:{createClient(){return {auth:{getSession(){return new Promise(()=>{});},onAuthStateChange(){}},from(name){
    const query={name,calls:[],result:{data:[],error:null}};queries.push(query);
    const chain={};for(const op of ['select','eq','order','range','limit','gt','lt','ilike','in','or','contains'])chain[op]=(...args)=>{query.calls.push([op,...args]);return chain;};
    chain.then=(resolve)=>query.pending?query.pending.then(resolve):Promise.resolve(context.__dbResult?context.__dbResult(query):query.result).then(resolve);return chain;
  }}}}};
  context.fetch=async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({sources:[]})};};
  const script=[...appHtml.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script[^>]*>/gi)].map(m=>m[1]).find(s=>s.includes('function loadChats'));
  const instrumented=script.replace('  // ---- Boot ----',`globalThis.consoleTest={showOAuthConsent,oauthScopeDescription,renderFor,loadChats,renderConversationList,loadContacts,restoreConversation,openConversation,showKnowledge,loadKeys,showSettings,loadMessages,loadKnowledge,generateDraft,workspaceAPI,refreshAccounts,showWorkspace,showAccounts,renderFolderOptions,loadMessageActivity,renderActivityMessage,renderActivityFolders,setupMessageActivity,loadAudit,
    configure(values){if(values.pollAt!==undefined)lastWorkspacePoll=values.pollAt;if(values.authenticated)authenticated=true;if(values.pane)currentPane=values.pane;if(values.accounts)accounts=values.accounts;if(values.selectedId)selectedId=values.selectedId;if(values.chat)activeChat=values.chat;if(values.filters)inboxFilters=values.filters;if(values.rows)messageRows=values.rows;},
    navigate(){workspaceEpoch++;chatEpoch++;},epoch(){return workspaceEpoch;},chat(){return activeChat;},messages(){return messageRows;},chats(){return inboxRows;},auth(session){sb.auth.getSession=async()=>({data:{session}});},setRefresh(handler){sb.auth.refreshSession=handler;}};\n  // ---- Boot ----`).replace('%SUPABASE_URL%','https://example.supabase.co');
  vm.createContext(context);vm.runInContext(instrumented,context);
  return {api:context.consoleTest,nodes,node,queries,requests,context,windowEvents};
}
const turn=()=>new Promise(resolve=>setImmediate(resolve));

test('inbox filters and pagination read only account-scoped synced records',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'account-one',search:'Support',unread:true,archive:false}});
  const p=h.api.loadChats(false);const q=h.queries.at(-1);q.result.data=Array.from({length:50},(_,i)=>({account_id:'account-one',chat_id:String(i),title:'Support',last_message:'hello'}));await p;
  assert.equal(q.name,'open_tgate_tg_chats');assert.deepEqual(q.calls.find(c=>c[0]==='range'),['range',0,50]);
  assert.ok(q.calls.some(c=>c[0]==='eq'&&c[1]==='account_id'&&c[2]==='account-one'));
  assert.ok(q.calls.some(c=>c[0]==='or'&&c[1]==='unread_count.gt.0,is_marked_unread.eq.true'));assert.ok(q.calls.some(c=>c[0]==='ilike'&&c[2]==='%Support%'));
  const next=h.api.loadChats(true);await next;assert.ok(h.queries.some(q=>q.calls.some(c=>c[0]==='range'&&c[1]===0&&c[2]===100)));
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


test('browser Back restores an account-scoped chat absent from the first inbox page',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'',search:'',unread:false,archive:false}});
  h.context.__dbResult=q=>({error:null,data:q.name==='open_tgate_tg_chats'?[{account_id:'second-account',chat_id:'900',title:'Page two chat'}]:[]});
  await h.api.restoreConversation('second-account','900',h.api.epoch());
  const q=h.queries[0];assert.ok(q.calls.some(c=>c[0]==='eq'&&c[1]==='account_id'&&c[2]==='second-account'));
  assert.ok(q.calls.some(c=>c[0]==='eq'&&c[1]==='chat_id'&&c[2]==='900'));
  assert.equal(h.api.chat().title,'Page two chat');assert.ok(h.node('chat-panel').innerHTML.includes('Page two chat'));
  assert.equal(h.context.history.state.chatAccountId,'second-account');assert.equal(h.context.history.state.chatId,'900');
});

test('multi-account list pagination adds account_id to both total ordering keys',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'',search:'',unread:false,archive:false}});
  await h.api.loadChats(false);assert.deepEqual(h.queries.at(-1).calls.filter(c=>c[0]==='order').map(c=>c[1]),['last_message_at','chat_id','account_id']);
  h.api.configure({pane:'contacts'});await h.api.loadContacts(false);
  assert.deepEqual(h.queries.at(-1).calls.filter(c=>c[0]==='order').map(c=>c[1]),['title','tg_id','account_id']);
});

test('unread-only inbox includes manually marked chats with no unread messages',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'',search:'',unread:true,archive:false}});
  const p=h.api.loadChats(false);const q=h.queries.at(-1);q.result.data=[{account_id:'first-account',chat_id:'1',title:'Marked unread',unread_count:0,is_marked_unread:true}];await p;
  assert.ok(q.calls.some(c=>c[0]==='or'&&c[1]==='unread_count.gt.0,is_marked_unread.eq.true'));
  assert.ok(h.node('conversation-list').innerHTML.includes('aria-label="Unread"'));
  assert.ok(h.node('conversation-list').innerHTML.includes('Marked unread'));
});

test('text upload rejects over-limit characters even when below the byte limit',async()=>{
  const h=harness();h.api.showKnowledge();h.node('source-content').value='existing approved facts';
  const input=h.node('source-file');input.value='large.txt';input.files=[{name:'large.txt',size:100001,text:async()=> 'a'.repeat(100001)}];
  await input.onchange.call(input);assert.equal(h.node('source-content').value,'existing approved facts');assert.equal(input.value,'');assert.match(h.node('knowledge-msg').textContent,/100,000 character limit/);
  h.node('source-content').value='a'.repeat(100001);await h.node('knowledge-form').onsubmit({preventDefault(){}});
  assert.equal(h.requests.length,0);assert.match(h.node('knowledge-msg').textContent,/100,000 characters or fewer/);
});

test('text upload accepts exactly the server character limit',async()=>{
  const h=harness();h.api.showKnowledge();const input=h.node('source-file');input.files=[{name:'policy.md',size:100000,text:async()=> 'a'.repeat(100000)}];
  await input.onchange.call(input);assert.equal(h.node('source-content').value.length,100000);assert.equal(h.node('source-title').value,'policy.md');
});


test('both inbox and direct history restoration exclude chats removed from supported lists',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'',search:'',unread:false,archive:true}});
  await h.api.loadChats(false);assert.ok(h.queries[0].calls.some(c=>c[0]==='eq'&&c[1]==='is_visible'&&c[2]===true));
  await h.api.restoreConversation('first-account','77',h.api.epoch());
  assert.ok(h.queries.at(-1).calls.some(c=>c[0]==='eq'&&c[1]==='is_visible'&&c[2]===true));assert.equal(h.api.chat(),null);
});

test('refresh clears active removed chat and its cached message text without deleting stored records',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'',search:'',unread:false,archive:true},chat:{account_id:'first-account',chat_id:'77',title:'Removed chat'},rows:[{message_id:'1',text:'cached private text'}]});
  h.node('chat-panel').innerHTML='cached private text';
  h.context.__dbResult=q=>({error:null,data:q.calls.some(c=>c[0]==='range')?[]:[{account_id:'first-account',chat_id:'77',is_visible:false}]});
  await h.api.loadChats(false);assert.equal(h.api.chat(),null);assert.equal(h.api.messages().length,0);assert.ok(!h.node('chat-panel').innerHTML.includes('cached private text'));
});

test('loading another chat page prunes invisible rows cached on earlier pages',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'',search:'',unread:false,archive:true}});
  let first=true;h.context.__dbResult=q=>({error:null,data:q.calls.some(c=>c[0]==='in')?[{account_id:'first-account',chat_id:'1',is_visible:false}]:first?[{account_id:'first-account',chat_id:'1',title:'Removed from Telegram',is_visible:true}]:[{account_id:'first-account',chat_id:'2',title:'Visible next page',is_visible:true}]});
  await h.api.loadChats(false);first=false;await h.api.loadChats(true);
  assert.ok(!h.node('conversation-list').innerHTML.includes('Removed from Telegram'));assert.ok(h.node('conversation-list').innerHTML.includes('Visible next page'));
});


test('knowledge and keys support server pagination so older records stay manageable',async()=>{
  const h=harness();h.api.auth({access_token:'test-token'});h.api.configure({pane:'knowledge'});
  h.context.fetch=async(url)=>({ok:true,status:200,json:async()=> url.includes('/knowledge')?{sources:[{id:'source-id',title:url.includes('offset=100')?'Older policy':'Newest policy',content:'Facts',approved:true,enabled:true}],has_more:!url.includes('offset=100'),next_offset:100}:{keys:[{id:'key-id',name:url.includes('offset=100')?'Older active key':'Newest key',prefix:'otg_demo',scopes:['read']}],has_more:!url.includes('offset=100'),next_offset:100}});
  await h.api.loadKnowledge();assert.ok(h.node('knowledge-sources').innerHTML.includes('id="knowledge-next"'));
  await h.node('knowledge-next').onclick();assert.ok(h.node('knowledge-sources').innerHTML.includes('Older policy'));assert.ok(h.node('knowledge-sources').innerHTML.includes('id="knowledge-previous"'));
  await h.node('knowledge-previous').onclick();assert.ok(h.node('knowledge-sources').innerHTML.includes('Newest policy'));
  h.api.configure({pane:'settings'});await h.api.loadKeys();assert.ok(h.node('key-list').innerHTML.includes('id="keys-next"'));
  await h.node('keys-next').onclick();assert.ok(h.node('key-list').innerHTML.includes('Older active key'));assert.ok(h.node('key-list').innerHTML.includes('id="keys-previous"'));
});


test('manual chat selection wins over a delayed browser Back restoration lookup',async()=>{
  const h=harness();h.api.configure({pane:'inbox'});let resolveLookup;
  const pending=h.api.restoreConversation('first-account','77',h.api.epoch());
  h.queries[0].pending=new Promise(resolve=>{resolveLookup=resolve;});
  await turn();h.api.openConversation({account_id:'second-account',chat_id:'88',title:'Manually selected chat'},false);
  resolveLookup({error:null,data:[{account_id:'first-account',chat_id:'77',title:'Old history target'}]});await pending;
  assert.equal(h.api.chat().chat_id,'88');assert.equal(h.context.history.state.chatId,'88');
  assert.ok(h.node('chat-panel').innerHTML.includes('Manually selected chat'));assert.ok(!h.node('chat-panel').innerHTML.includes('Old history target'));
});

for(const item of [{pane:'knowledge',prefix:'knowledge',loader:'loadKnowledge',host:'knowledge-sources',field:'sources'},{pane:'settings',prefix:'keys',loader:'loadKeys',host:'key-list',field:'keys'}]){
  test(item.prefix+' pagination discards out-of-order replies and retains captured page controls',async()=>{
    const h=harness();h.api.auth({access_token:'test-token'});h.api.configure({pane:item.pane});const pending=[];
    h.context.fetch=url=>new Promise(resolve=>pending.push({url,resolve}));
    const response=(label,hasMore,next)=>({ok:true,status:200,json:async()=>({[item.field]:[{id:'record-id',title:label,name:label,content:'Facts',enabled:true,approved:true,scopes:['read']}],has_more:hasMore,next_offset:next})});
    const initial=h.api[item.loader]();await turn();pending.shift().resolve(response('Newest page',true,100));await initial;
    const firstNext=h.node(item.prefix+'-next').onclick();await turn();pending.shift().resolve(response('Middle page',true,200));await firstNext;
    const slowNext=h.node(item.prefix+'-next').onclick();await turn();const slow=pending.shift();assert.ok(slow.url.includes('offset=200'));
    const fastPrevious=h.node(item.prefix+'-previous').onclick();await turn();const fast=pending.shift();assert.ok(fast.url.includes('offset=0'));
    fast.resolve(response('Newest page restored',true,100));await fastPrevious;
    slow.resolve(response('Stale oldest page',false,null));await slowNext;
    assert.ok(h.node(item.host).innerHTML.includes('Newest page restored'));assert.ok(!h.node(item.host).innerHTML.includes('Stale oldest page'));
    const correctedNext=h.node(item.prefix+'-next').onclick();await turn();const next=pending.shift();assert.ok(next.url.includes('offset=100'));next.resolve(response('Middle page again',false,null));await correctedNext;
  });
}


test('older completed history never claims full sync while recent catch-up is pending',async()=>{
  const h=harness();h.api.configure({pane:'inbox',chat:{account_id:'first-account',chat_id:'77',history_complete:true,recent_complete:false,recent_note:'Waiting for recent Telegram page'}});
  await h.api.loadMessages(false);const status=h.node('history-note').textContent;
  assert.match(status,/Recent messages catching up/);assert.match(status,/Waiting for recent Telegram page/);assert.ok(!status.includes('Available history synchronized'));
});

test('sync status preserves older pending work after recent catch-up completes',async()=>{
  const h=harness();h.api.configure({pane:'inbox',chat:{account_id:'first-account',chat_id:'77',history_complete:false,history_note:'History checkpoint retained',recent_complete:true}});
  await h.api.loadMessages(false);const status=h.node('history-note').textContent;
  assert.match(status,/Older history sync in progress/);assert.match(status,/History checkpoint retained/);assert.ok(!status.includes('Recent messages catching up'));
  h.api.configure({chat:{account_id:'first-account',chat_id:'77',history_complete:true,recent_complete:true}});await h.api.loadMessages(false);
  assert.equal(h.node('history-note').textContent,'Available history synchronized · Read-only');
});


test('bot history reports received-update limits rather than perpetual catch-up',async()=>{
  const h=harness();h.api.configure({pane:'inbox',accounts:[{id:'bot-account',account_type:'bot',label:'Support bot'}],chat:{account_id:'bot-account',chat_id:'77',history_complete:false,recent_complete:false}});
  await h.api.loadMessages(false);assert.equal(h.node('history-note').textContent,'Bot history is limited to received updates · Read-only');
});

test('rendered chat buttons keep their own row while a refresh is pending or reordered',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'',search:'',unread:false,archive:true}});
  const button={getAttribute:()=> '0'};h.node('conversation-list').querySelectorAll=()=>[button];
  h.context.__dbResult=q=>({error:null,data:q.name==='open_tgate_tg_chats'?[{account_id:'first-account',chat_id:'1',title:'Original visible chat',is_visible:true}]:[]});
  await h.api.loadChats(false);const originalClick=button.onclick;
  let finishRefresh;const refreshed=h.api.loadChats(false);h.queries.at(-1).pending=new Promise(resolve=>{finishRefresh=resolve;});await turn();
  assert.equal(h.api.chats()[0].chat_id,'1');originalClick();assert.equal(h.api.chat().chat_id,'1');
  finishRefresh({error:null,data:[{account_id:'second-account',chat_id:'2',title:'New first row',is_visible:true}]});await refreshed;
  originalClick();assert.equal(h.api.chat().chat_id,'1');assert.equal(h.context.history.state.chatId,'1');
  h.api.navigate();originalClick();assert.equal(h.api.chat().chat_id,'1');
});

test('expanded prefix catches promoted later chats, retains loaded breadth on refresh and prunes removals',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'',search:'',unread:false,archive:true}});
  let dataset=Array.from({length:160},(_,i)=>({account_id:'first-account',chat_id:String(i),title:'Chat '+i,is_visible:true}));
  h.context.__dbResult=q=>{const range=q.calls.find(c=>c[0]==='range');return {error:null,data:range?dataset.slice(range[1],range[2]+1):[]};};
  await h.api.loadChats(false);assert.equal(h.api.chats().length,50);
  const promoted=dataset[120];dataset=[promoted,...dataset.filter(c=>c.chat_id!==promoted.chat_id)];
  await h.api.loadChats(true);assert.equal(h.api.chats().length,100);assert.equal(h.api.chats()[0].chat_id,'120');
  assert.equal(new Set(Array.from(h.api.chats(),c=>c.account_id+':'+c.chat_id)).size,100);
  dataset=dataset.filter(c=>c.chat_id!=='5');await h.api.loadChats(false);
  assert.equal(h.api.chats().length,100);assert.ok(!h.api.chats().some(c=>c.chat_id==='5'));
  assert.deepEqual(h.queries.at(-1).calls.find(c=>c[0]==='range'),['range',0,100]);
});

test('prefix fetching beyond 200 uses bounded batches and deduplicates composite chat identity',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'',search:'',unread:false,archive:true}});
  const dataset=Array.from({length:400},(_,i)=>({account_id:i<200?'first-account':'second-account',chat_id:String(i%200),title:'Chat '+i,is_visible:true}));dataset[200]=dataset[199];
  h.context.__dbResult=q=>{const range=q.calls.find(c=>c[0]==='range');return {error:null,data:range?dataset.slice(range[1],range[2]+1):[]};};
  await h.api.loadChats(false);for(let i=0;i<4;i++)await h.api.loadChats(true);
  assert.equal(h.api.chats().length,250);assert.equal(new Set(Array.from(h.api.chats(),c=>c.account_id+':'+c.chat_id)).size,250);
  assert.ok(h.queries.some(q=>q.calls.some(c=>c[0]==='range'&&c[1]===200&&c[2]===250)));
  assert.ok(h.queries.every(q=>q.calls.filter(c=>c[0]==='range').every(c=>c[2]-c[1]+1<=200)));
  await h.api.loadChats(false);assert.equal(h.api.chats().length,250);
  const last=h.queries.at(-1);assert.deepEqual(last.calls.find(c=>c[0]==='range'),['range',200,250]);
});

test('prefix refresh snapshots filters and discards replies from an older request',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'old-account',search:'Old',unread:false,archive:false}});
  let finishOld;const old=h.api.loadChats(false);const oldQuery=h.queries.at(-1);oldQuery.pending=new Promise(resolve=>{finishOld=resolve;});await turn();
  h.api.configure({filters:{account:'new-account',search:'New',unread:true,archive:true}});
  h.context.__dbResult=()=>({error:null,data:[{account_id:'new-account',chat_id:'2',title:'Latest filtered result',is_visible:true}]});await h.api.loadChats(false,true);
  finishOld({error:null,data:[{account_id:'old-account',chat_id:'1',title:'Obsolete filtered result',is_visible:true}]});await old;
  assert.ok(oldQuery.calls.some(c=>c[0]==='eq'&&c[1]==='account_id'&&c[2]==='old-account'));
  assert.ok(oldQuery.calls.some(c=>c[0]==='ilike'&&c[2]==='%Old%'));
  assert.equal(h.api.chats()[0].account_id,'new-account');assert.ok(!h.node('conversation-list').innerHTML.includes('Obsolete'));
});


// A small browser-history model executes the real popstate handler and verifies
// stack positions, rather than merely asserting pushState/back source strings.
function browserHistory(h,entries=[{otgPane:'dashboard',otgDepth:0}]){
  let index=entries.length-1,backCalls=0;
  const history={get state(){return entries[index];},pushState(state){entries.splice(index+1);entries.push(state);index++;},replaceState(state){entries[index]=state;},back(){backCalls++;if(index>0){index--;h.windowEvents.get('popstate')({state:entries[index]});}},forward(){if(index<entries.length-1){index++;h.windowEvents.get('popstate')({state:entries[index]});}}};
  h.context.history=history;h.api.configure({authenticated:true});
  return {entries,index:()=>index,backCalls:()=>backCalls,history};
}
const historyChat=(id)=>({account_id:'first-account',chat_id:id,title:'Chat '+id,is_visible:true});

test('mobile chat Back unwinds the pushed entry without leaving a duplicate inbox in history',async()=>{
  const h=harness(),stack=browserHistory(h);h.api.showWorkspace('inbox');await turn();
  h.api.openConversation(historyChat('A'),false);await turn();assert.equal(stack.index(),2);
  h.node('chat-back').onclick();await turn();assert.equal(stack.index(),1);assert.equal(h.api.chat(),null);assert.equal(stack.history.state.otgPane,'inbox');
  stack.history.back();await turn();assert.equal(stack.index(),0);assert.equal(stack.history.state.otgPane,'dashboard');
});

test('mobile and desktop Back share restoration for repeated chat selection and browser Forward',async()=>{
  const h=harness(),stack=browserHistory(h);h.context.__dbResult=q=>({error:null,data:q.name==='open_tgate_tg_chats'?[historyChat('A'),historyChat('B')]:[]});
  h.api.showWorkspace('inbox');await turn();h.api.openConversation(historyChat('A'),false);h.api.openConversation(historyChat('B'),false);await turn();assert.equal(stack.index(),3);
  h.node('chat-back').onclick();await turn();assert.equal(stack.index(),2);assert.equal(h.api.chat().chat_id,'A');assert.equal(stack.history.state.otgDepth,2);
  h.node('back-btn').events.click();await turn();assert.equal(stack.index(),1);assert.equal(h.api.chat(),null);
  stack.history.forward();await turn();assert.equal(h.api.chat().chat_id,'A');assert.equal(stack.history.state.otgDepth,2);
  h.node('chat-back').onclick();await turn();assert.equal(stack.index(),1);assert.equal(h.api.chat(),null);
});

for(const control of ['mobile','desktop'])test(control+' Back falls back to the inbox for a directly restored chat without a pushed entry',async()=>{
  const h=harness(),stack=browserHistory(h,[{foreign:'outside-site'},null]);
  h.api.showWorkspace('inbox',true);await turn();h.api.openConversation(historyChat('direct'),true);await turn();assert.equal(stack.history.state.otgDepth,0);
  if(control==='mobile')h.node('chat-back').onclick();else h.node('back-btn').events.click();await turn();
  assert.equal(stack.backCalls(),0);assert.equal(stack.index(),1);assert.equal(h.api.chat(),null);assert.equal(stack.history.state.otgPane,'inbox');assert.equal(stack.entries[0].foreign,'outside-site');
});


for(const control of [{id:'inbox-search',tag:'INPUT',value:'Support'},{id:'inbox-account',tag:'SELECT',value:'first-account'},{id:'inbox-unread',tag:'INPUT',value:'on',checked:true}]){
  test('inbox polling preserves focused '+control.id+' while applying new, edited and deleted message updates',async()=>{
    const h=harness();h.api.configure({pane:'inbox',pollAt:Date.now()-16000,filters:{account:'first-account',search:'Support',unread:true,archive:false},chat:{account_id:'first-account',chat_id:'77',title:'Support chat',is_visible:true},rows:[{message_id:'1',text:'Deleted cached text'},{message_id:'2',text:'Old message text'}]});
    const input=h.node(control.id);input.tagName=control.tag;input.value=control.value;input.checked=!!control.checked;h.context.document.activeElement=input;
    h.node('pane-body').contains=el=>el===input;h.node('pane-body').innerHTML='existing inbox and filter controls';let resolveChat,resolveMessages;
    h.context.__dbResult=q=>{
      if(q.name==='open_tgate_tg_accounts')return {error:null,data:[{id:'first-account',label:'Support',status:'authorized',account_type:'user'}]};
      if(q.name==='open_tgate_tg_chats')return new Promise(resolve=>{resolveChat=resolve;});
      if(q.name==='open_tgate_tg_messages' && !q.calls.some(c=>c[0]==='in'))return new Promise(resolve=>{resolveMessages=resolve;});
      return {error:null,data:[]};
    };
    await h.api.refreshAccounts();await turn();assert.equal(typeof resolveChat,'function');assert.equal(typeof resolveMessages,'function');
    assert.equal(h.context.document.activeElement,input);assert.equal(h.node('pane-body').innerHTML,'existing inbox and filter controls');
    resolveChat({error:null,data:[{account_id:'first-account',chat_id:'77',title:'Support chat refreshed',last_message:'New incoming text',is_visible:true}]});
    resolveMessages({error:null,data:[{message_id:'3',text:'New incoming text'},{message_id:'2',text:'Edited message text'}]});await turn();
    assert.deepEqual(Array.from(h.api.messages(),m=>m.message_id),['2','3']);assert.ok(h.node('message-list').innerHTML.includes('Edited message text'));assert.ok(h.node('message-list').innerHTML.includes('New incoming text'));assert.ok(!h.node('message-list').innerHTML.includes('Deleted cached text'));
    assert.equal(h.context.document.activeElement,input);assert.equal(h.node(control.id),input);assert.equal(input.value,control.value);assert.equal(input.checked,!!control.checked);assert.equal(h.node('pane-body').innerHTML,'existing inbox and filter controls');
  });
}

test('focused search does not prevent polling from removing a deleted chat and its cached history',async()=>{
  const h=harness();h.api.configure({pane:'inbox',pollAt:Date.now()-16000,filters:{account:'',search:'',unread:false,archive:true},chat:{account_id:'first-account',chat_id:'77',title:'Removed chat',is_visible:true},rows:[{message_id:'1',text:'Old cached history'}]});
  const input=h.node('inbox-search');input.tagName='INPUT';input.value='partial search';h.context.document.activeElement=input;h.node('pane-body').contains=el=>el===input;
  h.context.__dbResult=q=>({error:null,data:q.name==='open_tgate_tg_accounts'?[{id:'first-account',label:'Support',status:'authorized'}]:q.name==='open_tgate_tg_chats'&&!q.calls.some(c=>c[0]==='range')?[{account_id:'first-account',chat_id:'77',is_visible:false}]:[]});
  await h.api.refreshAccounts();await turn();assert.equal(h.api.chat(),null);assert.equal(h.api.messages().length,0);assert.equal(h.context.document.activeElement,input);assert.equal(input.value,'partial search');
});


test('authenticated reload restores the selected chat from persisted browser history beyond page one',async()=>{
  const h=harness();h.context.location.hash='#inbox';h.context.history.state={otgPane:'inbox',otgDepth:2,chatAccountId:'first-account',chatId:'77'};
  h.context.__dbResult=q=>({error:null,data:q.name==='open_tgate_operators'?[{email:'operator@example.com',role:'operator',is_active:true}]:q.name==='open_tgate_tg_accounts'?[{id:'first-account',label:'Support',status:'authorized',account_type:'user'}]:q.name==='open_tgate_tg_chats'?q.calls.some(c=>c[0]==='range')?[{account_id:'first-account',chat_id:'1',title:'First page chat',is_visible:true}]:[{account_id:'first-account',chat_id:'77',title:'Persisted selected chat',is_visible:true}]:[]});
  await h.api.renderFor({user:{email:'operator@example.com'}});await turn();
  assert.equal(h.api.chat().chat_id,'77');assert.ok(h.node('chat-panel').innerHTML.includes('Persisted selected chat'));
  assert.equal(h.context.history.state.chatId,'77');assert.equal(h.context.history.state.chatAccountId,'first-account');assert.equal(h.context.history.state.otgDepth,2);
  assert.ok(h.queries.some(q=>q.name==='open_tgate_tg_chats'&&q.calls.some(c=>c[0]==='eq'&&c[1]==='chat_id'&&c[2]==='77')));
});

test('reload honors an explicit workspace URL instead of stale inbox selection state',async()=>{
  const h=harness();h.context.location.hash='#settings';h.context.history.state={otgPane:'inbox',otgDepth:2,chatAccountId:'first-account',chatId:'77'};
  h.context.__dbResult=q=>({error:null,data:q.name==='open_tgate_operators'?[{email:'operator@example.com',role:'operator',is_active:true}]:q.name==='open_tgate_tg_accounts'?[{id:'first-account',label:'Support',status:'authorized'}]:[]});
  await h.api.renderFor({user:{email:'operator@example.com'}});await turn();
  assert.equal(h.api.chat(),null);assert.equal(h.context.history.state.otgPane,'settings');assert.ok(!h.queries.some(q=>q.name==='open_tgate_tg_chats'));
});

test('reload of a removed selected chat respects visibility and shows an unavailable state',async()=>{
  const h=harness();h.context.location.hash='#inbox';h.context.history.state={otgPane:'inbox',otgDepth:2,chatAccountId:'first-account',chatId:'77'};
  h.context.__dbResult=q=>({error:null,data:q.name==='open_tgate_operators'?[{email:'operator@example.com',role:'operator',is_active:true}]:q.name==='open_tgate_tg_accounts'?[{id:'first-account',label:'Support',status:'authorized'}]:[]});
  await h.api.renderFor({user:{email:'operator@example.com'}});await turn();
  assert.equal(h.api.chat(),null);assert.ok(h.node('chat-panel').innerHTML.includes('Conversation unavailable'));
  assert.ok(h.queries.some(q=>q.name==='open_tgate_tg_chats'&&q.calls.some(c=>c[0]==='eq'&&c[1]==='chat_id'&&c[2]==='77')&&q.calls.some(c=>c[0]==='eq'&&c[1]==='is_visible'&&c[2]===true)));
});


test('reload destination survives pending lookup, overlapping auth events and a second reload',async()=>{
  const h=harness();h.context.location.hash='#inbox';h.context.history.state={otgPane:'inbox',otgDepth:2,chatAccountId:'first-account',chatId:'77'};let finishLookup;
  const db=q=>({error:null,data:q.name==='open_tgate_operators'?[{email:'operator@example.com',role:'operator',is_active:true}]:q.name==='open_tgate_tg_accounts'?[{id:'first-account',label:'Support',status:'authorized'}]:q.name==='open_tgate_tg_chats'&&!q.calls.some(c=>c[0]==='range')?[{account_id:'first-account',chat_id:'77',title:'Restored destination',is_visible:true}]:[]});
  h.context.__dbResult=q=>q.name==='open_tgate_tg_chats'&&!q.calls.some(c=>c[0]==='range')?new Promise(resolve=>{finishLookup=resolve;}):db(q);
  const session={user:{email:'operator@example.com'}};await h.api.renderFor(session);await turn();assert.equal(typeof finishLookup,'function');assert.equal(h.api.chat(),null);
  assert.equal(h.context.history.state.chatId,'77');assert.equal(h.context.history.state.chatAccountId,'first-account');assert.equal(h.context.history.state.otgDepth,2);
  await h.api.renderFor(session);assert.equal(h.context.history.state.chatId,'77');
  const reloaded=harness();reloaded.context.location.hash='#inbox';reloaded.context.history.state={...h.context.history.state};reloaded.context.__dbResult=db;
  await reloaded.api.renderFor(session);await turn();assert.equal(reloaded.api.chat().chat_id,'77');assert.equal(reloaded.context.history.state.otgDepth,2);
  h.api.openConversation({account_id:'first-account',chat_id:'88',title:'New manual selection',is_visible:true},false);
  finishLookup({error:null,data:[{account_id:'first-account',chat_id:'77',title:'Late obsolete destination',is_visible:true}]});await turn();
  assert.equal(h.api.chat().chat_id,'88');assert.equal(h.context.history.state.chatId,'88');assert.ok(!h.node('chat-panel').innerHTML.includes('Late obsolete destination'));
});


test('manual inbox selection wins while reload bootstrap waits for its first account snapshot',async()=>{
  const h=harness();h.context.location.hash='#inbox';h.context.history.state={otgPane:'inbox',otgDepth:2,chatAccountId:'first-account',chatId:'77'};let finishAccounts;
  h.context.__dbResult=q=>q.name==='open_tgate_tg_accounts'?new Promise(resolve=>{finishAccounts=resolve;}):{error:null,data:q.name==='open_tgate_operators'?[{email:'operator@example.com',role:'operator',is_active:true}]:q.name==='open_tgate_tg_chats'?[{account_id:'first-account',chat_id:'88',title:'New manual destination',is_visible:true}]:[]};
  const boot=h.api.renderFor({user:{email:'operator@example.com'}});await turn();assert.equal(typeof finishAccounts,'function');
  h.api.showWorkspace('inbox');h.api.openConversation({account_id:'first-account',chat_id:'88',title:'New manual destination',is_visible:true},false);await turn();
  assert.equal(h.api.chat().chat_id,'88');
  finishAccounts({error:null,data:[{id:'first-account',label:'Support',status:'authorized'}]});await boot;await turn();
  assert.equal(h.api.chat().chat_id,'88');assert.equal(h.context.history.state.chatId,'88');assert.ok(h.node('chat-panel').innerHTML.includes('New manual destination'));
  assert.ok(!h.queries.some(q=>q.name==='open_tgate_tg_chats'&&q.calls.some(c=>c[0]==='eq'&&c[1]==='chat_id'&&c[2]==='77')));
});


test('selected filtered chat retains its panel across a transient membership outage and resumes history on retry',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'other-account',search:'Different chat',unread:false,archive:false}});let failure=true,removed=false;
  h.context.__dbResult=q=>{
    if(q.name==='open_tgate_tg_chats')return q.calls.some(c=>c[0]==='range')?{error:null,data:[]}:failure?{error:{message:'Temporary membership outage'},data:null}:{error:null,data:[{account_id:'first-account',chat_id:'77',is_visible:!removed,recent_complete:true,history_complete:true}]};
    if(q.name==='open_tgate_tg_messages')return {error:null,data:q.calls.some(c=>c[0]==='in')?[]:[{message_id:failure?'1':'2',text:failure?'Previously synchronized text':'Fresh text after recovery'}]};
    return {error:null,data:[]};
  };
  h.api.openConversation({account_id:'first-account',chat_id:'77',title:'Selected outside the current filter',is_visible:true},false);await turn();
  const panel=h.node('chat-panel').innerHTML,draft=h.node('chat-draft').onclick,back=h.node('chat-back').onclick;
  await h.api.loadChats(false);assert.equal(h.api.chat().chat_id,'77');assert.equal(h.node('chat-panel').innerHTML,panel);assert.match(h.node('chat-sync-msg').textContent,/Temporary membership outage/);
  assert.equal(h.node('chat-draft').onclick,draft);assert.equal(h.node('chat-back').onclick,back);assert.ok(h.node('chat-panel').innerHTML.includes('id="message-list"'));
  failure=false;await h.api.loadChats(false);await h.api.loadMessages(false);
  assert.equal(h.node('chat-sync-msg').textContent,'');assert.ok(h.node('message-list').innerHTML.includes('Fresh text after recovery'));assert.ok(!h.node('message-list').innerHTML.includes('Previously synchronized text'));
  assert.equal(h.node('history-note').textContent,'Available history synchronized · Read-only');assert.equal(h.node('chat-panel').innerHTML,panel);
  removed=true;await h.api.loadChats(false);assert.equal(h.api.chat(),null);assert.equal(h.api.messages().length,0);assert.ok(!h.node('chat-panel').innerHTML.includes('id="message-list"'));
});

test('late membership errors cannot replace or warn a newer manually selected chat',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'',search:'',unread:false,archive:true}});let finishMembership;
  h.context.__dbResult=q=>q.name==='open_tgate_tg_chats'&&!q.calls.some(c=>c[0]==='range')?new Promise(resolve=>{finishMembership=resolve;}):{error:null,data:[]};
  h.api.openConversation({account_id:'first-account',chat_id:'77',title:'Old selection',is_visible:true},false);await turn();
  const pending=h.api.loadChats(false);await turn();assert.equal(typeof finishMembership,'function');
  h.api.openConversation({account_id:'second-account',chat_id:'88',title:'New manual selection',is_visible:true},false);await turn();
  finishMembership({error:{message:'Late old membership error'},data:null});await pending;
  assert.equal(h.api.chat().chat_id,'88');assert.ok(h.node('chat-panel').innerHTML.includes('New manual selection'));assert.ok(!h.node('chat-sync-msg').textContent.includes('Late old membership error'));
});


test('OAuth consent describes only granted scopes',()=>{
  const h=harness();
  assert.match(h.api.oauthScopeDescription(['read']),/Telegram accounts/);
  assert.doesNotMatch(h.api.oauthScopeDescription(['read']),/Approved knowledge/);
  assert.match(h.api.oauthScopeDescription(['knowledge:read']),/Approved knowledge/);
  assert.doesNotMatch(h.api.oauthScopeDescription(['knowledge:read']),/Telegram accounts/);
});

test('failed consent preview clears persistent and URL context and offers workspace recovery',async()=>{
  let stored='client_id=expired';
  const h=harness({href:'https://open-tgate.site/app?oauth_request=client_id%3Dexpired',search:'?oauth_request=client_id%3Dexpired',
    storage:{getItem(){return stored;},setItem(_k,v){stored=v;},removeItem(){stored=null;}}});
  h.api.configure({authenticated:true});
  h.context.fetch=async()=>({ok:false,json:async()=>({error:'invalid_client_or_redirect_uri'})});
  let replacement;
  h.context.history.replaceState=(_state,_title,url)=>{replacement=url;};
  await h.api.showOAuthConsent();
  assert.equal(stored,null);
  assert.equal(replacement,'/app');
  assert.match(h.node('pane-body').innerHTML,/Back to workspace/);
  assert.equal(typeof h.node('oauth-consent-back').onclick,'function');
});

test('stale failed consent response cannot clear a newer workspace',async()=>{
  let stored='client_id=pending',resolve;
  const h=harness({storage:{getItem(){return stored;},setItem(){},removeItem(){stored=null;}}});
  h.api.configure({authenticated:true});
  h.context.fetch=()=>new Promise(r=>{resolve=r;});
  const pending=h.api.showOAuthConsent();
  h.api.navigate();h.api.configure({pane:'inbox'});
  resolve({ok:false,json:async()=>({error:'invalid_client'})});
  await pending;
  assert.equal(stored,'client_id=pending');
});


test('valid consent retains an exit when approval later becomes invalid',async()=>{
  let stored='client_id=valid';
  const h=harness({storage:{getItem(){return stored;},setItem(){},removeItem(){stored=null;}}});
  h.api.configure({authenticated:true});h.api.auth({access_token:'local-fixture'});
  h.context.fetch=async(url)=>({ok:url.includes('preview=1'),json:async()=>url.includes('preview=1')?
    {client_name:'ChatGPT',redirect_uri:'https://chatgpt.com/connector/oauth/fixture',scopes:['read']}:
    {error:'invalid_client_or_redirect_uri'}});
  await h.api.showOAuthConsent();
  await h.node('oauth-consent-allow').onclick();
  assert.match(h.node('oauth-consent-msg').textContent,/invalid_client/);
  assert.equal(typeof h.node('oauth-consent-back').onclick,'function');
  h.node('oauth-consent-back').onclick();
  assert.equal(stored,null);
});


test('Accounts owns account setup, is navigable and escapes account labels',()=>{
  const h=harness();h.api.configure({authenticated:true,accounts:[{id:'one',label:'<script>private</script>',status:'pending',account_type:'user'}]});
  h.api.showWorkspace('accounts');assert.equal(h.context.history.state.otgPane,'accounts');
  assert.ok(h.node('pane-body').innerHTML.includes('id="sb-add-personal"'));
  assert.ok(h.node('accounts-list').innerHTML.includes('&lt;script&gt;'));
  const sidebar=appHtml.slice(appHtml.indexOf('<aside'),appHtml.indexOf('</aside>'));
  assert.ok(sidebar.includes('id="nav-accounts"'));assert.ok(!sidebar.includes('id="sb-add-personal"'));assert.ok(!sidebar.includes('id="sb-add-bot"'));
});

test('Telegram folders preserve account identity, main position and escaped names',()=>{
  const h=harness();h.api.configure({accounts:[{id:'one',main_chat_list_position:1,chat_folders:[{id:3,title:'<Work>'},{id:7,title:'Channels'}]}],filters:{account:'one',folder:'3'}});
  h.api.renderFolderOptions();const html=h.node('inbox-folder').innerHTML;
  assert.ok(html.indexOf('value="3"')<html.indexOf('value="main"'));assert.ok(html.indexOf('value="main"')<html.indexOf('value="7"'));assert.ok(html.includes('&lt;Work&gt;'));assert.equal(h.node('inbox-folder').value,'3');
  h.api.configure({filters:{account:'',folder:'3'}});h.api.renderFolderOptions();assert.equal(h.node('inbox-folder').value,'main');assert.ok(!h.node('inbox-folder').innerHTML.includes('value="3"'));
});

test('custom folder queries retain archived members, native order and account scope',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'one',folder:'3',kind:'bot',unread:false,archive:false,search:''}});
  await h.api.loadChats(false);const calls=h.queries.at(-1).calls;
  assert.ok(calls.some(c=>c[0]==='contains'&&c[1]==='folder_ids'&&c[2][0]===3));
  assert.ok(calls.some(c=>c[0]==='eq'&&c[1]==='account_id'&&c[2]==='one'));
  assert.ok(calls.some(c=>c[0]==='eq'&&c[1]==='kind'&&c[2]==='bot'));
  assert.ok(!calls.some(c=>c[0]==='eq'&&c[1]==='is_archived'));
  assert.equal(calls.find(c=>c[0]==='order')[1],'folder_positions->>3');
});

test('rejected read refreshes once and forwards only the renewed bearer',async()=>{
  const h=harness();h.api.auth({access_token:'stale'});let refreshed=0,seen=[];
  h.context.fetch=async(_url,opts)=>{seen.push(opts.headers.Authorization);return {status:seen.length===1?401:200,ok:seen.length>1,json:async()=>({events:[]})};};
  h.api.setRefresh(async()=>{refreshed++;return {data:{session:{access_token:'fresh'}}};});
  await h.api.workspaceAPI('/audit');assert.equal(refreshed,1);assert.deepEqual(seen,['Bearer stale','Bearer fresh']);
});

test('missing server session and repeated unauthorized reads provide bounded sign-in recovery',async()=>{
  for(const missing of [true,false]){
    const h=harness();h.api.auth({access_token:'stale'});let calls=0,refreshes=0;
    h.context.fetch=async()=>{calls++;return {status:401,ok:false,json:async()=>({detail:'unauthorized'})};};
    h.api.setRefresh(async()=>{refreshes++;return missing?{error:{message:'session missing'},data:{session:null}}:{data:{session:{access_token:'fresh'}}};});
    await assert.rejects(h.api.workspaceAPI('/deleted'),/Sign in again/);assert.equal(refreshes,1);assert.equal(calls,missing?1:2);
  }
});

test('operator writes are never replayed after authorization rejection',async()=>{
  const h=harness();h.api.auth({access_token:'stale'});let calls=0,refreshes=0;
  h.api.setRefresh(async()=>{refreshes++;return {data:{session:{access_token:'fresh'}}};});
  h.context.fetch=async()=>{calls++;return {status:401,ok:false,json:async()=>({detail:'unauthorized'})};};
  await assert.rejects(h.api.workspaceAPI('/keys',{method:'POST',body:{name:'reader'}}),/Sign in again/);assert.equal(calls,1);assert.equal(refreshes,0);
});


test('single-account main inbox follows Telegram pinned order and numeric descending chat-id ties',async()=>{
  const h=harness();h.api.configure({pane:'inbox',filters:{account:'one',folder:'main',search:'',archive:false,unread:false}});await h.api.loadChats(false);
  const orders=h.queries.at(-1).calls.filter(c=>c[0]==='order');assert.equal(orders[0][1],'folder_positions->>main');assert.equal(orders[1][1],'telegram_sort_id');assert.equal(orders[1][2].ascending,false);
});

test('selected bot inbox retains latest-message ordering without personal chat-list positions',async()=>{
  const h=harness();h.api.configure({pane:'inbox',accounts:[{id:'bot-one',account_type:'bot'}],filters:{account:'bot-one',folder:'main',search:'',archive:false,unread:false}});await h.api.loadChats(false);
  const orders=h.queries.at(-1).calls.filter(c=>c[0]==='order');assert.equal(orders[0][1],'last_message_at');assert.equal(orders[1][1],'chat_id');assert.equal(orders[1][2].ascending,true);
});

test('live message activity binds account/type/folder and displays escaped text by default',async()=>{
  const h=harness();h.api.configure({pane:'activity'});h.api.auth({access_token:'test-token'});
  h.node('activity-account').value='account-one';h.node('activity-kind').value='bot';h.node('activity-folder').value='6';h.node('activity-query').value='100% _';h.node('activity-live').checked=true;
  h.context.fetch=async(url,options)=>{h.requests.push({url,options});return {ok:true,json:async()=>({capabilities:{message_activity:true},messages:[{account_id:'account-one',chat_id:'99',chat_title:'Notify',kind:'bot',text:'<script>private text</script>',sent_at:'2026-01-01'}]})};};
  await h.api.loadMessageActivity(false);const u=new URL(h.requests.find(r=>r.url.includes('/message-activity?')).url,'https://example.com');
  assert.equal(u.searchParams.get('account_id'),'account-one');assert.equal(u.searchParams.get('kind'),'bot');assert.equal(u.searchParams.get('folder_id'),'6');assert.equal(u.searchParams.get('query'),'100% _');
  const html=h.node('activity-messages').innerHTML;assert.match(html,/&lt;script&gt;private text/);assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<details>'));assert.match(h.node('activity-feed-status').textContent,/Search · automatic refresh paused/);
});

test('activity keeps successful messages on transient refresh errors but clears changed scopes',async()=>{
  const h=harness();h.api.configure({pane:'activity'});h.api.auth({access_token:'test-token'});
  h.context.fetch=async()=>({ok:true,json:async()=>({capabilities:{message_activity:true},messages:[{text:'retained message'}]})});await h.api.loadMessageActivity(false);
  h.context.fetch=async()=>{throw new Error('Temporary failure');};await h.api.loadMessageActivity(false);
  assert.match(h.node('activity-messages').innerHTML,/retained message/);assert.match(h.node('activity-feed-status').textContent,/last successful/);
  h.node('activity-account').value='different-account';await h.api.loadMessageActivity(false);assert.ok(!h.node('activity-messages').innerHTML.includes('retained message'));
});

test('activity discards late filter responses and paginates older records without automatic refresh',async()=>{
  const h=harness();h.api.configure({pane:'activity'});h.api.auth({access_token:'test-token'});const pending=[];
  h.context.fetch=url=>url.endsWith('/accounts')?Promise.resolve({ok:true,json:async()=>({capabilities:{message_activity:true}})}):new Promise(resolve=>pending.push({url,resolve}));const response=text=>({ok:true,json:async()=>({capabilities:{message_activity:true},messages:Array.from({length:50},()=>({text})),next_cursor:'stable-boundary'})});
  const slow=h.api.loadMessageActivity(false);await turn();h.node('activity-kind').value='group';const fast=h.api.loadMessageActivity(false);await turn();pending[1].resolve(response('current group'));await fast;pending[0].resolve(response('stale personal'));await slow;
  assert.match(h.node('activity-messages').innerHTML,/current group/);assert.ok(!h.node('activity-messages').innerHTML.includes('stale personal'));
  const older=h.api.loadMessageActivity(true);await turn();assert.match(pending[2].url,/cursor=stable-boundary/);assert.ok(!pending[2].url.includes('offset='));pending[2].resolve(response('older group'));await older;assert.match(h.node('activity-messages').innerHTML,/older group/);
});

test('deleted message text is visible while technical metadata is collapsed and escaped',async()=>{
  const h=harness();h.api.configure({pane:'activity'});h.api.auth({access_token:'test-token'});h.node('audit-mode').value='deleted';
  h.context.fetch=async()=>({ok:true,json:async()=>({capabilities:{message_activity:true},messages:[{account_id:'a',text:'<img>captured deletion',meta:{nested:'value'}}]})});await h.api.loadAudit(false);
  const html=h.node('audit-list').innerHTML;assert.ok(html.indexOf('captured deletion')<html.indexOf('<details>'));assert.match(html,/&lt;img&gt;/);assert.ok(!html.includes('[object Object]'));assert.match(html,/Technical details/);
});

test('native activity folders preserve focused selection during account refresh',()=>{
  const h=harness();h.api.configure({accounts:[{id:'a',chat_folders:[{id:2,title:'Contacts'}]}]});h.node('activity-account').value='a';const select=h.node('activity-folder');select.value='2';select.options=[{value:''},{value:'2'}];h.context.document.activeElement=select;
  h.api.renderActivityFolders(false);assert.equal(select.value,'2');assert.equal(select.disabled,false);h.api.renderActivityFolders(true);assert.equal(select.value,'');h.node('activity-account').value='';h.api.renderActivityFolders(false);assert.equal(select.disabled,true);
});


test('empty and whitespace media bodies show content type rather than blank activity',async()=>{
  const h=harness();h.api.configure({pane:'activity'});h.api.auth({access_token:'test-token'});
  h.context.fetch=async()=>({ok:true,json:async()=>({capabilities:{message_activity:true},messages:[{text:'',content_type:'messagePhoto'},{text:'  ',content_type:'messageSticker'}]})});
  await h.api.loadMessageActivity(false);assert.match(h.node('activity-messages').innerHTML,/\[messagePhoto\]/);assert.match(h.node('activity-messages').innerHTML,/\[messageSticker\]/);
});


test('older APIs keep activity and conversation search available without requesting unsupported endpoints',async()=>{
  const h=harness();h.api.auth({access_token:'test-token'});h.api.configure({pane:'activity'});
  h.context.fetch=async url=>{h.requests.push({url});return {ok:true,json:async()=>({accounts:[],sources:[]})};};
  await h.api.loadMessageActivity(false);assert.match(h.node('activity-messages').innerHTML,/Message activity is updating/);
  h.api.showKnowledge();h.node('conversation-search-query').value='support';await h.node('conversation-search-form').onsubmit({preventDefault(){}});
  assert.match(h.node('conversation-search-results').innerHTML,/Conversation search is updating/);
  assert.ok(!h.requests.some(r=>r.url.includes('/message-activity')));
});

test('audit monitoring continues while message search polling is paused',async()=>{
  const h=harness();h.api.auth({access_token:'test-token'});h.api.configure({pane:'activity'});h.node('activity-live').checked=true;h.node('activity-query').value='search';h.node('audit-mode').value='audit';
  h.context.fetch=async url=>{h.requests.push({url});return {ok:true,json:async()=>({events:[]})};};
  await h.api.refreshAccounts();await turn();assert.ok(h.requests.some(r=>r.url.includes('/audit')));assert.ok(!h.requests.some(r=>r.url.includes('/message-activity')));
});
