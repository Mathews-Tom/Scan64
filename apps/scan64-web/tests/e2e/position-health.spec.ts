import { test, expect, type Page } from '@playwright/test';
import { Chess } from 'chess.js';
import { prepareToday } from './today';
import type { HealthMeterRead, PositionHealthRead } from '../../src/api/types';

const GROUPS = ['king safety', 'material', 'threats', 'pieces', 'mobility', 'pawn structure', 'passed pawns', 'space'];
const meter = (health: number, level: HealthMeterRead['level'], trend: HealthMeterRead['trend']): HealthMeterRead =>
  ({ health, level, trend, terms: [{ name: 'King danger', pawns: -1.1 }] });

function healthFor(fen: string): PositionHealthRead {
  return {
    fen, learner: 'white',
    overall: { key: 'Overall', name: 'overall', learner: meter(0.55, 'fair', 'falling'), opponent: meter(0.93, 'good', 'rising') },
    groups: GROUPS.map((name, index) => ({
      key: name, name,
      learner: index === 0 ? meter(0.2, 'low', 'falling') : meter(0.88, 'good', 'steady'),
      opponent: index === 2 ? meter(0.5, 'fair', 'steady') : meter(0.88, 'good', 'steady'),
    })),
  };
}

async function openAnalysis(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Review', exact: true }).click();
  await page.getByRole('button', { name: 'Analysis Board' }).click();
  await expect(page.getByRole('heading', { name: 'Analysis Board' })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await prepareToday(page);
  await page.route('**/v1/analysis/position', async route => {
    const { fen } = route.request().postDataJSON() as { fen: string };
    await route.fulfill({ json: { fen: new Chess(fen).fen(), lines: [] } });
  });
  await page.route('**/v1/analysis/position/health', async route => {
    const { fen } = route.request().postDataJSON() as { fen: string };
    await route.fulfill({ json: healthFor(fen) });
  });
});

test('health panel shows labelled meters and the Settings switch hides it', async ({ page }) => {
  await openAnalysis(page);
  const panel = page.getByTestId('position-health');
  await expect(panel.getByRole('img', { name: 'White king safety: low, falling' })).toBeVisible();
  await expect(panel.getByRole('img', { name: 'White overall: fair, falling' })).toBeVisible();
  await expect(panel.getByRole('img', { name: 'Black threats: fair' })).toBeVisible();
  await panel.getByRole('button', { name: 'Show details' }).click();
  await expect(panel.getByText('White: King danger -1.10').first()).toBeVisible();

  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Settings', exact: true }).click();
  const setting = page.getByTestId('health-panel-setting');
  await expect(setting).toBeChecked();
  await setting.uncheck();
  await openAnalysis(page);
  await expect(page.getByTestId('position-health')).toHaveCount(0);
  await page.reload();
  await openAnalysis(page);
  await expect(page.getByTestId('position-health')).toHaveCount(0);
});

for (const [appearance, width] of [['light', 1280], ['dark', 1280], ['light', 390], ['dark', 390]] as const) {
  test(`health panel screenshot, ${appearance}, ${width}px`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem('scan64-appearance', value), appearance);
    await page.setViewportSize({ width, height: 1000 });
    await openAnalysis(page);
    const panel = page.getByTestId('position-health');
    await expect(panel.getByRole('img', { name: 'White overall: fair, falling' })).toBeVisible();
    const box = await panel.boundingBox();
    expect(box && box.x + box.width <= width).toBe(true);
    await panel.screenshot({ path: `test-results/position-health-${appearance}-${width}.png` });
  });
}
