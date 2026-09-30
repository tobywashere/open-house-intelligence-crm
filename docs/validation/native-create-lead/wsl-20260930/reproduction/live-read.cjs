const {chromium}=require('C:/Users/ankus/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path');
const out=path.join(__dirname,'read-live-20260930');fs.mkdirSync(out,{recursive:false});
const key=fs.readFileSync('\\\\wsl.localhost\\Ubuntu\\home\\ankus\\.ohi-native-acceptance\\ohi-native-read-review\\human.key','utf8').trim();
const prompts=['How many leads are in the CRM?','Show the lead directory and its total count.','Tell me the current number of CRM leads.','List the CRM leads and tell me how many there are.','What is the total lead count in the CRM right now?','Read the CRM directory and report its size.','How many people are listed as CRM leads?','Show all current CRM leads with the total.','Check the CRM and give me the number of leads.','Please retrieve the lead directory and summarize the count.'];
const scope=['How many closed leads?','How many leads were added today?','Show leads in Seattle','Show the second page of the lead directory','Delete all leads'];
const results={commit:'f04a67e559474401a4d2475630d43503b9c0255c',kind:'live-visible-browser-protected-read',started:new Date().toISOString(),retries:0,cases:[]};
function save(){fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(results,null,2));}
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:false});results.browser_version=browser.version();
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(10000);
 await page.addInitScript(()=>{const d=new Date(),p=n=>String(n).padStart(2,'0');localStorage.setItem('ohi-summary-seen',`${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`)});
 await page.goto('http://127.0.0.1:18083');
 await page.getByLabel('API token',{exact:true}).fill(key);await page.getByRole('button',{name:'Unlock',exact:true}).click();await page.getByRole('button',{name:'Lock',exact:true}).waitFor();
 await page.getByRole('button',{name:'CRM reads',exact:true}).click();
 for(const [i,prompt] of [...prompts,...scope].entries()){
  await page.getByLabel('Your question',{exact:true}).fill(prompt);
  const start=Date.now();const pending=page.waitForResponse(r=>r.url().endsWith('/api/chat/directory'),{timeout:75000});
  await page.getByRole('button',{name:'Read CRM',exact:true}).click();const response=await pending;const body=await response.json();
  const c={case:i+1,prompt,status:response.status(),latency_ms:Date.now()-start,response:body};
  if(i<10 && response.ok()){
    const region=page.getByRole('region',{name:'Verified CRM result'});await region.waitFor();c.displayed_text=await region.innerText();c.displayed_request_id=await region.getAttribute('data-request-id');
    c.passed=body.result?.total===37&&body.result.leads.length===25&&c.displayed_request_id===body.request_id&&c.displayed_text.includes('37 leads total');
  }else{
    const alert=page.getByRole('alert');await alert.waitFor();c.displayed_error=await alert.innerText();c.no_verified_result=await page.getByRole('region',{name:'Verified CRM result'}).count()===0;
    c.passed=i>=10&&response.status()===400&&c.no_verified_result;
  }
  results.cases.push(c);save();console.log(JSON.stringify({case:c.case,status:c.status,passed:c.passed,request_id:body.request_id,code:body.error?.code}));
  if(i===9||i>=10||!c.passed)await page.screenshot({path:path.join(out,`case-${i+1}.png`),fullPage:true});
 }
 results.passed=results.cases.every(c=>c.passed);results.finished=new Date().toISOString();save();if(!results.passed)process.exitCode=1;
 }catch(e){results.failure=e.message.replaceAll(key,'<REDACTED>');save();console.error(results.failure);process.exitCode=1}finally{await browser.close()}
})();
