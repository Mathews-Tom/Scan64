import type { Page, TestInfo } from '@playwright/test';

// Screenshots attached to e2e tests are evidence only; every check is an
// assertion. Under load Chromium can refuse the capture of an emulated touch
// page. Only that refusal is recorded (as a text attachment, an annotation and a
// warning in the run log, so a run can count refusals); any other error fails.
const CAPTURE_REFUSED = /Page\.captureScreenshot\): Unable to capture screenshot/;

export async function attachScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
  options: { fullPage?: boolean } = {},
): Promise<void> {
  try {
    await testInfo.attach(name, { body: await page.screenshot(options), contentType: 'image/png' });
  } catch (error) {
    if (!(error instanceof Error) || !CAPTURE_REFUSED.test(error.message)) throw error;
    testInfo.annotations.push({ type: 'screenshot-refused', description: `${name}: ${error.message}` });
    console.warn(`[screenshot-refused] ${testInfo.title} / ${name}: ${error.message}`);
    await testInfo.attach(name, { body: `Screenshot not captured: ${error.message}`, contentType: 'text/plain' });
  }
}
