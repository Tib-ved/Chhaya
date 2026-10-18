import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const base = 'http://127.0.0.1:8091'
const region = '/regional/overview?state=RJ&district=churu'
test.use({ viewport: { width: 1440, height: 1000 } })

async function pilot(page: Page) {
  await page.goto(base + region)
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible()
}

async function firstCase(page: Page) {
  const data = await (await page.request.get(`${base}/api/ops/overview?state_id=RJ&district_id=churu`)).json()
  return data.cases.find((c: { tier: string; status: string }) => c.tier === 'red' && c.status === 'open')
}

function caseOpener(page: Page, name: string) {
  return page.locator('tr').filter({ hasText: name }).getByRole('button', { name: 'Open case' })
}

async function openAttentionCase(page: Page) {
  const person = await firstCase(page)
  await page.goto(base + '/regional/critical?state=RJ&district=churu')
  await caseOpener(page, person.resident_name).click()
  await expect(page.getByRole('dialog').getByRole('heading', { name: person.resident_name, exact: true })).toBeVisible()
  return person
}

test('web-app role gateway and side navigation remain the entry experience', async ({ page }) => {
  await page.goto(base)
  await expect(page.getByRole('img', { name: 'Chhaya — A call that brings you shade' })).toBeVisible()
  await page.getByRole('link', { name: /Regional Officer/ }).click()
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main Navigation' })).toBeVisible()
  await expect(page.locator('.workspace-topbar').getByRole('button', { name: 'Ask Chhaya' })).toBeVisible()
})

test('geographic scope survives sidebar navigation and shows honest empty states', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await pilot(page)
  await page.getByRole('combobox', { name: 'Ward', exact: true }).selectOption('ward-a')
  await page.getByRole('link', { name: 'Situation report', exact: true }).click()
  await expect(page).toHaveURL(/ward=ward-a/)
  await expect(page.getByRole('combobox', { name: 'Ward', exact: true })).toHaveValue('ward-a')
  await page.getByRole('combobox', { name: 'State', exact: true }).selectOption('TS')
  await expect(page.getByRole('heading', { name: 'No calls or visits recorded here yet' })).toBeVisible()
  await page.getByRole('link', { name: /Coverage/ }).click()
  await expect(page).toHaveURL(/\/regional\/districts\?/)
  await page.getByRole('combobox', { name: 'District', exact: true }).selectOption({ label: 'Hyderabad' })
  await expect(page.getByText('No wards or residents have been added for this district.')).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('combobox', { name: 'State', exact: true })).toHaveValue('TS')
  expect(errors).toEqual([])
})

test('clicking a resident opens an in-place modal with keyboard dismissal', async ({ page }) => {
  await pilot(page)
  const person = await openAttentionCase(page)
  const before = new URL(page.url()).pathname
  expect(before).toBe('/regional/critical')
  await page.getByRole('dialog').getByRole('button', { name: 'transcript', exact: true }).click()
  await expect(page.getByRole('dialog').getByText('Started', { exact: false }).first()).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: 'timeline', exact: true }).click()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(caseOpener(page, person.resident_name)).toBeFocused()
  expect(new URL(page.url()).pathname).toBe(before)
})

test('resident dialogs also work from directory and critical pages without leaving them', async ({ page }) => {
  await pilot(page)
  const person = await firstCase(page)
  await page.getByRole('link', { name: /Coverage/ }).click()
  const path = new URL(page.url()).pathname
  await page.getByRole('button', { name: person.resident_name, exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(new URL(page.url()).pathname).toBe(path)
  await page.getByRole('button', { name: 'Close case', exact: true }).click()
  await page.getByRole('link', { name: /Urgent cases/ }).click()
  const row = page.locator('tr').filter({ hasText: person.resident_name })
  await row.getByRole('button', { name: 'Open case' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(new URL(page.url()).pathname).toBe('/regional/critical')
})

test('admin approval, notification simulation and crew visit retain backend history', async ({ page }) => {
  await pilot(page)
  const person = await openAttentionCase(page)
  const worker = await page.getByRole('dialog').getByRole('combobox', { name: 'Crew member' }).inputValue()
  await page.getByRole('dialog').getByRole('button', { name: 'Review assignment' }).click()
  await expect(page.getByRole('dialog').getByText('This is a simulated demo action.')).toBeVisible()
  await page.getByRole('button', { name: 'Confirm demo action' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await openAttentionCase(page)
  await page.getByRole('dialog').getByRole('button', { name: 'Review resident call' }).click()
  await page.getByRole('button', { name: 'Confirm demo action' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.getByRole('link', { name: /Assignments & updates/ }).click()
  await expect(page.locator('.ops-proposal').filter({ hasText: 'simulated' }).first()).toBeVisible()
  await page.getByRole('button', { name: 'Switch to Field crew' }).click()
  await page.getByRole('button', { name: 'EN', exact: true }).click()
  await page.getByText('Demo: switch worker', { exact: true }).click()
  await page.getByRole('combobox', { name: 'Crew member', exact: true }).selectOption(worker)
  await page.getByRole('link', { name: /Today’s Visits/ }).click()
  const card = page.locator('.workspace-page .card').filter({ hasText: person.resident_name })
  await card.getByRole('button', { name: 'Open', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Accept assignment' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Mark arrived' }).click()
  await page.getByRole('dialog').getByLabel('Visit note').fill('Synthetic test visit; water supplied.')
  await page.getByRole('dialog').getByRole('button', { name: 'Save visit outcome' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'timeline', exact: true }).click()
  await expect(page.getByRole('dialog').getByText('visit completed', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('dialog').getByText('visited', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Close case', exact: true }).click()
  await page.getByRole('link', { name: 'Ward Overview', exact: true }).click()
  await page.getByRole('button', { name: 'हिन्दी', exact: true }).click()
  await expect(page.getByText(/में से 1 मुलाकात पूरी हुई$/)).toBeVisible()
})

test('overview lists wards needing attention and charts carry no table button', async ({ page }) => {
  await pilot(page)
  await expect(page.getByRole('heading', { name: 'Wards needing attention' })).toBeVisible()
  const ward = page.locator('.attn button').first()
  await expect(ward).toContainText('Ward')
  await expect(ward).toContainText('Red not visited')
  await expect(page.getByRole('button', { name: /Show as table/ })).toHaveCount(0)
  await ward.click()
  await expect(page).toHaveURL(/\/regional\/districts\?.*ward=/)
  await expect(page.getByRole('navigation', { name: 'Breadcrumbs' })).toContainText('Rajasthan')
})

test('Hindi visit list shows resident names and call summaries in Hindi', async ({ page }) => {
  await page.goto(base + '/ward/visits')
  await page.getByRole('button', { name: 'हिन्दी', exact: true }).click()
  await expect(page.getByText(/^लता शेखावत, 78$/)).toBeVisible()
  await expect(page.getByText('Lata Shekhawat', { exact: false })).toHaveCount(0)
})

test('top bar stays pinned and Ask Chhaya opens a fixed right-side drawer', async ({ page }) => {
  await pilot(page)
  const ask = page.locator('.workspace-topbar').getByRole('button', { name: 'Ask Chhaya' })
  const drawer = page.locator('#assistant-drawer')
  await expect(drawer).toBeHidden()
  await ask.click()
  await expect(drawer).toBeVisible()
  await expect(ask).toHaveCount(0)
  await page.mouse.wheel(0, 1500)
  await page.waitForTimeout(300)
  expect((await page.locator('.workspace-topbar').boundingBox())!.y).toBe(0)
  expect((await drawer.boundingBox())!.y).toBe(0)
  await page.keyboard.press('Escape')
  await expect(drawer).toBeHidden()
  await expect(ask).toBeFocused()
  await ask.click()
  await expect(drawer.getByRole('heading', { name: 'Ask Chhaya', exact: true })).toBeFocused()
  await drawer.getByRole('button', { name: 'Close Ask Chhaya' }).click()
  await expect(ask).toBeFocused()
  await page.reload()
  await expect(ask).toBeVisible()
  await expect(drawer).toBeHidden()
})

test('real microphone flow is retained separately from the scripted tester', async ({ page }) => {
  await page.goto(base + '/regional/agent-tester')
  await expect(page.getByText('Talk to Chhaya as a resident would.', { exact: false })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Get a call on your phone' })).toHaveAttribute('href', '/call?via=phone')
  await expect(page.getByRole('link', { name: 'Start a microphone call' })).toHaveAttribute('href', '/call')
  await page.getByRole('link', { name: 'Start a microphone call' }).click()
  await expect(page).toHaveURL(base + '/call')
  await expect(page.locator('.chhaya-brand img:visible').first()).toBeVisible()
})

test('unassessed residents open a record modal rather than an error or false Green case', async ({ page }) => {
  await pilot(page)
  const data = await (await page.request.get(`${base}/api/ops/overview?district_id=churu`)).json()
  const resident = data.residents.find((r: { case_id: string | null }) => !r.case_id)
  await page.getByRole('link', { name: /Coverage/ }).click()
  await expect(page).toHaveURL(/\/regional\/districts/)
  await page.getByRole('button', { name: resident.name, exact: true }).click()
  await expect(page.getByRole('dialog').getByText('Not yet assessed', { exact: true })).toBeVisible()
  expect(new URL(page.url()).pathname).toBe('/regional/districts')
})

test('ward map uses the same resident modal with keyboard support', async ({ page }) => {
  await page.goto(base + '/ward/map?state=RJ&district=churu')
  await page.getByRole('button', { name: 'EN', exact: true }).click()
  const pin = page.getByRole('button', { name: /^Open resident / }).first()
  await pin.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(new URL(page.url()).pathname).toBe('/ward/map')
  await page.keyboard.press('Escape')
  await expect(pin).toBeFocused()
})

test('mobile sidebar can be closed with Escape and restores focus', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await pilot(page)
  const toggle = page.getByRole('button', { name: 'Toggle navigation menu' })
  await toggle.click()
  await expect(page.getByRole('dialog', { name: 'Workspace navigation' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(toggle).toBeFocused()
})

test('brand assets remain consistent across the integrated screens', async ({ page }) => {
  for (const path of ['/', region, '/regional/districts?state=RJ&district=churu', '/regional/critical', '/regional/report', '/regional/team', '/regional/dispatch', '/ward/overview', '/ward/visits', '/ward/map', '/call', '/evidence', '/team']) {
    await page.goto(base + path)
    const logo = page.locator('.chhaya-brand img:visible').first()
    await expect(logo, path).toBeVisible()
    await expect.poll(() => logo.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0), { message: path }).toBe(true)
    expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--color-brand').trim())).toBe('#146536')
  }
})

test('product copy is plain and does not claim unmeasured safety results', async ({ page }) => {
  await page.goto(base)
  await expect(page.getByRole('heading', { name: 'Check on people at home during heat and cold waves.' })).toBeVisible()
  await expect(page.getByText('Deterministic Triage', { exact: true })).toHaveCount(0)
  await page.goto(base + '/regional/report?state=RJ&district=churu')
  await expect(page.getByRole('heading', { name: 'About safety testing' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'How we test Chhaya' })).toHaveAttribute('href', '/evidence')
  await expect(page.getByText('Not measured in this report', { exact: true })).toHaveCount(0)
  await expect(page.getByText('98.4% (Mukta / Indic voice model benchmark)', { exact: true })).toHaveCount(0)
})

test('Hindi ward overview is assigned-area only and has translated visit reasons', async ({ page }) => {
  await page.goto(base + '/ward/overview?state=MH&district=mh-pune&ward=unrelated')
  await expect(page.getByRole('heading', { name: 'मेरे क्षेत्र की स्थिति' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'State', exact: true })).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: 'District', exact: true })).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: 'Ward', exact: true })).toHaveCount(0)
  await expect(page.locator('.workspace-shell')).toHaveAttribute('lang', 'hi')
  const reasons = page.locator('section').filter({ has: page.getByRole('heading', { name: 'मिलने की वजहें', exact: true }) })
  await expect(reasons.getByText('अकेले रहते हैं और उम्र 70 साल या उससे ज़्यादा है', { exact: true })).toBeVisible()
  await expect(reasons.getByText('Alone and aged 70 or over', { exact: true })).toHaveCount(0)
  for (const label of await reasons.locator('.hb-label').allTextContents()) expect(label).toMatch(/[\u0900-\u097f]/)
  expect(await reasons.locator('.hb-label').first().evaluate(node => getComputedStyle(node).whiteSpace)).toBe('normal')
  expect(await page.evaluate(async () => { await document.fonts.ready; return document.fonts.check('16px Mukta', 'हिन्दी'); })).toBe(true)
  await expect(page.getByText('मुलाकातें पूरी हुईं', { exact: false }).first()).toBeVisible()
  await page.getByRole('button', { name: 'EN', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Reasons for a visit', exact: true })).toBeVisible()
  await expect(page.getByText('Alone and aged 70 or over', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.locator('.workspace-shell')).toHaveAttribute('lang', 'en')
})

test('Hindi visit list and resident dialog keep interface labels in Hindi', async ({ page }) => {
  await page.goto(base + '/ward/visits')
  await expect(page.locator('.workspace-shell')).toHaveAttribute('lang', 'hi')
  await page.getByRole('button', { name: 'खोलें', exact: true }).first().click()
  await expect(page.getByRole('dialog')).toHaveAttribute('lang', 'hi')
  await expect(page.getByRole('dialog').getByRole('button', { name: 'बातचीत', exact: true })).toBeVisible()
  await expect(page.getByRole('dialog').getByText('गंभीर लक्षण बताए', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'विवरण बंद करें', exact: true }).click()
})

test('brand and risk colors retain readable contrast', async ({ page }) => {
  await pilot(page)
  const ratios = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement)
    const luminance = (name: string) => {
      const raw = style.getPropertyValue(name).trim().slice(1)
      const hex = raw.length === 3 ? [...raw].map(c => c + c).join('') : raw
      const rgb = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4)
      return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722
    }
    return [['--color-brand', '--color-on-brand'], ['--color-text-main', '--color-shade'], ['--color-text-muted', '--color-surface'], ['--color-amber', '--color-on-brand'], ['--color-green', '--color-on-brand'], ['--color-red', '--color-on-brand']].map(([a, b]) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05))
  })
  ratios.forEach(ratio => expect(ratio).toBeGreaterThanOrEqual(4.5))
})

for (const width of [375, 768, 1024, 1440]) {
  test(`integrated layouts fit ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    for (const path of ['/', region, '/ward/visits?state=RJ&district=churu']) {
      await page.goto(base + path)
      await expect(page.locator('.chhaya-brand:visible').first()).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    }
  })
}

test('urgent cases: each warning shows its own severity, and district and ward cards drill down', async ({ page }) => {
  await page.goto(base + '/regional/critical?state=RJ')
  const sikar = page.getByRole('button', { name: 'Show urgent cases in Sikar' })
  await expect(sikar).toContainText('Amber alert')
  await expect(page.getByRole('button', { name: 'Show urgent cases in Churu' })).toContainText('Red alert')
  await sikar.click()
  await expect(page).toHaveURL(/district=sikar/)
  const ward = page.getByRole('button', { name: /^Show urgent cases in Ward/ }).first()
  await ward.click()
  await expect(page).toHaveURL(/district=sikar&ward=sikar-ward-/)
  await expect(page.getByRole('button', { name: /^Show urgent cases in Ward/ })).toHaveCount(1)
})
