import { defineConfig } from '@playwright/test'
export default defineConfig({ testDir: '.', testMatch: ['ask.spec.ts', 'operations.spec.ts', 'call.spec.ts', 'weather.spec.ts', 'evidence.spec.ts'], use: { channel: 'chrome' } })
