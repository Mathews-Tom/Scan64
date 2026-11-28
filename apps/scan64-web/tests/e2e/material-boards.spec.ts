import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { prepareToday } from './today';

interface MaterialBoard { id: string; label: string; files: Record<'webp' | 'avif', { url: string; bytes: number }> }
interface Manifest { budget_bytes_per_board: number; min_luminance_ratio: number; boards: MaterialBoard[] }

const manifest = JSON.parse(readFileSync(new URL('../../src/boardMaterials.generated.json', import.meta.url), 'utf8')) as Manifest;
/** Navigation start to the second frame after the texture has decoded, on the Analysis board. */
const FIRST_TEXTURED_PAINT_BUDGET_MS = 1500;

// SCAN64_BOARD_BASE_URL measures a production bundle (`pnpm build && pnpm preview`) instead of the dev server.
if (process.env.SCAN64_BOARD_BASE_URL) test.use({ baseURL: process.env.SCAN64_BOARD_BASE_URL });

for (const board of manifest.boards) {
  test(`${board.label} board renders its matte texture within budget`, async ({ page }, testInfo) => {
    await prepareToday(page);
    await page.addInitScript((id) => localStorage.setItem('scan64-board-theme', id), board.id);
    await page.goto('/analysis');
    const wrapper = page.getByTestId('free-analysis-board');
    const cgBoard = wrapper.locator('cg-board');
    await expect(cgBoard).toBeVisible();
    await expect.poll(() => cgBoard.evaluate((element) => getComputedStyle(element).backgroundImage)).toContain(board.files.avif.url);

    const timing = await page.evaluate(async (url) => {
      const image = new Image();
      image.src = url;
      await image.decode();
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      const paintedAt = performance.now();
      const entry = performance.getEntriesByType('resource').find((item) => item.name.endsWith(url)) as PerformanceResourceTiming | undefined;
      return { paintedAt, bytes: entry?.encodedBodySize ?? null, responseEnd: entry?.responseEnd ?? null };
    }, board.files.avif.url);
    expect(timing.bytes, 'the AVIF texture was fetched once by the board CSS').toBe(board.files.avif.bytes);
    expect(timing.bytes).toBeLessThanOrEqual(manifest.budget_bytes_per_board);
    expect(timing.paintedAt).toBeLessThanOrEqual(FIRST_TEXTURED_PAINT_BUDGET_MS);
    await testInfo.attach(`${board.id}-timing`, { body: JSON.stringify(timing), contentType: 'application/json' });

    const screenshot = await cgBoard.screenshot();
    await testInfo.attach(`material-board-${board.id}`, { body: screenshot, contentType: 'image/png' });

    // Measure the rendered pixels of the empty squares b3-g6, away from coordinates and pieces.
    const rendered = await page.evaluate(async (base64) => {
      const bitmap = await createImageBitmap(await (await fetch(`data:image/png;base64,${base64}`)).blob());
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
      const context = canvas.getContext('2d')!;
      context.drawImage(bitmap, 0, 0);
      const square = bitmap.width / 8;
      const linear = (value: number) => {
        const channel = value / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      };
      const squares: { light: boolean; mean: number; deviation: number; pixels: number[] }[] = [];
      for (let row = 2; row <= 5; row += 1) {
        for (let col = 1; col <= 6; col += 1) {
          const inset = square * 0.2;
          const size = Math.floor(square * 0.6);
          const data = context.getImageData(Math.round(col * square + inset), Math.round(row * square + inset), size, size).data;
          const pixels: number[] = [];
          for (let index = 0; index < data.length; index += 4) {
            pixels.push(0.2126 * linear(data[index]) + 0.7152 * linear(data[index + 1]) + 0.0722 * linear(data[index + 2]));
          }
          const mean = pixels.reduce((sum, value) => sum + value, 0) / pixels.length;
          const deviation = Math.sqrt(pixels.reduce((sum, value) => sum + (value - mean) ** 2, 0) / pixels.length);
          squares.push({ light: (row + col) % 2 === 0, mean, deviation, pixels });
        }
      }
      const average = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / values.length;
      const light = squares.filter((item) => item.light);
      const dark = squares.filter((item) => !item.light);
      const difference = (a: number[], b: number[]) => average(a.map((value, index) => Math.abs(value - b[index])));
      return {
        ratio: (average(light.map((item) => item.mean)) + 0.05) / (average(dark.map((item) => item.mean)) + 0.05),
        minDeviation: Math.min(...squares.map((item) => item.deviation / (item.mean + 0.05))),
        minLightDifference: Math.min(...light.slice(1).map((item) => difference(item.pixels, light[0].pixels))),
        minDarkDifference: Math.min(...dark.slice(1).map((item) => difference(item.pixels, dark[0].pixels))),
      };
    }, screenshot.toString('base64'));
    await testInfo.attach(`${board.id}-rendered`, { body: JSON.stringify(rendered), contentType: 'application/json' });
    expect(rendered.ratio).toBeGreaterThanOrEqual(manifest.min_luminance_ratio);
    // Every square shows grain or veining, and no two same-coloured squares are the same tile.
    expect(rendered.minDeviation).toBeGreaterThan(0.01);
    expect(rendered.minLightDifference).toBeGreaterThan(0.001);
    expect(rendered.minDarkDifference).toBeGreaterThan(0.0005);
  });
}

test('flat palettes keep their untextured checkerboard', async ({ page }) => {
  await prepareToday(page);
  await page.addInitScript(() => localStorage.setItem('scan64-board-theme', 'green'));
  await page.goto('/analysis');
  const cgBoard = page.getByTestId('free-analysis-board').locator('cg-board');
  await expect(cgBoard).toBeVisible();
  // One page call finds the board and reads its style: a locator evaluate resolves the
  // element first and runs the function in a second call, and a Chessground redraw
  // between them leaves a detached node, whose computed style is empty.
  const background = await page.evaluate(() => getComputedStyle(
    document.querySelector('[data-testid="free-analysis-board"] cg-board')!).backgroundImage);
  expect(background).toMatch(/^none, conic-gradient/);
  expect(background).not.toContain('/boards/');
});
