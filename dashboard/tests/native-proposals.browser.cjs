// SIMULATED completion only. Protected backend, agent HTTP submission, DB,
// approval edits and decisions are real. No OpenClaw/Ollama inference claimed.
const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
let browser
const token = process.env.CRM_TEST_HUMAN_TOKEN
const key = 'ohi-lead-proposal-request'
async function screenshot(page, name) {
  if (process.env.CRM_TEST_SCREENSHOTS) await page.screenshot({ path: require('node:path').join(process.env.CRM_TEST_SCREENSHOTS, name + '.png'), fullPage: true, animations: 'disabled' })
}
before(async () => { assert.ok(token); browser = await chromium.launch({ headless: true }) })
after(async () => browser?.close())
async function unlock(page) {
  await page.getByLabel('API token', { exact: true }).fill(token)
  await page.getByRole('button', { name: 'Unlock', exact: true }).click()
  await page.getByRole('button', { name: 'Lock', exact: true }).waitFor()
}
async function open() {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  page.setDefaultTimeout(5000)
  await page.addInitScript(() => {
    const d = new Date(), p = n => String(n).padStart(2, '0')
    localStorage.setItem('ohi-summary-seen', `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`)
  })
  await page.goto(process.env.CRM_TEST_URL)
  await page.getByLabel('API token', { exact: true }).waitFor()
  await screenshot(page, 'unlock')
  await unlock(page)
  await page.getByRole('button', { name: 'Propose lead', exact: true }).click()
  return page
}
async function api(page, path, data) {
  const options = { headers: { 'X-API-Token': token } }
  return data === undefined ? page.request.get(process.env.CRM_TEST_URL + '/api' + path, options)
    : page.request.post(process.env.CRM_TEST_URL + '/api' + path, { ...options, data })
}
async function submit(page, message = 'Create a synthetic lead for browser acceptance.') {
  await page.getByLabel('Lead request', { exact: true }).fill(message)
  await page.getByRole('button', { name: 'Send proposal request', exact: true }).click()
}
async function requestId(page) { return page.evaluate(k => sessionStorage.getItem(k), key) }
async function status(page) { return (await api(page, '/chat/lead-proposal/' + await requestId(page))).json() }
async function leads(page) { return (await api(page, '/leads')).json() }
const review = page => page.getByRole('dialog', { name: 'Pending approvals' })
async function deny(page) {
  await review(page).getByRole('button', { name: 'Deny', exact: true }).click()
  await review(page).getByRole('button', { name: 'Confirm deny', exact: true }).click()
  await review(page).waitFor({ state: 'hidden' })
}

test('proposal queues without a lead; human edits apply and repeated approval cannot duplicate', async () => {
  const page = await open()
  try {
    const before = (await leads(page)).length
    await page.getByText('No data yet', { exact: true }).waitFor()
    await page.getByLabel('Lead request', { exact: true }).fill('Create a synthetic lead for browser acceptance.')
    await screenshot(page, 'proposal')
    await submit(page)
    await review(page).waitFor()
    await screenshot(page, 'review')
    const pending = await status(page)
    assert.equal(pending.state, 'proposed')
    assert.equal((await leads(page)).length, before)
    assert.equal(await review(page).getByLabel('Name', { exact: true }).inputValue(), 'Synthetic Proposal')
    await review(page).getByLabel('Name', { exact: true }).fill('Human Edited Synthetic')
    await review(page).getByLabel('Email', { exact: true }).fill('edited@example.invalid')
    await review(page).getByRole('button', { name: 'Approve', exact: true }).click()
    const verified = page.getByRole('region', { name: 'Approved lead' })
    await verified.waitFor()
    await screenshot(page, 'approved')
    const approved = await status(page)
    assert.equal(approved.state, 'approved')
    assert.match(await verified.innerText(), /Human Edited Synthetic/)
    assert.match(await verified.innerText(), new RegExp('ID ' + approved.proposal.result.id))
    assert.doesNotMatch(await verified.innerText(), /999999/)
    assert.equal(approved.proposal.result.email, 'edited@example.invalid')
    const duplicate = await api(page, `/pending-changes/${pending.proposal.id}/approve`, {})
    assert.equal(duplicate.status(), 400)
    assert.equal((await leads(page)).length, before + 1)
    // Reload needs human unlock, restores the ID, and gets status without POST.
    const id = await requestId(page)
    let posts = 0
    page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/chat/lead-proposal')) posts++ })
    await page.reload(); await unlock(page)
    await page.getByRole('button', { name: 'Propose lead', exact: true }).click()
    await verified.waitFor()
    assert.equal(await requestId(page), id)
    assert.equal(posts, 0)
  } finally { await page.close() }
})

test('reload recovers pending approval and denial creates no lead', async () => {
  const page = await open()
  try {
    const before = (await leads(page)).length
    await submit(page); await review(page).waitFor()
    const id = await requestId(page)
    await page.reload(); await unlock(page)
    await review(page).waitFor()
    await deny(page)
    await page.getByRole('button', { name: 'Propose lead', exact: true }).click()
    await page.getByText('Denied. No lead was created.', { exact: true }).waitFor()
    assert.equal(await requestId(page), id)
    assert.equal((await status(page)).state, 'denied')
    assert.equal((await leads(page)).length, before)
    assert.equal(await page.getByRole('region', { name: 'Approved lead' }).count(), 0)
  } finally { await page.close() }
})

test('unknown keeps ID across checks and reload; never retries inference or saves prompt/token', async () => {
  const page = await open()
  try {
    let posts = 0
    page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/chat/lead-proposal')) posts++ })
    await submit(page, 'Synthetic unknown')
    await page.getByText(/Outcome is uncertain/).waitFor()
    const id = await requestId(page)
    await page.getByRole('button', { name: 'Check status', exact: true }).click()
    await page.getByText(/Outcome is uncertain/).waitFor()
    const storage = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }))
    assert.deepEqual(storage.session, { [key]: id })
    assert.equal(JSON.stringify(storage).includes(token), false)
    assert.equal(JSON.stringify(storage).includes('Synthetic unknown'), false)
    assert.equal(await page.getByRole('button', { name: 'New proposal request', exact: true }).count(), 0)
    await page.reload(); await unlock(page)
    await page.getByRole('button', { name: 'Propose lead', exact: true }).click()
    await page.getByText(/Outcome is uncertain/).waitFor()
    assert.equal(await requestId(page), id)
    assert.equal(posts, 1)
  } finally { await page.close() }
})

test('lost POST response and close preserve a real proposal that already won', async () => {
  const page = await open()
  try {
    let posts = 0
    await page.route('**/api/chat/lead-proposal', async route => {
      posts++
      const response = await route.fetch()
      assert.equal(response.status(), 200)
      await route.abort('failed')
    })
    await submit(page)
    await page.getByRole('alert').filter({ hasText: /Outcome is uncertain/ }).waitFor()
    const id = await requestId(page)
    await page.getByRole('button', { name: 'Close request', exact: true }).click()
    // Existing approval polling may already show the real pending record.
    const result = await status(page)
    assert.equal(result.state, 'proposed')
    await review(page).waitFor()
    await deny(page)
    await page.getByText('Denied. No lead was created.', { exact: true }).waitFor()
    assert.equal(await requestId(page), id)
    assert.equal(posts, 1)
  } finally { await page.close() }
})

test('unknown 404 retains recovery ID and cannot start a second request', async () => {
  const page = await open()
  try {
    await page.route('**/api/chat/lead-proposal', r => r.abort('failed'))
    await submit(page)
    await page.getByRole('alert').filter({ hasText: /Outcome is uncertain/ }).waitFor()
    const id = await requestId(page)
    await page.getByRole('button', { name: 'Check status', exact: true }).click()
    await page.getByText(/No stored request was found/).waitFor()
    assert.equal(await requestId(page), id)
    assert.equal(await page.getByRole('button', { name: 'New proposal request', exact: true }).count(), 0)
  } finally { await page.close() }
})

test('explicit preflight not-configured failure allows a fresh deliberate request', async () => {
  const page = await open()
  try {
    await page.route('**/api/chat/lead-proposal', r => r.fulfill({ status: 503,
      contentType: 'application/json', body: JSON.stringify({ error: { code: 'not_configured', message: 'unused' } }) }))
    await submit(page)
    await page.getByText(/Request was not started/).waitFor()
    assert.equal(await requestId(page), null)
    await page.unroute('**/api/chat/lead-proposal')
    await submit(page)
    await review(page).waitFor(); await deny(page)
  } finally { await page.close() }
})

test('completion prose without a proposal is failed and permits a deliberate new request', async () => {
  const page = await open()
  try {
    await submit(page, 'Synthetic failed')
    await page.getByText('No proposal was produced. No lead was created.', { exact: true }).waitFor()
    assert.equal(await page.getByRole('region', { name: 'Approved lead' }).count(), 0)
    await page.getByRole('button', { name: 'New proposal request', exact: true }).click()
    assert.equal(await requestId(page), null)
    assert.equal(await page.getByLabel('Lead request', { exact: true }).inputValue(), '')
  } finally { await page.close() }
})

test('malformed approved result cannot be presented as a created lead', async () => {
  const page = await open()
  try {
    await page.route('**/api/chat/lead-proposal', r => r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ request_id: r.request().postDataJSON().request_id, state: 'approved',
        proposal: { id: 1, operation: 'create_lead', status: 'approved', payload: { name: 'Fake' }, result: { id: '999999', name: 'Fake' } } }) }))
    await submit(page)
    await page.getByText(/could not be verified/).waitFor()
    assert.equal(await page.getByRole('region', { name: 'Approved lead' }).count(), 0)
    assert.match(await requestId(page), /^[a-f0-9]{32}$/)
  } finally { await page.close() }
})

test('human closes permanent unknown, then deliberately starts one new request', async () => {
  const page = await open()
  try {
    await submit(page, 'Synthetic unknown')
    await page.getByText(/Outcome is uncertain/).waitFor()
    const oldId = await requestId(page)
    await page.getByRole('button', { name: 'Close request', exact: true }).click()
    await page.getByText('No proposal was produced. No lead was created.', { exact: true }).waitFor()
    assert.equal((await status(page)).state, 'failed')
    await page.getByRole('button', { name: 'New proposal request', exact: true }).click()
    await submit(page)
    await review(page).waitFor()
    assert.notEqual(await requestId(page), oldId)
    await deny(page)
  } finally { await page.close() }
})

test('lost close response retains ID until a status check confirms retirement', async () => {
  const page = await open()
  try {
    await page.route('**/api/chat/lead-proposal', r => r.abort('failed'))
    await submit(page)
    await page.getByRole('alert').filter({ hasText: /Outcome is uncertain/ }).waitFor()
    const id = await requestId(page)
    await page.route('**/api/chat/lead-proposal/*/close', async route => {
      assert.equal((await route.fetch()).status(), 200)
      await route.abort('failed')
    })
    await page.getByRole('button', { name: 'Close request', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: /Outcome is uncertain/ }).waitFor()
    assert.equal(await requestId(page), id)
    assert.equal(await page.getByRole('button', { name: 'New proposal request', exact: true }).count(), 0)
    await page.getByRole('button', { name: 'Check status', exact: true }).click()
    await page.getByText('No proposal was produced. No lead was created.', { exact: true }).waitFor()
    await page.getByRole('button', { name: 'New proposal request', exact: true }).waitFor()
  } finally { await page.close() }
})
