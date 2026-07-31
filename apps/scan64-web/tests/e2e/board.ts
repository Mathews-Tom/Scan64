import { expect, type Locator, type Page } from '@playwright/test';

async function moveCoordinates(
  board: Locator, from: string, to: string
): Promise<{ source: { x: number; y: number }; destination: { x: number; y: number } }> {
  await expect(board).toHaveClass(/\borientation-(white|black)\b/);

  const orientation = await board.evaluate((element) => {
    if (element.classList.contains('orientation-white')) return 'white';
    if (element.classList.contains('orientation-black')) return 'black';
    return null;
  });
  await board.scrollIntoViewIfNeeded();
  if (orientation === null) throw new Error('Chessground board has no orientation');

  const bounds = await board.boundingBox();
  if (bounds === null) throw new Error('Chessground board has no bounds');

  const squareCenter = (square: string) => {
    const file = square.charCodeAt(0) - 'a'.charCodeAt(0);
    const rank = Number(square[1]);
    const fileFromLeft = orientation === 'white' ? file : 7 - file;
    const rankFromTop = orientation === 'white' ? 8 - rank : rank - 1;
    return {
      x: bounds.x + (fileFromLeft + 0.5) * (bounds.width / 8),
      y: bounds.y + (rankFromTop + 0.5) * (bounds.height / 8),
    };
  };

  return { source: squareCenter(from), destination: squareCenter(to) };
}

export async function movePiece(page: Page, board: Locator, from: string, to: string): Promise<void> {
  const { source, destination } = await moveCoordinates(board, from, to);
  await page.mouse.move(source.x, source.y);
  await page.mouse.down();
  await expect(board.locator('square.selected')).toBeVisible();
  await page.mouse.move(destination.x, destination.y);
  await page.mouse.up();
}

export async function attemptMovePiece(
  page: Page, board: Locator, from: string, to: string
): Promise<void> {
  const { source, destination } = await moveCoordinates(board, from, to);
  await page.mouse.move(source.x, source.y);
  await page.mouse.down();
  await page.mouse.move(destination.x, destination.y);
  await page.mouse.up();
}
