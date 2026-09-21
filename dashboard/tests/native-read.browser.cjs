// Small browser boundary suite; set CRM_TEST_URL to the built fixture dashboard.
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
before(async()=>{browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})})});
after(async()=>{await browser?.close()});
async function pageForTest(){
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 await page.addInitScript(()=>{
  const d=new Date(),p=n=>String(n).padStart(2,'0');
  localStorage.setItem('ohi-summary-seen',`${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`);
 });
 await page.goto(process.env.CRM_TEST_URL || 'http://127.0.0.1:18080');
 await page.getByLabel('Your question',{exact:true}).waitFor();return page;
}
const receipt=(total=37)=>({request_id:'a'.repeat(32),operation:'list_lead_directory',result:{total,offset:0,limit:25,leads:Array.from({length:Math.min(total,25)},(_,i)=>({id:i+1,name:`Synthetic Lead ${String(i+1).padStart(2,'0')}`,status:'new'}))}});
async function submit(page,message='How many leads are in the CRM?'){
 await page.getByLabel('Your question',{exact:true}).fill(message);await page.getByRole('button',{name:'Read CRM',exact:true}).click();
}
async function stub(page,status,json){await page.route('**/api/chat/directory',route=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(json)}))}
test('non-empty result renders total separately from its 25-row page',async()=>{
 const page=await pageForTest();try{await stub(page,200,receipt());await submit(page);
  const region=page.getByRole('region',{name:'Verified CRM result'});await region.waitFor();
  assert.match(await region.innerText(),/37 leads total/);assert.match(await region.innerText(),/Showing 25 of 37 leads/);
  assert.equal(await region.getByRole('listitem').count(),25);
 }finally{await page.close()}
});
test('empty CRM is a successful zero result',async()=>{
 const page=await pageForTest();try{await stub(page,200,receipt(0));await submit(page);
  await page.getByText('No leads in the CRM.',{exact:true}).waitFor();assert.equal(await page.getByRole('alert').count(),0);
 }finally{await page.close()}
});
test('unavailable gateway displays explicit error with no result',async()=>{
 const page=await pageForTest();try{await stub(page,503,{error:{code:'gateway_unavailable',message:'OpenClaw is unavailable. No current CRM result was received.'}});await submit(page);
  await page.getByRole('alert').waitFor();assert.match(await page.getByRole('alert').innerText(),/OpenClaw is unavailable/);assert.equal(await page.getByRole('region',{name:'Verified CRM result'}).count(),0);
 }finally{await page.close()}
});
test('malformed successful HTTP response is rejected',async()=>{
 const page=await pageForTest();try{const bad=receipt();bad.result.total='37';await stub(page,200,bad);await submit(page);
  await page.getByRole('alert').waitFor();assert.match(await page.getByRole('alert').innerText(),/could not be verified/);assert.equal(await page.getByRole('region',{name:'Verified CRM result'}).count(),0);
 }finally{await page.close()}
});
test('new pending/failed request removes previous verified result',async()=>{
 const page=await pageForTest();try{let count=0,release;
  await page.route('**/api/chat/directory',async route=>{
   if(++count===1)return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(receipt())});
   await new Promise(r=>release=r);await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{message:'No current result was received.'}})});
  });
  await submit(page);await page.getByRole('region',{name:'Verified CRM result'}).waitFor();await submit(page,'Show the lead directory.');
  await page.getByRole('status').waitFor();assert.equal(await page.getByRole('region',{name:'Verified CRM result'}).count(),0);
  release();await page.getByRole('alert').waitFor();assert.equal(await page.getByRole('region',{name:'Verified CRM result'}).count(),0);
 }finally{await page.close()}
});
test('write request is rejected by the real backend route',async()=>{
 const page=await pageForTest();try{await submit(page,'Delete all leads');await page.getByRole('alert').waitFor();
  assert.match(await page.getByRole('alert').innerText(),/cannot change CRM records/);
  assert.equal(await page.getByRole('region',{name:'Verified CRM result'}).count(),0);
 }finally{await page.close()}
});
test('unsupported filters and pages show scope errors from the real backend',async()=>{
 const page=await pageForTest();try{
  for(const message of ['How many closed leads?', 'How many leads were added today?', 'Show leads in Seattle', 'Show the second page of the lead directory']){
   const reply=page.waitForResponse(r=>r.url().endsWith('/api/chat/directory'));
   await submit(page,message);
   assert.equal((await reply).status(),400);
   await page.getByRole('alert').waitFor();
   assert.match(await page.getByRole('alert').innerText(),/unfiltered/);
   assert.equal(await page.getByRole('region',{name:'Verified CRM result'}).count(),0);
  }
 }finally{await page.close()}
});
