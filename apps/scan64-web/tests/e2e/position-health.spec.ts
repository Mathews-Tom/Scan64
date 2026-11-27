import { test, expect, type Page } from '@playwright/test';
import { Chess } from 'chess.js';
import { prepareToday } from './today';
import type { HealthMeterRead, PositionHealthRead } from '../../src/api/types';
import { makeGameReview } from '../../src/components/review.test-fixtures';

const GROUPS = ['king safety', 'material', 'threats', 'piece placement', 'mobility', 'pawn structure', 'passed pawns', 'space'];
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
    outcome: null,
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

test('game review with a Black owner sends each selected position and its parent, and the trend follows', async ({ page }, testInfo) => {
  const id = '00000000-0000-0000-0000-000000000416';
  const review = makeGameReview({ game_id: id, owner_color: 'black' });
  await page.route(`**/v1/games/${id}/review`, route => route.fulfill({ json: review }));
  await page.route(`**/v1/games/${id}/analysis-status`, route => route.fulfill({ json: { status: 'completed' } }));
  const requests: { fen: string; previous_fen: string | null; learner: string }[] = [];
  await page.route('**/v1/analysis/position/health', async route => {
    const body = route.request().postDataJSON() as { fen: string; previous_fen: string | null; learner: 'white' | 'black' };
    requests.push(body);
    const read = healthFor(body.fen);
    // The learner's king meter falls only when a parent position was sent.
    read.learner = body.learner;
    read.groups[0].learner = meter(0.2, 'low', body.previous_fen ? 'falling' : 'steady');
    await route.fulfill({ json: read });
  });
  await page.goto(`/games/${id}/analysis`);
  const panel = page.getByTestId('position-health');
  await expect(page.getByRole('button', { name: 'First', exact: true })).toBeDisabled();
  await expect(panel.getByRole('img', { name: 'You king safety: low' })).toBeVisible();

  const timings: number[] = [];
  for (const move of review.moves.slice(0, 3)) {
    // In the page: from the Next click to the panel showing the new reading.
    await page.evaluate(() => {
      const w = window as unknown as { healthShown?: Promise<number> };
      const next = [...document.querySelectorAll('button')].find(b => b.textContent === 'Next')!;
      w.healthShown = new Promise(resolve => {
        next.addEventListener('click', () => {
          const started = performance.now();
          const panel = document.querySelector('[data-testid="position-health"]')!;
          let busy = false;
          new MutationObserver((_, observer) => {
            if (panel.getAttribute('aria-busy') === 'true') busy = true;
            else if (busy) { observer.disconnect(); resolve(performance.now() - started); }
          }).observe(panel, { attributes: true, attributeFilter: ['aria-busy'] });
        }, { once: true, capture: true });
      });
    });
    await page.getByRole('button', { name: 'Next', exact: true }).click();
    timings.push(await page.evaluate(() => (window as unknown as { healthShown: Promise<number> }).healthShown));
    await expect(panel.getByRole('img', { name: 'You king safety: low, falling' })).toBeVisible();
    expect(requests.at(-1)).toMatchObject({ fen: move.fen, previous_fen: move.source_fen, learner: 'black' });
  }
  await expect(panel.getByRole('columnheader', { name: 'Opponent' })).toBeVisible();
  // Selection to display with an instant API, for the PR's latency report.
  await testInfo.attach('selection-to-display-ms', { body: timings.map(t => t.toFixed(1)).join(','), contentType: 'text/plain' });
});

test('the panel keeps its height while the next position loads', async ({ page }) => {
  const id = '00000000-0000-0000-0000-000000000417';
  const review = makeGameReview({ game_id: id, owner_color: 'black' });
  await page.route(`**/v1/games/${id}/review`, route => route.fulfill({ json: review }));
  await page.route(`**/v1/games/${id}/analysis-status`, route => route.fulfill({ json: { status: 'completed' } }));
  // The second reply is held until the test has measured the busy panel.
  let hold: Promise<void> | null = null;
  let release = () => {};
  await page.route('**/v1/analysis/position/health', async route => {
    const { fen } = route.request().postDataJSON() as { fen: string };
    if (hold) await hold;
    await route.fulfill({ json: healthFor(fen) });
  });
  await page.goto(`/games/${id}/analysis`);
  const panel = page.getByTestId('position-health');
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  const before = (await panel.boundingBox())!.height;
  hold = new Promise(resolve => { release = resolve; });
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(panel).toHaveAttribute('aria-busy', 'true');
  expect((await panel.boundingBox())!.height).toBe(before);
  // The last reading keeps full contrast; a held reply shows the Updating label.
  await expect(panel.locator('.position-health__table')).toHaveCSS('opacity', '1');
  await expect(panel.getByRole('status')).toHaveText('Updating');
  expect((await panel.boundingBox())!.height).toBe(before);
  release();
  await expect(panel.getByRole('img', { name: 'You king safety: low, falling' })).toBeVisible();
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  await expect(panel.getByRole('status')).toHaveText('');
});

test('a fast reply never shows the Updating label', async ({ page }) => {
  const id = '00000000-0000-0000-0000-000000000418';
  const review = makeGameReview({ game_id: id, owner_color: 'black' });
  await page.route(`**/v1/games/${id}/review`, route => route.fulfill({ json: review }));
  await page.route(`**/v1/games/${id}/analysis-status`, route => route.fulfill({ json: { status: 'completed' } }));
  // Sample the label on every frame from the first paint: the mount and three steps.
  await page.addInitScript(() => {
    const w = window as unknown as { shown: boolean };
    w.shown = false;
    const sample = () => {
      if (document.querySelector('.position-health__updating')?.textContent) w.shown = true;
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  await page.goto(`/games/${id}/analysis`);
  const panel = page.getByTestId('position-health');
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  for (let step = 0; step < 3; step++) {
    await page.getByRole('button', { name: 'Next', exact: true }).click();
    await expect(panel).toHaveAttribute('aria-busy', 'false');
  }
  expect(await page.evaluate(() => (window as unknown as { shown: boolean }).shown)).toBe(false);
});
