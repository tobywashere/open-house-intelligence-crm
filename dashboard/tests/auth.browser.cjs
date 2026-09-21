const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')

const HUMAN_OLD = 'human-old-' + 'h'.repeat(40)
const HUMAN_NEW = 'human-new-' + 'n'.repeat(40)
const AGENT = 'agent-' + 'a'.repeat(40)
let browser

before(async () => {
  browser = await chromium.launch({ headless: true })
})
after(async () => browser?.close())

async function openAuthPage({ delayOldMetrics = false, rejectOldMetrics = false } = {}) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  page.setDefaultTimeout(5_000)
  await page.addInitScript(() => {
    const date = new Date()
    const pad = (value) => String(value).padStart(2, '0')
    localStorage.setItem(
      'ohi-summary-seen',
      `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    )
  })
  let oldMetricsStartedResolve
  let releaseOldMetricsResolve
  const oldMetricsStarted = new Promise((resolve) => { oldMetricsStartedResolve = resolve })
  const releaseOldMetrics = new Promise((resolve) => { releaseOldMetricsResolve = resolve })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const token = request.headers()['x-api-token']
    if (path === '/api/auth/status') {
      const role = token === AGENT ? 'agent'
        : token === HUMAN_OLD || token === HUMAN_NEW ? 'human' : null
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ mode: 'capabilities', role }),
      })
    }
    if (path === '/api/metrics' && delayOldMetrics && token === HUMAN_OLD) {
      oldMetricsStartedResolve()
      await releaseOldMetrics
      return route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'expired old credential' }),
      })
    }
    if (path === '/api/metrics' && rejectOldMetrics && token === HUMAN_OLD) {
      return route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'invalid credential' }),
      })
    }
    if (path === '/api/metrics') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          active_leads: 0, high_priority: 0, followups_due: 0,
          appointments_booked: 0, avg_response_minutes: null,
          agent_mode: 'mock', cloud_llm_requests: 0,
        }),
      })
    }
    return route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'unused browser fixture route' }),
    })
  })
  await page.goto(process.env.CRM_TEST_URL || 'http://127.0.0.1:18080')
  return {
    page,
    oldMetricsStarted,
    releaseOldMetrics: () => releaseOldMetricsResolve(),
  }
}

async function unlock(page, token) {
  await page.getByLabel('API token', { exact: true }).fill(token)
  await page.getByRole('button', { name: 'Unlock', exact: true }).click()
}

test('invalid and agent credentials cannot unlock; human can lock and refresh clears it', async () => {
  const { page } = await openAuthPage()
  try {
    await page.getByLabel('API token', { exact: true }).waitFor()
    await unlock(page, 'invalid-token')
    await page.getByText(/invalid credential/i).waitFor()
    await unlock(page, AGENT)
    await page.getByText(/human credential/i).waitFor()
    await unlock(page, HUMAN_OLD)
    await page.getByRole('button', { name: 'Lock', exact: true }).waitFor()

    const stored = await page.evaluate(() => ({
      local: Object.values(localStorage),
      session: Object.values(sessionStorage),
    }))
    assert.equal([...stored.local, ...stored.session].some((value) => value.includes('human-old-')), false)

    await page.getByRole('button', { name: 'Lock', exact: true }).click()
    await page.getByLabel('API token', { exact: true }).waitFor()
    await unlock(page, HUMAN_NEW)
    await page.getByRole('button', { name: 'Lock', exact: true }).waitFor()
    await page.reload()
    await page.getByLabel('API token', { exact: true }).waitFor()
  } finally {
    await page.close()
  }
})

test('a delayed 401 from an old token does not clear a newer unlocked token', async () => {
  const { page, oldMetricsStarted, releaseOldMetrics } = await openAuthPage({ delayOldMetrics: true })
  try {
    await page.getByLabel('API token', { exact: true }).waitFor()
    await unlock(page, HUMAN_OLD)
    await oldMetricsStarted
    await page.getByRole('button', { name: 'Lock', exact: true }).click()
    await unlock(page, HUMAN_NEW)
    await page.getByRole('button', { name: 'Lock', exact: true }).waitFor()
    releaseOldMetrics()
    await page.waitForTimeout(100)
    assert.equal(await page.getByRole('button', { name: 'Lock', exact: true }).count(), 1)
    assert.equal(await page.getByLabel('API token', { exact: true }).count(), 0)
  } finally {
    releaseOldMetrics()
    await page.close()
  }
})

test('a 401 for the current credential returns the dashboard to unlock', async () => {
  const { page } = await openAuthPage({ rejectOldMetrics: true })
  try {
    await page.getByLabel('API token', { exact: true }).waitFor()
    await unlock(page, HUMAN_OLD)
    await page.getByLabel('API token', { exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: 'Lock', exact: true }).count(), 0)
  } finally {
    await page.close()
  }
})
