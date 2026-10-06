// Real CRM/auth/approval API; native completion is explicitly simulated.
const {test,before,after}=require('node:test')
const assert=require('node:assert/strict')
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright')
let browser
before(async()=>{browser=await chromium.launch({headless:true})})
after(async()=>{await browser?.close()})
const token=process.env.CRM_TEST_HUMAN_TOKEN
async function unlock(page){
 await page.getByLabel('API token',{exact:true}).fill(token)
 await page.getByRole('button',{name:'Unlock',exact:true}).click()
 await page.getByRole('button',{name:'Lock',exact:true}).waitFor()
}
test('native setup hides general chat, reads same CRM after approval, and relocks on refresh',async()=>{
 const page=await browser.newPage({viewport:{width:1440,height:1000}})
 page.setDefaultTimeout(5000)
 try{
  await page.addInitScript(()=>{const d=new Date(),p=n=>String(n).padStart(2,'0');localStorage.setItem('ohi-summary-seen',`${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`)})
  await page.goto(process.env.CRM_TEST_URL);await unlock(page)
  await page.getByText('Native setup · verify each request',{exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:'General chat',exact:true}).count(),0)
  await page.getByLabel('Your question',{exact:true}).fill('How many leads are there?')
  await page.getByRole('button',{name:'Read CRM',exact:true}).click()
  await page.getByText('No leads in the CRM.',{exact:true}).waitFor()
  await page.getByRole('button',{name:'Propose lead',exact:true}).click()
  await page.getByLabel('Lead request',{exact:true}).fill('Create a synthetic lead')
  await page.getByRole('button',{name:'Send proposal request',exact:true}).click()
  const review=page.getByRole('dialog',{name:'Pending approvals'})
  await review.waitFor();await review.getByLabel('Name',{exact:true}).fill('Native Edited')
  await review.getByRole('button',{name:'Approve',exact:true}).click()
  await page.getByRole('region',{name:'Approved lead'}).waitFor()
  await page.getByRole('button',{name:'CRM reads',exact:true}).click()
  await page.getByLabel('Your question',{exact:true}).fill('How many leads are there?')
  await page.getByRole('button',{name:'Read CRM',exact:true}).click()
  const result=page.getByRole('region',{name:'Verified CRM result'})
  await result.getByText('Native Edited',{exact:false}).waitFor()
  await page.reload();await page.getByLabel('API token',{exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:'CRM reads',exact:true}).count(),0)
 }finally{await page.close()}
})
test('invalid workflow mode fails auth bootstrap',async()=>{
 const page=await browser.newPage()
 page.setDefaultTimeout(5000)
 try{
  await page.route('**/api/auth/status',r=>r.fulfill({json:{mode:'local',role:'human',workflow_mode:'invented'}}))
  await page.goto(process.env.CRM_TEST_URL)
  await page.getByRole('alert').waitFor()
  assert.equal(await page.getByRole('button',{name:'CRM reads',exact:true}).count(),0)
 }finally{await page.close()}
})
