// The "How we test Chhaya" page against a mocked /api/eval/latest: every section from one results file,
// "Not measured yet" for a part with no result, and nothing invented when there is no file.
// Runs against the local server on port 8091 (python -m tests.operations_server) for the page only.
import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const base = 'http://127.0.0.1:8091'
const FIXTURE = JSON.parse(readFileSync(new URL('./fixtures/eval_showcase.json', import.meta.url), 'utf-8'))
test.use({ viewport: { width: 1440, height: 1000 } })

async function open(page: Page, run: unknown) {
  await page.route('**/api/eval/latest', route => route.fulfill({ json: { run } }))
  await page.goto(base + '/evidence')
  await expect(page.getByRole('heading', { name: 'How we test Chhaya', level: 1 })).toBeVisible()
}

test('every section is drawn from the results file, in story order', async ({ page }) => {
  await open(page, FIXTURE)
  const headings = ['Check-in calls with simulated residents', 'Ask Chhaya', 'Speed', 'How we got here', 'What we have not tested yet']
  for (const name of headings) await expect(page.getByRole('heading', { name, exact: true, level: 2 })).toBeVisible()
  const tops = await Promise.all(headings.map(name => page.getByRole('heading', { name, exact: true, level: 2 }).evaluate(el => el.getBoundingClientRect().top)))
  expect([...tops].sort((a, b) => a - b)).toEqual(tops)

  // The four headline figures, as counts.
  await expect(page.getByText('0 of 11', { exact: true })).toBeVisible()
  await expect(page.getByText('49 of 50', { exact: true })).toBeVisible()
  await expect(page.getByText('8 of 9', { exact: true })).toBeVisible()
  const unrecorded = "For 1 of them the moment of the flag was not recorded, so a 108 line anywhere after the resident's last words counted."
  await expect(page.getByRole('region', { name: 'Headline results' }).getByText(unrecorded, { exact: false })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Headline results' }).getByText('Flagged in 9 of 9 emergencies', { exact: false })).toBeVisible()
  await expect(page.getByText('could not be checked', { exact: false })).toHaveCount(0)
  await expect(page.getByText('13 of 15', { exact: true })).toBeVisible()
  await expect(page.getByText('%')).toHaveCount(0)

  // Models, dates and the commit come from the file, never a fallback.
  await expect(page.getByText(/Gemini 2\.5 Live/)).toHaveCount(0)
  await expect(page.getByText('gemini-3.8-flash', { exact: false }).first()).toBeVisible()
  await expect(page.getByText(/commit/i)).toHaveCount(0)
  await expect(page.getByText(/[0-9a-f]{7}(?<![0-9]{7})/)).toHaveCount(0)

  const calls = page.getByRole('region', { name: 'Check-in calls with simulated residents' })
  await expect(calls.getByRole('note', { name: 'What the tiers mean' })).toBeVisible()
  const grid = calls.getByRole('table', { name: 'Expected tier against the tier Chhaya gave' })
  await expect(grid.locator('tbody tr').filter({ hasText: 'Green' })).toContainText('8')
  await expect(calls.getByText('Hindi: 22 of 23', { exact: false })).toBeVisible()
  await expect(calls.getByText('Marwari: 17 of 17', { exact: false })).toBeVisible()
  await expect(calls.getByText('36 of 37', { exact: false })).toBeVisible()
  await expect(calls.getByText(unrecorded, { exact: false })).toBeVisible()
  await expect(calls.getByText('RED', { exact: true })).toHaveCount(0)

  // Every conversation is in a collapsed table; opening it shows a missed 108 line.
  const every = calls.locator('details')
  await expect(every).not.toHaveAttribute('open', /.*/)
  const summary = every.locator('summary')
  expect((await summary.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await summary.click()
  const chest = every.getByRole('row').filter({ hasText: 'Emergency chest pain' })
  await expect(chest).toContainText('Hindi')
  await expect(chest.getByRole('cell', { name: 'No', exact: true })).toBeVisible()
  await expect(every.getByRole('row').filter({ hasText: 'Healthy alone younger' }).getByRole('cell', { name: 'Failed', exact: true })).toBeVisible()

  const ask = page.getByRole('region', { name: 'Ask Chhaya' })
  for (const check of ['Right data used', 'Correct answer', 'No made-up claims', 'Numbers from the data', 'Safe', 'Chart or table'])
    await expect(ask.getByRole('columnheader', { name: check, exact: true })).toBeVisible()
  const wrong = ask.getByRole('row').filter({ hasText: 'What did the resident in ward 2 say' })
  await expect(wrong).toContainText('40.1 s')
  await expect(wrong.getByRole('cell', { name: 'Failed', exact: true })).toHaveCount(2)
  await expect(ask.getByText('22 of 22 refused correctly, 14 Oct, not re-run, with gemini-3.8-flash', { exact: false })).toBeVisible()

  const speed = page.getByRole('region', { name: 'Speed' })
  await expect(speed.getByText('Not measured yet')).toHaveCount(0)
  await expect(speed.getByText('0.82 s', { exact: false }).first()).toBeVisible()
  await expect(speed.getByText('11.4 s', { exact: false }).first()).toBeVisible()
  await expect(speed.getByRole('definition').filter({ hasText: 'gemini-3.8-live' })).toBeVisible()
  await expect(speed.getByText('(gemini-3.8-live)', { exact: false })).toBeVisible()
  await expect(speed.getByRole('img', { name: /Ask Chhaya time by step/ })).toBeVisible()
  await expect(speed.getByRole('listitem').filter({ hasText: 'Specialist agents 4.1 s' })).toBeVisible()

  const history = page.getByRole('region', { name: 'How we got here' })
  await expect(history.getByText('18.8 s', { exact: false })).toBeVisible()
  // Newest first; rows of the same date keep the file's order.
  const dates = await history.locator('.evidence-history-date').allTextContents()
  expect(dates).toEqual(['17 Oct', '17 Oct', '15 Oct'])
  await expect(history.locator('li').first()).toContainText("Officer's Overview")
  await expect(page.getByText(/Claude/i)).toHaveCount(0)
  const limits = page.getByRole('region', { name: 'What we have not tested yet' })
  await expect(limits.getByText('Some of the data is synthetic', { exact: false })).toBeVisible()
  await expect(limits.getByText('each evaluation was run once', { exact: false })).toBeVisible()
  await expect(limits.getByText('cost per completed call', { exact: false })).toBeVisible()
  await expect(history.getByText('8 of 9 (flag moment not recorded for 1)', { exact: false })).toBeVisible()
  await expect(page.getByRole('region', { name: 'What we have not tested yet' }).getByRole('listitem')).toHaveCount(5)
})

test('the 108 headline shows how many emergencies were flagged at all', async ({ page }) => {
  await open(page, { ...FIXTURE, voice: { ...FIXTURE.voice, emergencies: { total: 9, flagged: 8, line_spoken: 8, unknown: 0 } } })
  const headline = page.getByRole('region', { name: 'Headline results' })
  await expect(headline.getByText('8 of 8', { exact: true })).toBeVisible()
  await expect(headline.getByText('Flagged in 8 of 9 emergencies; counted over those flagged.', { exact: false })).toBeVisible()
})

test('a part with no result says it was not measured yet', async ({ page }) => {
  await open(page, { ...FIXTURE, ask: null, latency: { voice: null } })
  await expect(page.getByRole('region', { name: 'Ask Chhaya' }).getByText('Not measured yet').first()).toBeVisible()
  await expect(page.getByRole('region', { name: 'Speed' }).getByText('Not measured yet').first()).toBeVisible()
  // The refusals result is still shown, with its date.
  await expect(page.getByText('22 of 22 refused correctly, 14 Oct, not re-run', { exact: false })).toBeVisible()
  await expect(page.getByText('13 of 15', { exact: true })).toHaveCount(0)
})

test('an incomplete Ask Chhaya run says so', async ({ page }) => {
  await open(page, { ...FIXTURE, ask: { ...FIXTURE.ask, cases: 10, passed: 9, complete: false, steps_s: {} } })
  await expect(page.getByText('9 of 15', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Ask Chhaya' }).getByText('incomplete', { exact: false }).first()).toBeVisible()
})

test('with no results file the page shows only the intro', async ({ page }) => {
  await open(page, null)
  await expect(page.getByText('No results have been published for this build yet.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Speed', exact: true })).toHaveCount(0)
  await expect(page.getByText(/ of \d+/)).toHaveCount(0)
  await expect(page.getByText(/Gemini 2\.5 Live/)).toHaveCount(0)
})

test.describe('at phone width', () => {
  test.use({ viewport: { width: 375, height: 812 } })
  test('the page does not scroll sideways; wide tables scroll inside their own box', async ({ page }) => {
    await open(page, FIXTURE)
    await page.locator('details summary').click()
    const sizes = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
    expect(sizes.scroll).toBeLessThanOrEqual(sizes.client)
  })
})

const STAND_IN = 'Run with a stand-in model while Gemini on Vertex AI was congested; the deployed Ask Chhaya runs on Gemini.'
const VOICE_STAND_IN = "Simulated with a stand-in text model while Gemini on Vertex AI was congested; Chhaya's real calls run on Gemini Live."
const STAND_IN_ASK = { model: 'claude-opus-5-5', stand_in: true, small_model: 'claude-haiku-5-5', judge_model: 'gemini-3.8-flash' }
const STAND_IN_VOICE = () => ({
  ...FIXTURE.voice, model: 'claude-sonnet-5-5', stand_in: true, resident_model: 'claude-haiku-5-5',
  recorded: [{ ...FIXTURE.voice.recorded[0], model: 'claude-sonnet-5-5' }],
})

test('a stand-in Ask Chhaya run says so once per part and names no model', async ({ page }) => {
  await open(page, { ...FIXTURE, ask: { ...FIXTURE.ask, ...STAND_IN_ASK } })
  await expect(page.getByRole('region', { name: 'Ask Chhaya' }).getByText(STAND_IN, { exact: true })).toBeVisible()
  await expect(page.locator('.page-intro').getByText(STAND_IN, { exact: true })).toHaveCount(1)
  await expect(page.getByText(STAND_IN, { exact: true })).toHaveCount(2)
  const cards = page.getByRole('region', { name: 'Headline results' })
  await expect(cards.getByText(STAND_IN, { exact: false })).toHaveCount(0)
  await expect(cards.getByText('Run 17 Oct (stand-in model)', { exact: false })).toHaveCount(1)
  await expect(page.getByText(/Claude|claude-/)).toHaveCount(0)
})

test('a stand-in Ask Chhaya run marks its times and its run summary', async ({ page }) => {
  await open(page, { ...FIXTURE, ask: { ...FIXTURE.ask, ...STAND_IN_ASK } })
  const times = 'Stand-in model timings; not the deployed Gemini assistant.'
  const speed = page.getByRole('region', { name: 'Speed' })
  await expect(speed.getByText('Time per answer', { exact: false })).toContainText(times)
  await expect(speed.getByText('The median of each step', { exact: false })).toContainText(times)
  await expect(page.locator('.evidence-meta li').filter({ hasText: 'Ask Chhaya:' })).not.toContainText('claude')
})

test('a Gemini Ask Chhaya run has no stand-in sentence', async ({ page }) => {
  await open(page, FIXTURE)
  await expect(page.getByText('stand-in', { exact: false })).toHaveCount(0)
})

test('check-in calls with a stand-in model say so once per part and name no model', async ({ page }) => {
  await open(page, { ...FIXTURE, voice: STAND_IN_VOICE() })
  await expect(page.getByRole('region', { name: 'Check-in calls with simulated residents' }).getByText(VOICE_STAND_IN, { exact: true })).toBeVisible()
  await expect(page.locator('.page-intro').getByText(VOICE_STAND_IN, { exact: true })).toHaveCount(1)
  await expect(page.getByText(VOICE_STAND_IN, { exact: true })).toHaveCount(2)
  const cards = page.getByRole('region', { name: 'Headline results' })
  await expect(cards.getByText(VOICE_STAND_IN, { exact: false })).toHaveCount(0)
  await expect(cards.getByText('(stand-in model)', { exact: false })).toHaveCount(3)
  await expect(cards.getByText('Recorded 17 Oct (stand-in model)', { exact: false }).first()).toBeVisible()
  await expect(page.getByText(/Claude|claude-/)).toHaveCount(0)
  // The voice latency and the earlier pickup are not stand-ins: they keep their model.
  await expect(page.getByRole('region', { name: 'Speed' }).getByText('gemini-3.8-live', { exact: false }).first()).toBeVisible()
})

test('check-in calls recorded on Gemini have no stand-in sentence', async ({ page }) => {
  await open(page, FIXTURE)
  await expect(page.getByText('stand-in text model', { exact: false })).toHaveCount(0)
})
