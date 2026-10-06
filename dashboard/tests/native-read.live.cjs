// Real browser/OpenClaw acceptance. Never intercepts or mocks the read request.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const prompts=[
 'How many leads are in the CRM?', 'Show the lead directory and its total count.',
 'Tell me the current number of CRM leads.', 'List the CRM leads and tell me how many there are.',
 'What is the total lead count in the CRM right now?', 'Read the CRM directory and report its size.',
 'How many people are listed as CRM leads?', 'Show all current CRM leads with the total.',
 'Check the CRM and give me the number of leads.', 'Please retrieve the lead directory and summarize the count.',
];
(async()=>{
 const expected=Number(process.env.EXPECTED_COUNT || 37);
 const out=process.env.LIVE_RESULTS;
 if(!out || fs.existsSync(out))throw new Error('Set LIVE_RESULTS to a new artifact path');
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});
 const results=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  await page.addInitScript(()=>{const d=new Date(),p=n=>String(n).padStart(2,'0');localStorage.setItem('ohi-summary-seen',`${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`)});
  await page.goto(process.env.CRM_TEST_URL || 'http://127.0.0.1:18080');
  for(const [i,prompt] of (expected===0?prompts.slice(0,1):prompts).entries()){
   await page.getByLabel('Your question',{exact:true}).fill(prompt);
   const start=Date.now();const responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/chat/directory'),{timeout:70000});
   await page.getByRole('button',{name:'Read CRM',exact:true}).click();
   const response=await responsePromise;const data=await response.json();
   const entry={case:i+1,prompt,status:response.status(),latency_ms:Date.now()-start,retries:0,expected_count:expected,receipt:data};
   if(response.ok()){
    const region=page.getByRole('region',{name:'Verified CRM result'});await region.waitFor();entry.displayed_text=await region.innerText();
    entry.displayed_request_id=await region.getAttribute('data-request-id');
    entry.passed=data.result?.total===expected && data.result.leads.length===Math.min(expected,25) && entry.displayed_request_id===data.request_id && entry.displayed_text.includes(`${expected} leads total`);
   }else{entry.displayed_error=await page.getByRole('alert').innerText();entry.passed=false}
   results.push(entry);fs.writeFileSync(out,JSON.stringify({kind:'live-dashboard-native-read',retries:0,results},null,2)+'\n');
   console.log(JSON.stringify({case:entry.case,passed:entry.passed,status:entry.status,latency_ms:entry.latency_ms,request_id:data.request_id}));
  }
  if(process.env.LIVE_SCREENSHOT)await page.screenshot({path:process.env.LIVE_SCREENSHOT,fullPage:true});
  assert(results.every(r=>r.passed),'One or more live cases failed; evidence retained, no retries');
 }finally{await browser.close()}
})().catch(e=>{console.error(e.message);process.exitCode=1});
