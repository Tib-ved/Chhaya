// Ask Chhaya's drawer against a mocked /api/ask: the stream, charts, tables, history and errors.
// Runs against the same local server as operations.spec.ts (port 8091); only /api/ask is mocked.
import { expect, test } from '@playwright/test'
import type { Page, Route } from '@playwright/test'

const base = 'http://127.0.0.1:8091'
const region = '/regional/overview?state=RJ&district=churu'
test.use({ viewport: { width: 1440, height: 1000 } })

const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
const CHART = { type: 'bar', title: 'Red cases by ward', x_label: 'Ward', y_label: 'Cases',
  series: [{ name: 'Red cases', points: [{ label: 'Ward 1', value: 1 }, { label: 'Ward 2', value: 2 }] }] }
const TABLE = { title: 'Highest risk', columns: ['Name', 'Score'], rows: [['Kamla Devi', 7], ['Mohan Lal', 5]] }
const ANSWER = frame('status', { label: 'Analysing the numbers…' }) + frame('chart', { id: 'c1', spec: CHART })
  + frame('table', { id: 't1', spec: TABLE }) + frame('delta', { text: '**Ward 2** has the most ' })
  + frame('delta', { text: 'red cases.' }) + frame('sources', { case_ids: ['churu_heat_20261014_churu-ward-2-r-1'], call_ids: [] })
  + frame('title', { title: 'Red cases by ward' }) + frame('done', { data_as_of: '2026-10-14T06:30:00Z', kind: 'answer' })

async function mockAsk(page: Page, answer = ANSWER, status = 200) {
  const asked: { focus: unknown; text: string }[] = []
  await page.route('**/api/ask/sessions', (route: Route) => route.request().method() === 'POST'
    ? route.fulfill({ status: 201, json: { session_id: 's_test', created_at: '2026-10-14T06:30:00Z' } })
    : route.fulfill({ json: [{ session_id: 's_test', title: 'Red cases by ward', updated_at: '2026-10-14T06:30:00Z' }] }))
  await page.route('**/api/ask/sessions/s_test', route => route.fulfill({ json: { session_id: 's_test', title: 'Red cases by ward', focus: {},
    messages: [{ role: 'user', text: 'Earlier question', visuals: [] }, { role: 'assistant', text: 'Earlier answer.', visuals: [] }] } }))
  await page.route('**/api/ask/sessions/s_test/messages', async route => {
    asked.push(route.request().postDataJSON())
    await route.fulfill(status === 200
      ? { status, headers: { 'Content-Type': 'text/event-stream' }, body: answer }
      : { status, json: { detail: 'Ask Chhaya is resting for today.' } })
  })
  return asked
}

async function openAsk(page: Page) {
  await page.goto(base + region)
  await page.locator('.workspace-topbar').getByRole('button', { name: 'Ask Chhaya' }).click()
  return page.locator('#assistant-drawer')
}

test('the officer asks, and the answer streams in with a chart, a table and its cases', async ({ page }) => {
  const asked = await mockAsk(page)
  const drawer = await openAsk(page)
  await drawer.getByRole('textbox', { name: 'Ask Chhaya' }).fill('Compare red cases across the wards')
  await drawer.getByRole('button', { name: 'Send' }).click()
  const log = drawer.getByRole('log')
  await expect(log.locator('.ask-markdown')).toHaveText('Ward 2 has the most red cases.')
  await expect(log.getByRole('figure', { name: 'Red cases by ward' })).toBeVisible()
  await expect(log.getByRole('cell', { name: 'Kamla Devi' })).toBeVisible()
  await expect(log.getByRole('button', { name: /Open case churu-ward-2-r-1/ })).toBeVisible()
  expect(asked[0]).toEqual({ text: 'Compare red cases across the wards', focus: { state: 'Rajasthan', district_id: 'churu', ward_id: '' } })
})

test('a refused or failed request is shown, and the question is kept to try again', async ({ page }) => {
  await mockAsk(page, ANSWER, 429)
  const drawer = await openAsk(page)
  await drawer.getByRole('textbox', { name: 'Ask Chhaya' }).fill('How many red cases?')
  await drawer.getByRole('button', { name: 'Send' }).click()
  await expect(drawer.getByRole('alert')).toHaveText('Ask Chhaya is resting for today.')
  await expect(drawer.getByRole('textbox', { name: 'Ask Chhaya' })).toHaveValue('How many red cases?')
})

test('an error mid-answer keeps what was said so far', async ({ page }) => {
  await mockAsk(page, frame('delta', { text: 'Churu has' }) + frame('error', { code: 'timeout', message: 'That took too long; try a narrower question.' }))
  const drawer = await openAsk(page)
  await drawer.getByRole('textbox', { name: 'Ask Chhaya' }).fill('Everything about everywhere')
  await drawer.getByRole('button', { name: 'Send' }).click()
  await expect(drawer.getByText('Churu has')).toBeVisible()
  await expect(drawer.getByRole('alert')).toHaveText('That took too long; try a narrower question.')
})

test('history lists past conversations and opens one', async ({ page }) => {
  await mockAsk(page)
  const drawer = await openAsk(page)
  await drawer.getByRole('button', { name: 'History' }).click()
  await drawer.getByRole('button', { name: /Red cases by ward/ }).click()
  await expect(drawer.getByRole('log').getByText('Earlier answer.')).toBeVisible()
})

test('links and images in an answer are not rendered', async ({ page }) => {
  await mockAsk(page, frame('delta', { text: 'See [this](http://evil.example) and ![x](http://evil.example/p.png)' }) + frame('done', { data_as_of: '', kind: 'answer' }))
  const drawer = await openAsk(page)
  await drawer.getByRole('textbox', { name: 'Ask Chhaya' }).fill('Show me something')
  await drawer.getByRole('button', { name: 'Send' }).click()
  await expect(drawer.getByText('this', { exact: false })).toBeVisible()
  await expect(drawer.locator('.ask-markdown a, .ask-markdown img')).toHaveCount(0)
})

test('the field crew never sees Ask Chhaya', async ({ page }) => {
  await page.goto(base + '/ward/visits')
  await expect(page.getByRole('button', { name: 'Ask Chhaya' })).toHaveCount(0)
})

test("a conversation of the other mode is forgotten, and the next question starts a new one", async ({ page }) => {
  const made: string[] = []
  await page.route('**/api/ask/sessions', (route: Route) => {
    if (route.request().method() !== 'POST') return route.fulfill({ json: [] })
    made.push(made.length ? 's_new' : 's_other')
    return route.fulfill({ status: 201, json: { session_id: made.at(-1), created_at: '2026-10-14T06:30:00Z' } })
  })
  await page.route('**/api/ask/sessions/s_other/messages', route => route.fulfill({ status: 409,
    json: { detail: 'This conversation belongs to the demo data; start a new one.', reason: 'other_mode' } }))
  await page.route('**/api/ask/sessions/s_new/messages', route => route.fulfill({ headers: { 'Content-Type': 'text/event-stream' }, body: ANSWER }))
  const drawer = await openAsk(page)
  await drawer.getByRole('textbox', { name: 'Ask Chhaya' }).fill('Compare red cases across the wards')
  await drawer.getByRole('button', { name: 'Send' }).click()
  await expect(drawer.getByRole('alert')).toHaveText('This conversation belongs to the demo data; start a new one.')
  await drawer.getByRole('button', { name: 'Send' }).click()
  await expect(drawer.getByRole('log').locator('.ask-markdown')).toHaveText('Ward 2 has the most red cases.')
  expect(made).toEqual(['s_other', 's_new'])
})
