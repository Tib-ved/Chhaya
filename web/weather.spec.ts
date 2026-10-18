// The Weather tab, the overview's weather line and the Live/Demo switch, against a fully mocked /api.
// Runs against the local server on port 8091 for the pages only; every /api/ call is answered here.
import { expect, test } from '@playwright/test'
import type { Page, Request } from '@playwright/test'

const base = 'http://127.0.0.1:8091'
test.use({ viewport: { width: 1440, height: 1000 } })

const DISTRICTS = [
  { id: 'churu', name: 'Churu', peak_c: 36.8, peak_hour: 15, alert: 'red' },
  { id: 'sikar', name: 'Sikar', peak_c: 35.1, peak_hour: 15, alert: 'orange' },
  { id: 'jhunjhunu', name: 'Jhunjhunu', peak_c: 33.9, peak_hour: 14, alert: null },
  { id: 'bikaner', name: 'Bikaner', peak_c: 31.8, peak_hour: 15, alert: null },
]
const REGION = { districts: DISTRICTS.map(d => ({ id: d.id, name: d.name, state: 'Rajasthan', wards: 0, residents: 0, high_risk: 0, living_alone: 0, aged_75_plus: 0, warning: null, round: null })) }
const districtView = (id: string) => {
  const d = DISTRICTS.find(x => x.id === id) || DISTRICTS[0]
  return { district: { id: d.id, name: d.name, state: 'Rajasthan' }, warning: null, as_of: '2026-10-15T08:30:00Z', round: null, wards: [], problems: [], progress: null, forecast: null, attention: [], residents: [] }
}
const ICON = 'https://maps.gstatic.com/weather/v1/sunny'
const HOURS = Array.from({ length: 15 }, (_, i) => {
  const hour = 6 + i
  const temp = Math.round((24 + 12.8 * Math.sin(Math.PI * Math.min(1, i / 9) / 2)) * 10) / 10
  return { hour, temp_c: temp, feels_like_c: temp, heat_index_c: temp, humidity_pct: 20, rain_pct: 0, condition_type: 'CLEAR', condition_text: 'Sunny', icon_uri: ICON, is_day: true }
})
const DAYS = ['2026-10-15', '2026-10-16', '2026-10-17', '2026-10-18', '2026-10-19'].map((date, i) => ({ date, max_c: 36.8 - i, min_c: 22.1, condition_type: 'CLEAR', condition_text: 'Sunny', icon_uri: ICON, rain_pct: 0 }))
const REASON = 'Hottest in Rajasthan today: 36.8 °C at 15:00, 2.4 °C above the state average'
const WEATHER = {
  area: { kind: 'district', id: 'churu', name: 'Churu', state: 'Rajasthan' },
  source: 'google_weather',
  fetched_at: '2026-10-15T08:30:00Z',
  now: { hour: 14, temp_c: 35.2, feels_like_c: 36.0, condition_text: 'Sunny', icon_uri: ICON, is_day: true },
  today: { max_c: 36.8, min_c: 22.1, humidity_pct: 18, rain_pct: 0 },
  hours: HOURS,
  days: DAYS,
  state: { name: 'Rajasthan', mean_peak_c: 34.4, max_peak_c: 36.8, districts: DISTRICTS },
  alert: { severity: 'red', reason_text: REASON, raw: {} },
  unavailable_since: null,
}
const UNAVAILABLE = { ...WEATHER, now: null, today: null, hours: [], days: [], alert: null, fetched_at: '2026-10-01T08:30:00Z', unavailable_since: '2026-10-01T00:30:00Z' }
const istTime = (d: Date) => new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false }).format(d)

const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`

async function mockApi(page: Page, weather: (url: URL) => unknown = url => url.searchParams.get('district_id') ? WEATHER : { ...WEATHER, area: { kind: 'state', id: 'Rajasthan', name: 'Rajasthan', state: 'Rajasthan' } }) {
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (path === '/api/region') return route.fulfill({ json: REGION })
    if (path.startsWith('/api/districts/')) return route.fulfill({ json: districtView(path.split('/').pop()!) })
    if (path === '/api/weather') return route.fulfill({ json: weather(url) })
    if (path === '/api/ask/sessions') return route.request().method() === 'POST'
      ? route.fulfill({ status: 201, json: { session_id: 's_test', created_at: '2026-10-15T08:30:00Z' } })
      : route.fulfill({ json: [] })
    if (path === '/api/ask/sessions/s_test') return route.fulfill({ json: { session_id: 's_test', title: 'Hottest district', focus: {}, messages: [] } })
    if (path === '/api/ask/sessions/s_test/messages') return route.fulfill({ headers: { 'Content-Type': 'text/event-stream' },
      body: frame('delta', { text: 'Churu is the hottest.' }) + frame('done', { data_as_of: '2026-10-15T08:30:00Z', kind: 'answer' }) })
    return route.fulfill({ json: {} })
  })
}

test('the Weather menu item opens the card: now, today, 15 hours, 5 days and the source', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-10-15T09:00:00Z'))  // 14:30 IST, the day of the fetch
  await mockApi(page)
  await page.goto(base + '/regional/overview?state=Rajasthan&district=churu')
  await page.getByRole('navigation', { name: 'Main Navigation' }).getByRole('link', { name: 'Weather' }).click()
  await expect(page).toHaveURL(/\/regional\/weather\?/)
  const card = page.getByRole('region', { name: 'Weather in Churu' })
  await expect(card.locator('.wx-now-temp')).toHaveText('35.2°C')
  await expect(card.locator('.wx-now')).toContainText('Sunny')
  await expect(card.locator('.wx-now')).toContainText('Feels like 36°C')
  await expect(card.getByRole('list', { name: 'Hourly forecast' }).getByRole('listitem')).toHaveCount(15)
  await expect(card.getByRole('list', { name: 'Next 5 days' }).getByRole('listitem')).toHaveCount(5)
  await expect(card).toContainText('Google Weather · updated 14:00 IST')
  await expect(card.locator('.wx-earlier')).toHaveCount(0)
  await expect(card.getByRole('img', { name: /Hourly temperature, Churu/ })).toBeVisible()
  await expect(card.getByText('State mean peak 34.4°C')).toBeVisible()
  await expect(card.getByText('Hottest in state 36.8°C')).toBeVisible()
})

test('at state level the districts chart lists the 4 districts, the red one marked', async ({ page }) => {
  await mockApi(page)
  await page.goto(base + '/regional/weather?state=Rajasthan')
  const chart = page.getByRole('list', { name: 'Forecast peak by district' })
  await expect(chart.getByRole('listitem')).toHaveCount(4)
  await expect(chart.getByRole('listitem').filter({ hasText: 'Churu' })).toContainText('Red heat warning')
  await expect(chart.getByRole('listitem').filter({ hasText: 'Sikar' })).toContainText('Amber heat warning')
  await expect(chart.getByRole('listitem').filter({ hasText: 'Bikaner' })).not.toContainText('alert')
  await expect(page.getByText('Rajasthan mean 34.4°C')).toBeVisible()
})

test('the overview shows the alert with its reason and links to the Weather tab', async ({ page }) => {
  await mockApi(page)
  await page.goto(base + '/regional/overview?state=Rajasthan&district=churu')
  const line = page.locator('.wx-line')
  await expect(line).toContainText('Red heat warning · Churu')
  await expect(line).toContainText(REASON)
  await expect(line).toContainText('35.2°C now')
  await line.getByRole('link', { name: 'Weather details' }).click()
  await expect(page).toHaveURL(/\/regional\/weather\?/)
})

test('live is the default: no request carries mode=demo', async ({ page }) => {
  const urls: string[] = []
  page.on('request', (r: Request) => { if (r.url().includes('/api/')) urls.push(r.url()) })
  await mockApi(page)
  await page.goto(base + '/regional/weather?state=Rajasthan&district=churu')
  await expect(page.getByRole('region', { name: 'Weather in Churu' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Live data' })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.demo-band')).toHaveCount(0)
  expect(urls.length).toBeGreaterThan(0)
  expect(urls.filter(u => u.includes('mode='))).toEqual([])
})

test('Demo data shows the band, is remembered across a reload, and every request carries mode=demo', async ({ page }) => {
  await mockApi(page)
  await page.goto(base + '/regional/overview?state=Rajasthan&district=churu')
  await expect(page.locator('.wx-line')).toBeVisible()
  const urls: string[] = []
  page.on('request', (r: Request) => { if (r.url().includes('/api/')) urls.push(r.url()) })
  await Promise.all([page.waitForEvent('load'), page.getByRole('button', { name: 'Demo data' }).click()])
  const band = page.locator('.demo-band')
  await expect(band).toHaveText('Demo dataset: a fixed heat day for 4 Rajasthan districts.')
  await page.reload()
  await expect(band).toBeVisible()
  await expect(page.getByRole('button', { name: 'Demo data' })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('navigation', { name: 'Main Navigation' }).getByRole('link', { name: 'Weather' }).click()
  await expect(page.getByRole('region', { name: 'Weather in Churu' })).toBeVisible()
  await expect(band).toBeVisible()
  await page.locator('.workspace-topbar').getByRole('button', { name: 'Ask Chhaya' }).click()
  const drawer = page.locator('#assistant-drawer')
  await drawer.getByRole('textbox', { name: 'Ask Chhaya' }).fill('Which district is hottest?')
  await drawer.getByRole('button', { name: 'Send' }).click()
  await expect(drawer.getByText('Churu is the hottest.')).toBeVisible()
  expect(urls.some(u => u.includes('/api/weather'))).toBe(true)
  expect(urls.some(u => /\/api\/ask\/sessions\/[^/]+\/messages/.test(u))).toBe(true)
  expect(urls.filter(u => !new URL(u).searchParams.getAll('mode').includes('demo'))).toEqual([])
  // Back to live: the band goes and requests are plain again.
  await Promise.all([page.waitForEvent('load'), page.getByRole('button', { name: 'Live data' }).click()])
  await expect(page.getByRole('region', { name: 'Weather in Churu' })).toBeVisible()
  await expect(band).toHaveCount(0)
  // The conversation started on demo data is not resumed on live data: the next question opens a new session.
  const created: string[] = []
  page.on('request', (r: Request) => { if (r.method() === 'POST' && new URL(r.url()).pathname === '/api/ask/sessions') created.push(r.url()) })
  await expect(drawer.getByRole('log').getByText('Churu is the hottest.')).toHaveCount(0)
  await drawer.getByRole('textbox', { name: 'Ask Chhaya' }).fill('And on live data?')
  await drawer.getByRole('button', { name: 'Send' }).click()
  await expect(drawer.getByText('Churu is the hottest.')).toBeVisible()
  expect(created).toHaveLength(1)
  expect(created[0]).not.toContain('mode=')
})

test('demo weather says so in the footer', async ({ page }) => {
  await mockApi(page, () => ({ ...WEATHER, source: 'demo_scenario', fetched_at: null, days: [] }))
  await page.goto(base + '/regional/weather?state=Rajasthan&district=churu')
  const card = page.getByRole('region', { name: 'Weather in Churu' })
  await expect(card).toContainText('Demo scenario')
  await expect(card).not.toContainText('Google Weather')
})

test('without a forecast for today the card says since when', async ({ page }) => {
  await mockApi(page, () => UNAVAILABLE)
  await page.goto(base + '/regional/weather?state=Rajasthan&district=churu')
  await expect(page.getByRole('region', { name: 'Weather in Churu' })).toContainText('Live forecast unavailable since 1 Oct, 06:00; last update: 1 Oct, 14:00')
})

test('unavailable since earlier today says the time alone', async ({ page }) => {
  const since = new Date(Date.now() - 60_000)
  await mockApi(page, () => ({ ...UNAVAILABLE, unavailable_since: since.toISOString() }))
  await page.goto(base + '/regional/weather?state=Rajasthan&district=churu')
  await expect(page.getByRole('region', { name: 'Weather in Churu' })).toContainText(`Live forecast unavailable since ${istTime(since)};`)
})

test('an orange alert reads Amber (AGENTS.md), and no alert gives the peak against the state average', async ({ page }) => {
  await mockApi(page, () => ({ ...WEATHER, area: { kind: 'district', id: 'sikar', name: 'Sikar', state: 'Rajasthan' }, alert: { severity: 'orange', reason_text: '35.1 °C, 0.7 °C above the Rajasthan average', raw: {} } }))
  await page.goto(base + '/regional/overview?state=Rajasthan&district=sikar')
  await expect(page.locator('.wx-line')).toContainText('Amber heat warning · Sikar — 35.1 °C, 0.7 °C above the Rajasthan average')
  await page.unrouteAll()
  await mockApi(page, () => ({ ...WEATHER, area: { kind: 'district', id: 'bikaner', name: 'Bikaner', state: 'Rajasthan' }, alert: null }))
  await page.goto(base + '/regional/overview?state=Rajasthan&district=bikaner')
  await expect(page.locator('.wx-line')).toContainText('No alert · Bikaner — 31.8 °C, below the Rajasthan average of 34.4 °C')
})

test('the landing page opens the live overview and forgets a demo conversation', async ({ page }) => {
  await mockApi(page)
  await page.goto(base + '/')
  await page.evaluate(() => { localStorage.setItem('chhaya.mode', 'demo'); sessionStorage.setItem('chhaya_ask_session', 's_old') })
  await page.getByRole('link', { name: /Regional Chhaya Sevak/ }).click()
  await expect(page.getByRole('button', { name: 'Live data' })).toHaveAttribute('aria-pressed', 'true')
  expect(await page.evaluate(() => sessionStorage.getItem('chhaya_ask_session'))).toBeNull()
})

test('overnight the last day is shown with its date: on the card, its footer and the overview line', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-10-15T20:30:00Z'))  // 02:00 IST on 16 Oct, before the morning fetch
  await mockApi(page)
  await page.goto(base + '/regional/weather?state=Rajasthan&district=churu')
  const card = page.getByRole('region', { name: 'Weather in Churu' })
  await expect(card).toContainText('Google Weather · updated 15 Oct, 14:00 IST')
  await expect(card.locator('.wx-earlier')).toHaveText("Forecast from 15 Oct; today's is fetched at 06:00 IST.")
  await page.goto(base + '/regional/overview?state=Rajasthan&district=churu')
  await expect(page.locator('.wx-line')).toContainText('Forecast from 15 Oct')
})

test('Try a call from the live overview opens the demo dataset, where test calls are kept', async ({ page }) => {
  await mockApi(page)
  await page.goto(base + '/regional/overview?state=Rajasthan&district=churu')
  await expect(page.getByRole('button', { name: 'Live data' })).toHaveAttribute('aria-pressed', 'true')
  await page.locator('.head').getByRole('button', { name: 'Try a call' }).click()
  await expect(page).toHaveURL(/\/regional\/agent-tester/)
  await expect(page.getByRole('button', { name: 'Demo data' })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.demo-band')).toBeVisible()
  await expect(page.getByText('Test calls run in the demo dataset.')).toBeVisible()
  // the menu item does the same from live data
  await page.evaluate(() => localStorage.setItem('chhaya.mode', 'live'))
  await page.goto(base + '/regional/weather?state=Rajasthan&district=churu')
  await page.getByRole('navigation', { name: 'Main Navigation' }).getByRole('link', { name: 'Try a call' }).click()
  await expect(page).toHaveURL(/\/regional\/agent-tester/)
  await expect(page.getByRole('button', { name: 'Demo data' })).toHaveAttribute('aria-pressed', 'true')
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 375, height: 812 } })
  test('the Weather tab fits 375px: no page scroll sideways, the hourly strip scrolls by itself, the top bar keeps its context', async ({ page }) => {
    await mockApi(page)
    await page.goto(base + '/regional/weather?state=Rajasthan&district=churu')
    const strip = page.getByRole('list', { name: 'Hourly forecast' })
    await expect(strip).toBeVisible()
    const page_ = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
    expect(page_.scroll).toBeLessThanOrEqual(page_.client)
    const own = await strip.evaluate(el => ({ scroll: el.scrollWidth, client: el.clientWidth, overflow: getComputedStyle(el).overflowX }))
    expect(own.scroll).toBeGreaterThan(own.client)
    expect(own.overflow).toBe('auto')
    for (const name of ['Live data', 'Demo data']) {
      const box = (await page.getByRole('button', { name }).boundingBox())!
      expect(box.height).toBeGreaterThanOrEqual(44)
      expect(box.x + box.width).toBeLessThanOrEqual(375)
    }
    const ask = (await page.locator('.workspace-topbar').getByRole('button', { name: 'Ask Chhaya' }).boundingBox())!
    expect(ask.x + ask.width).toBeLessThanOrEqual(375)
    const context = (await page.locator('.topbar-context').boundingBox())!
    expect(context.width).toBeGreaterThan(300)
  })
})
