// Visual smoke test: drives the locally installed Chrome via playwright-core and
// captures each page at desktop and phone widths. Usage:
//   node scripts/screenshots.mjs <outDir> [baseUrl]
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

const out = process.argv[2] ?? 'screenshots'
const base = process.argv[3] ?? 'http://localhost:5173'
mkdirSync(out, { recursive: true })

const CHROME = process.env.CHROME_PATH ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--use-angle=d3d11', '--enable-gpu', '--ignore-gpu-blocklist'] })

const pages = [
  ['landing', '/', 4500],
  ['dashboard', '/dashboard?region=karnataka', 9000],
  ['analysis', '/analysis?region=karnataka', 5000],
  ['detections', '/detections', 6000],
  ['methodology', '/methodology', 2500],
]
const viewports = [
  ['desktop', { width: 1440, height: 900 }],
  ['phone', { width: 390, height: 844 }],
]
const problems = []
for (const [vpName, vp] of viewports) {
  const ctx = await browser.newContext({ viewport: vp, deviceScaleFactor: 1 })
  for (const [name, path, wait] of pages) {
    const page = await ctx.newPage()
    page.on('console', (m) => m.type() === 'error' && problems.push(`[${vpName}/${name}] console: ${m.text().slice(0, 240)}`))
    page.on('pageerror', (e) => problems.push(`[${vpName}/${name}] pageerror: ${String(e).slice(0, 240)}`))
    await page.goto(base + path, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(wait)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    if (overflow > 1) problems.push(`[${vpName}/${name}] horizontal overflow ${overflow}px`)
    await page.screenshot({ path: join(out, `${vpName}-${name}.png`) })
    await page.close()
  }
  await ctx.close()
}
// ---------------------------------------------------------------- interactive states
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => problems.push(`[interactive] pageerror: ${String(e).slice(0, 240)}`))
  await page.goto(base + '/dashboard?region=karnataka', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(8000)
  // Select a risk cell by clicking the map near central Karnataka.
  const box = await page.locator('canvas.maplibregl-canvas').boundingBox()
  await page.mouse.click(box.x + box.width * 0.47, box.y + box.height * 0.62)
  await page.waitForTimeout(4500)
  await page.screenshot({ path: join(out, 'interactive-cell.png') })
  await page.getByRole('radio', { name: /Composite/ }).check()
  await page.waitForTimeout(1500)
  await page.screenshot({ path: join(out, 'interactive-composite.png') })
  await page.getByRole('button', { name: 'Close panel' }).click().catch(() => undefined)
  await page.getByRole('button', { name: /3D/ }).click()
  await page.getByRole('radio', { name: /Fire Weather Index/ }).check()
  await page.getByRole('switch', { name: /Extrude/ }).click()
  await page.waitForTimeout(7000)
  await page.screenshot({ path: join(out, 'interactive-3d.png') })
  await ctx.close()
}
await browser.close()
console.log(problems.length ? problems.join('\n') : 'no console errors, no horizontal overflow')
