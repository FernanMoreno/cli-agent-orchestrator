import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  use: {
    browserName: 'chromium',
    // Auth forms and request bodies contain credentials: never retain them.
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
})
