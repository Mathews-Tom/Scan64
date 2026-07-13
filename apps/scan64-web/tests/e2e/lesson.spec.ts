import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';
import { LessonSpec } from '../../src/api/types';

test.describe('Lesson Review Flow', () => {
  test('renders hint ladder matching LessonSpec fixture', async ({ page }) => {
    // Read the conformance fixture
    const __dirname = path.dirname(fileURLToPath(import.meta.url));
    const fixturePath = path.resolve(__dirname, '../../../../tests/conformance/lesson_spec/valid_full.json');
    const lessonSpec = JSON.parse(fs.readFileSync(fixturePath, 'utf-8')) as LessonSpec;

    // Intercept API calls
    await page.route('/api/v1/games', async route => {
      await route.fulfill({ json: { id: 'test-game-id', pgn: '1. e4', white: 'w', black: 'b', result: '*' } });
    });

    await page.route('/api/v1/games/test-game-id/learning-opportunities', async route => {
      await route.fulfill({ json: [lessonSpec] });
    });

    // Go to home and navigate to import screen
    await page.goto('/');
    await page.getByText('Import PGN').click();

    // Import a game
    await page.getByTestId('pgn-textarea').fill('1. e4');
    await page.getByTestId('import-btn').click();

    // Verify learning opportunity appeared
    await expect(page.getByTestId('lessons-list')).toContainText(lessonSpec.diagnosis.primary);

    // Click Review button
    await page.getByTestId(`review-btn-${lessonSpec.lesson_id}`).click();

    // Now in LessonReviewScreen
    await expect(page.getByTestId('lesson-review')).toBeVisible();

    // Check objective
    await expect(page.getByText(lessonSpec.objective.instruction)).toBeVisible();

    // Initial state: no hints
    await expect(page.getByTestId('hint-0')).not.toBeVisible();

    // Show hints progressively
    for (let i = 0; i < lessonSpec.hints.length; i++) {
      const hintBtn = page.getByTestId('next-hint-btn');
      await hintBtn.click();

      const hintEl = page.getByTestId(`hint-${i}`);
      await expect(hintEl).toContainText(lessonSpec.hints[i].text);

      if (lessonSpec.hints[i].visualizations) {
        for (const vis of lessonSpec.hints[i].visualizations!) {
          await expect(hintEl).toContainText(vis.command);
          await expect(hintEl).toContainText(vis.description);
        }
      }
    }

    // Verify explanation appears
    await expect(page.getByTestId('explanation')).toContainText(lessonSpec.explanation.text);
  });
});
