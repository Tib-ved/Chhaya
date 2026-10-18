import { expect, test } from '@playwright/test'
import type { Page, Route } from '@playwright/test'

const base = 'http://127.0.0.1:8091'
const demo = {
  cards: [{ id: 'alone_dizzy', name: 'Kamla Devi', age: 72, ward_id: 'churu-ward-2', role: 'Lives alone', nudge: 'You feel dizzy.' }],
  languages: [{ code: 'hi', label: 'Hindi' }, { code: 'en', label: 'English' }],
}
function feed(overrides: Record<string, unknown> = {}) {
  return {
    call_id: 'c1', status: 'in_progress', started_at: '2026-10-14T10:00:00Z', ended_at: null, end_outcome: null,
    language_used: 'hi', respondent: 'self', transcript: [], questions: [], emergency: null,
    rules_so_far: [], tier_so_far: null, case: null, ...overrides,
  }
}
async function mockDemo(page: Page) {
  await page.route('**/api/demo', (r: Route) => r.fulfill({ json: demo }))
}

test('the test-call screen offers both ways', async ({ page }) => {
  await page.goto(base + '/regional/agent-tester?state=Rajasthan&district=churu')
  await expect(page.getByRole('link', { name: 'Start a microphone call' })).toBeVisible()
  await page.getByRole('link', { name: 'Get a call on your phone' }).click()
  await expect(page).toHaveURL(/\/call\?via=phone/)
})

test('the phone form sends E.164 once, then shows the call filling in', async ({ page }) => {
  await mockDemo(page)
  const bodies: unknown[] = []
  await page.route('**/api/visitor/phone-calls', async (r: Route) => {
    bodies.push(r.request().postDataJSON())
    await new Promise((done) => setTimeout(done, 300))
    await r.fulfill({ json: { call_id: 'c1' } })
  })
  let polls = 0
  await page.route('**/api/visitor/calls/c1', (r: Route) => {
    polls += 1
    const body = polls < 2
      ? feed({ status: 'dialing' })
      : feed({ transcript: [{ role: 'agent', text: 'नमस्ते, कैसी हैं आप?', at: '2026-10-14T10:00:01Z' }] })
    return r.fulfill({ json: body })
  })
  await page.goto(base + '/call?via=phone')
  const callMe = page.getByRole('button', { name: 'Call me' })
  await page.getByLabel('Country').selectOption('91')
  await page.getByLabel('Phone number').fill('12345')
  await page.getByLabel(/This is my number/).check()
  await expect(callMe).toBeDisabled()
  await page.getByLabel('Phone number').fill('098123 45678')
  await expect(callMe).toBeEnabled()
  await callMe.dblclick()
  await expect(page.getByText(/Calling ••• 5678/)).toBeVisible()
  await expect(page.getByText('नमस्ते, कैसी हैं आप?')).toBeVisible()
  expect(bodies).toEqual([{ card_id: 'alone_dizzy', language: 'hi', phone: '+919812345678' }])
  await expect(page.getByText('थोड़ा चक्कर आ रहा है')).toHaveCount(0) // no placeholder lines
})

test('a refused phone call says why and offers the browser call', async ({ page }) => {
  await mockDemo(page)
  await page.route('**/api/visitor/phone-calls', (r: Route) =>
    r.fulfill({ status: 429, json: { detail: "Today's phone calls have all been used. Try the call in your browser.", reason: 'phone_budget', retry_after_s: null } }))
  await page.goto(base + '/call?via=phone')
  await page.getByLabel('Phone number').fill('9812345678')
  await page.getByLabel(/This is my number/).check()
  await page.getByRole('button', { name: 'Call me' }).click()
  await expect(page.getByText("Today's phone calls have all been used.", { exact: false })).toBeVisible()
  await expect(page.getByRole('link', { name: /browser/i })).toBeVisible()
})

test('a phone call nobody answers ends with the unreached message', async ({ page }) => {
  await mockDemo(page)
  await page.route('**/api/visitor/phone-calls', (r: Route) => r.fulfill({ json: { call_id: 'c1' } }))
  await page.route('**/api/visitor/calls/c1', (r: Route) =>
    r.fulfill({ json: feed({ status: 'no_answer', ended_at: '2026-10-14T10:00:20Z' }) }))
  await page.goto(base + '/call?via=phone')
  await page.getByLabel('Phone number').fill('9812345678')
  await page.getByLabel(/This is my number/).check()
  await page.getByRole('button', { name: 'Call me' }).click()
  await expect(page.getByText("We couldn't reach your phone.", { exact: false })).toBeVisible()
})

test('a refused microphone is explained, with the other ways offered', async ({ page }) => {
  await mockDemo(page)
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('denied', 'NotAllowedError'))
  })
  await page.route('**/api/visitor/calls', (r: Route) => r.fulfill({ json: { call_id: 'c1', ticket: 't'.repeat(32), expires_in: 60 } }))
  await page.goto(base + '/call')
  await page.getByRole('button', { name: /Start the call/ }).click()
  await expect(page.getByText(/Allow the microphone/)).toBeVisible()
  await expect(page.getByRole('link', { name: /phone/i })).toBeVisible()
})

test('the result shows the transcript and says when the summary does not come', async ({ page }) => {
  test.setTimeout(60_000)
  await mockDemo(page)
  await page.route('**/api/visitor/phone-calls', (r: Route) => r.fulfill({ json: { call_id: 'c1' } }))
  await page.route('**/api/visitor/calls/c1', (r: Route) => r.fulfill({ json: feed({
    status: 'completed', ended_at: '2026-10-14T10:02:00Z',
    transcript: [{ role: 'agent', text: 'Goodbye', at: '2026-10-14T10:01:59Z' }],
    case: { id: 'k', tier: 'amber', reasons: ['A1'], reason_labels: ['Mild symptoms'], ward_id: 'w', note: null },
  }) }))
  await page.clock.install()
  await page.goto(base + '/call?via=phone')
  await page.getByLabel('Phone number').fill('9812345678')
  await page.getByLabel(/This is my number/).check()
  await page.getByRole('button', { name: 'Call me' }).click()
  await expect(page.getByText('Amber', { exact: true })).toBeVisible()
  await expect(page.getByText('Goodbye')).toBeVisible()
  await page.clock.runFor(31_000)
  await expect(page.getByText('The summary is not available')).toBeVisible()
})

test('the number is asked for without the country code', async ({ page }) => {
  await mockDemo(page)
  await page.goto(base + '/call?via=phone')
  await expect(page.getByText('Without the country code, for example 98123 45678')).toBeVisible()
  await page.getByLabel('Phone number').fill('+91 98123 45678')
  await expect(page.getByRole('alert')).toHaveText('Enter the number without the country code.')
  await page.getByLabel('Phone number').fill('981234')
  await expect(page.getByRole('alert')).toHaveText('') // not while the visitor is still typing
  await expect(page.getByLabel('Phone number')).toHaveAttribute('aria-describedby', 'phone-hint')
  await page.getByLabel('Phone number').blur()
  await expect(page.getByRole('alert')).toHaveText('This does not look like a number in that country.')
})

test('the microphone problem offers only the phone call', async ({ page }) => {
  await mockDemo(page)
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('denied', 'NotAllowedError'))
  })
  await page.route('**/api/visitor/calls', (r: Route) => r.fulfill({ json: { call_id: 'c1', ticket: 't'.repeat(32), expires_in: 60 } }))
  await page.goto(base + '/call')
  await page.getByRole('button', { name: /Start the call/ }).click()
  await expect(page.getByRole('alert')).toHaveText('Allow the microphone for this site and try again, or get a call on your phone.')
  await expect(page.getByText(/recorded call/i)).toHaveCount(0)
})

test('a phone call that never connects is given up after 90 seconds', async ({ page }) => {
  test.setTimeout(60_000)
  await mockDemo(page)
  await page.route('**/api/visitor/phone-calls', (r: Route) => r.fulfill({ json: { call_id: 'c1' } }))
  let polls = 0
  await page.route('**/api/visitor/calls/c1', (r: Route) => {
    polls += 1
    return r.fulfill({ json: feed({ status: 'dialing' }) })
  })
  await page.clock.install()
  await page.goto(base + '/call?via=phone')
  await page.getByLabel('Phone number').fill('9812345678')
  await page.getByLabel(/This is my number/).check()
  await page.getByRole('button', { name: 'Call me' }).click()
  await expect(page.getByText(/Calling ••• 5678/)).toBeVisible()
  await page.clock.runFor(91_000)
  await expect(page.getByText("We couldn't reach your phone. Check the number and try again, or use the browser call.")).toBeVisible()
  await expect(page.getByRole('link', { name: /browser/i })).toBeVisible()
  const after = polls
  await page.clock.runFor(5_000)
  expect(polls).toBe(after) // polling has stopped
})

test('a phone call that connects and runs past a minute ends as completed and waits for the note', async ({ page }) => {
  test.setTimeout(60_000)
  await mockDemo(page)
  await page.route('**/api/visitor/phone-calls', (r: Route) => r.fulfill({ json: { call_id: 'c1' } }))
  let phase: 'dialing' | 'in_progress' | 'completed' | 'note' = 'dialing'
  let polls = 0
  await page.route('**/api/visitor/calls/c1', (r: Route) => {
    polls += 1
    if (phase === 'dialing') return r.fulfill({ json: feed({ status: 'dialing' }) })
    if (phase === 'in_progress') return r.fulfill({ json: feed() })
    const kase = { id: 'k', tier: 'amber', reasons: ['A1'], reason_labels: ['Mild symptoms'], ward_id: 'w', note: phase === 'note' ? 'Feels dizzy; visit today.' : null }
    return r.fulfill({ json: feed({ status: 'completed', ended_at: '2026-10-14T10:01:10Z', case: kase }) })
  })
  await page.clock.install()
  await page.goto(base + '/call?via=phone')
  await page.getByLabel('Phone number').fill('9812345678')
  await page.getByLabel(/This is my number/).check()
  await page.getByRole('button', { name: 'Call me' }).click()
  await expect(page.getByText(/Calling ••• 5678/)).toBeVisible()
  phase = 'in_progress'
  await page.clock.runFor(2_000)
  await expect(page.getByText('On the phone with Chhaya')).toBeVisible()
  await page.clock.runFor(65_000)
  await expect(page.getByText('On the phone with Chhaya')).toBeVisible() // a connected call is never given up
  phase = 'completed'
  await page.clock.runFor(2_000)
  await expect(page.getByText(/^Completed ·/)).toBeVisible()
  await expect(page.getByText("We couldn't reach your phone", { exact: false })).toHaveCount(0)
  await expect(page.getByText('Writing the summary…')).toBeVisible()
  const before = polls
  phase = 'note'
  await page.clock.runFor(3_000)
  expect(polls).toBeGreaterThan(before) // still polling for the note
  await expect(page.getByText('Feels dizzy; visit today.')).toBeVisible()
})

test('a refused call shows the wait from retry_after_s', async ({ page }) => {
  await mockDemo(page)
  await page.route('**/api/visitor/phone-calls', (r: Route) =>
    r.fulfill({ status: 429, json: { detail: 'You have made enough test calls for now.', reason: 'rate_limited', retry_after_s: 125 } }))
  await page.goto(base + '/call?via=phone')
  await page.getByLabel('Phone number').fill('9812345678')
  await page.getByLabel(/This is my number/).check()
  await page.getByRole('button', { name: 'Call me' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'enough test calls' })).toHaveText('You have made enough test calls for now. Try again in 3 minutes.')
})

// A browser call with the microphone, the audio graph and the socket faked in the page.
async function fakeBrowserAudio(page: Page, endStatus: string | null) {
  await page.addInitScript((status) => {
    const w = window as any
    navigator.mediaDevices.getUserMedia = () => Promise.resolve({ getTracks: () => [{ stop() {} }] } as any)
    w.AudioContext = class {
      audioWorklet = { addModule: () => Promise.resolve() }
      resume() { return Promise.resolve() }
      close() { return Promise.resolve() }
      createMediaStreamSource() { return { connect() {} } }
    }
    w.AudioWorkletNode = class { port: any = {} }
    w.WebSocket = class {
      static OPEN = 1
      readyState = 1
      binaryType = ''
      onopen: any; onmessage: any; onclose: any; onerror: any
      constructor() {
        setTimeout(() => {
          this.onopen?.()
          if (status) setTimeout(() => this.onmessage?.({ data: JSON.stringify({ type: 'ended', status }) }), 50)
        }, 20)
      }
      send(data: unknown) { if (typeof data === 'string') (w.sentText ??= []).push(data) }
      close() {}
    }
  }, endStatus)
}

test('a browser call that ends failed says it did not connect, not that the phone was missed', async ({ page }) => {
  await mockDemo(page)
  await fakeBrowserAudio(page, 'failed')
  await page.route('**/api/visitor/calls', (r: Route) => r.fulfill({ json: { call_id: 'c1', ticket: 't'.repeat(32), expires_in: 60 } }))
  await page.route('**/api/visitor/calls/c1', (r: Route) =>
    r.fulfill({ json: feed({ status: 'failed', ended_at: '2026-10-14T10:00:05Z' }) }))
  await page.goto(base + '/call')
  await page.getByRole('button', { name: /Start the call/ }).click()
  await expect(page.getByText('The call did not connect. Try again, or get a call on your phone.')).toBeVisible()
  await expect(page.getByText(/reach your phone/)).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Get a call on your phone' })).toHaveAttribute('href', '/call?via=phone')
})

test('a double click on Start the call makes one call', async ({ page }) => {
  await mockDemo(page)
  await fakeBrowserAudio(page, null)
  let posts = 0
  await page.route('**/api/visitor/calls', async (r: Route) => {
    posts += 1
    await new Promise((done) => setTimeout(done, 300))
    await r.fulfill({ json: { call_id: 'c1', ticket: 't'.repeat(32), expires_in: 60 } })
  })
  await page.route('**/api/visitor/calls/c1', (r: Route) => r.fulfill({ json: feed() }))
  await page.goto(base + '/call')
  await page.getByRole('button', { name: /Start the call/ }).dblclick()
  await expect(page.getByRole('status')).toBeVisible()
  expect(posts).toBe(1)
})

test('a browser caller who cannot speak can press for help, once', async ({ page }) => {
  await mockDemo(page)
  await fakeBrowserAudio(page, null)
  await page.route('**/api/visitor/calls', (r: Route) => r.fulfill({ json: { call_id: 'c1', ticket: 't'.repeat(32), expires_in: 60 } }))
  await page.route('**/api/visitor/calls/c1', (r: Route) => r.fulfill({ json: feed() }))
  await page.goto(base + '/call')
  await page.getByRole('button', { name: /Start the call/ }).click()
  const help = page.getByRole('button', { name: "Can't speak? Press for help" })
  await expect(help).toBeEnabled()
  await help.click()
  const asked = page.getByRole('button', { name: /Help asked for/ })
  await expect(asked).toBeDisabled()
  await asked.click({ force: true })
  expect(await page.evaluate(() => (window as any).sentText)).toEqual([JSON.stringify({ type: 'help' })])
  await expect(page.getByRole('button', { name: 'End call' })).toBeVisible() // the call goes on
})
