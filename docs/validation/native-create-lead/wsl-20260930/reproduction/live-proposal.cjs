const {chromium}=require('C:/Users/ankus/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const phase=process.argv[2]||'initial',out=path.join(__dirname,'proposal-live-20260930');
if(phase==='initial')fs.mkdirSync(out,{recursive:false});
const token=fs.readFileSync('\\\\wsl.localhost\\Ubuntu\\home\\ankus\\.ohi-native-acceptance\\ohi-native-proposal-review\\human.key','utf8').trim();
const base='http://127.0.0.1:18082',storageKey='ohi-lead-proposal-request';
const result={commit:'f04a67e559474401a4d2475630d43503b9c0255c',kind:'live-visible-browser-native-proposals',phase,started:new Date().toISOString(),cases:[],retries:0};
function save(){fs.writeFileSync(path.join(out,phase+'.json'),JSON.stringify(result,null,2));}
function record(name,data){result.cases.push({name,...data});save();console.log(JSON.stringify({name,passed:data.passed,request_id:data.request_id}));}
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:false});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);
 await page.addInitScript(()=>{const d=new Date(),p=n=>String(n).padStart(2,'0');localStorage.setItem('ohi-summary-seen',`${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`)});
 async function unlock(){await page.getByLabel('API token',{exact:true}).fill(token);await page.getByRole('button',{name:'Unlock',exact:true}).click();await page.getByRole('button',{name:'Lock',exact:true}).waitFor()}
 async function api(route,data){const opts={headers:{'X-API-Token':token}};const r=data===undefined?await page.request.get(base+'/api'+route,opts):await page.request.post(base+'/api'+route,{...opts,data});return {status:r.status(),body:await r.json()}}
 const rid=()=>page.evaluate(k=>sessionStorage.getItem(k),storageKey);
 const status=async id=>(await api('/chat/lead-proposal/'+id)).body;
 const leads=async()=>(await api('/leads')).body;
 const dialog=()=>page.getByRole('dialog',{name:'Pending approvals'});
 async function shot(name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true})}
 async function submit(message,label){
   await page.getByLabel('Lead request',{exact:true}).fill(message);
   const wait=page.waitForResponse(r=>r.url().endsWith('/api/chat/lead-proposal')&&r.request().method()==='POST',{timeout:75000});const start=Date.now();
   await page.getByRole('button',{name:'Send proposal request',exact:true}).click();const r=await wait;const response=await r.json();const id=await rid();
   const c={request_id:id,prompt:message,status:r.status(),latency_ms:Date.now()-start,response,lead_count:(await leads()).length,passed:r.ok()&&response.state==='proposed'};
   record(label,c);await shot(label);
   if(!c.passed){
     const check=await status(id);record(label+'-status',{request_id:id,response:check,passed:false});
     if(check.state==='unknown'||check.state==='running'){
       await page.getByRole('button',{name:'Check status',exact:true}).click();
       await page.getByRole('button',{name:'Close request',exact:true}).click();
       await page.getByText('No proposal was produced. No lead was created.',{exact:true}).waitFor();
       record(label+'-explicit-close',{request_id:id,response:await status(id),passed:(await status(id)).state==='failed'});
     }
     throw new Error('Live proposal failed; retained first failure and stopped without inference retry');
   }
   await dialog().waitFor();return {id,message,response};
 }
 async function deny(){await dialog().getByRole('button',{name:'Deny',exact:true}).click();await dialog().getByRole('button',{name:'Confirm deny',exact:true}).click();await dialog().waitFor({state:'hidden'})}
 if(phase==='initial'){
   await page.goto(base);await unlock();await page.getByRole('button',{name:'Propose lead',exact:true}).click();
   assert.equal((await leads()).length,0);
   const first=await submit('Propose Synthetic WSL Person, synthetic@example.invalid, 555-0100','first-proposal');
   assert.equal((await leads()).length,0);assert.equal(first.response.proposal.operation,'create_lead');
   assert.equal(await dialog().getByLabel('Name',{exact:true}).inputValue(),'Synthetic WSL Person');
   assert.equal(await dialog().getByLabel('Email',{exact:true}).inputValue(),'synthetic@example.invalid');
   await dialog().getByLabel('Name',{exact:true}).fill('Human Edited WSL Person');await dialog().getByLabel('Email',{exact:true}).fill('edited@example.invalid');await shot('human-edits');
   await dialog().getByRole('button',{name:'Approve',exact:true}).click();await page.getByRole('region',{name:'Approved lead'}).waitFor();
   const approved=await status(first.id),rows=await leads();
   assert.equal(rows.length,1);assert.equal(rows[0].name,'Human Edited WSL Person');assert.equal(rows[0].email,'edited@example.invalid');assert.equal(rows[0].id,approved.proposal.result.id);
   record('human-edited-approval',{request_id:first.id,response:approved,lead_count:rows.length,passed:true});await shot('approved');
   let posts=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/chat/lead-proposal'))posts++});
   await page.reload();await unlock();await page.getByRole('button',{name:'Propose lead',exact:true}).click();await page.getByRole('region',{name:'Approved lead'}).waitFor();assert.equal(await rid(),first.id);assert.equal(posts,0);
   record('refresh-approved-recovery',{request_id:first.id,proposal_post_count:posts,passed:true});
   await page.getByRole('button',{name:'New proposal request',exact:true}).click();
   const denied=await submit('Propose Synthetic WSL Denied Person','second-proposal');await deny();await page.getByText('Denied. No lead was created.',{exact:true}).waitFor();assert.equal((await leads()).length,1);
   record('human-denial',{request_id:denied.id,response:await status(denied.id),lead_count:1,passed:true});await shot('denied');
   await page.getByRole('button',{name:'New proposal request',exact:true}).click();
   const pending=await submit('Propose Synthetic WSL Restart Person','third-proposal-pending');assert.equal((await leads()).length,1);
   result.ids={approved:first.id,denied:denied.id,pending:pending.id};result.first_prompt=first.message;
   fs.writeFileSync(path.join(out,'ids.json'),JSON.stringify(result.ids,null,2));record('pending-before-restart',{request_id:pending.id,response:await status(pending.id),lead_count:1,passed:true});
 }else{
   const ids=JSON.parse(fs.readFileSync(path.join(out,'ids.json'),'utf8'));
   await page.addInitScript(({key,id})=>sessionStorage.setItem(key,id),{key:storageKey,id:ids.pending});
   await page.goto(base);await unlock();await dialog().waitFor();
   const pending=await status(ids.pending);assert.equal(pending.state,'proposed');assert.equal((await leads()).length,1);assert.equal(pending.proposal.payload.name,'Synthetic WSL Restart Person');
   record('restart-pending-recovery',{request_id:ids.pending,response:pending,lead_count:1,passed:true});await shot('restart-pending');
   await deny();assert.equal((await leads()).length,1);record('restart-pending-denial',{request_id:ids.pending,response:await status(ids.pending),lead_count:1,passed:true});
   for(const [name,id] of Object.entries(ids)){const r=await status(id);assert.equal(r.state,name==='approved'?'approved':'denied');record('restart-'+name+'-status',{request_id:id,response:r,passed:true})}
   await shot('restart-denied');
 }
 result.passed=result.cases.every(c=>c.passed);result.finished=new Date().toISOString();save();
 }catch(e){result.passed=false;result.failure=e.message.replaceAll(token,'<REDACTED>');save();console.error(result.failure);process.exitCode=1}finally{await browser.close()}
})();
