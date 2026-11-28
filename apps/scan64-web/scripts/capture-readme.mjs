// Capture the README scenes from the real app: a running `scan64-cli serve` with
// Stockfish and an empty database, driven by headless Chromium. Each scene writes
// numbered PNG frames and a frames.json (file and seconds on screen) that
// scripts/readme-media/make_gifs.py turns into a GIF.
//
//   node scripts/capture-readme.mjs --base http://127.0.0.1:8011 --pgn game.pgn --side black \
//     --move "16... Rxc6" --out D:/scan64-readme/frames
//
// The game is a real game given as PGN; --move is the owner's move that the review marks
// as a key moment, as the review's move list labels it. Nothing is mocked: every screen
// comes from the server.
import { chromium } from '@playwright/test';
import { Chess } from 'chess.js';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';

const { values: args } = parseArgs({
  options: {
    base: { type: 'string' },
    pgn: { type: 'string' },
    side: { type: 'string' },
    move: { type: 'string' },
    out: { type: 'string' },
  },
});
for (const name of ['base', 'pgn', 'side', 'move', 'out']) {
  if (!args[name]) throw new Error(`--${name} is required`);
}
if (args.side !== 'white' && args.side !== 'black') throw new Error('--side must be white or black');

const pgn = await readFile(args.pgn, 'utf-8');
const [, number, dots, san] = args.move.match(/^(\d+)(\.\.\.|\.)\s*(\S+)$/) ?? [];
if (!san) throw new Error(`--move ${args.move} is not like "16... Rxc6"`);
const game = new Chess();
game.loadPgn(pgn);
const ply = (Number(number) - 1) * 2 + (dots === '...' ? 2 : 1);
const history = game.history();
if (history[ply - 1] !== san) throw new Error(`ply ${ply} of the game is ${history[ply - 1]}, not ${san}`);
const before = new Chess();
for (const move of history.slice(0, ply - 1)) before.move(move);
const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 }, colorScheme: 'dark', reducedMotion: 'reduce',
});
const page = await context.newPage();
page.setDefaultTimeout(120_000);

function scene(name) {
  const frames = [];
  return {
    async frame(seconds) {
      const file = `${String(frames.length + 1).padStart(2, '0')}.png`;
      await mkdir(`${args.out}/${name}`, { recursive: true });
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${args.out}/${name}/${file}` });
      frames.push({ file, seconds });
    },
    async done() {
      await writeFile(`${args.out}/${name}/frames.json`, JSON.stringify(frames, null, 2) + '\n');
    },
  };
}

const main = () => page.locator('main');
const top = () => page.evaluate(() => window.scrollTo(0, 0));
// Scroll so that the element starts a little below the top of the window.
const toTop = locator => locator.evaluate(element => {
  element.scrollIntoView({ block: 'start' });
  window.scrollBy(0, -24);
});

async function capture() {
  // A new local player; skip the level introduction so Today is not in the way.
  await page.goto(args.base);
  await page.getByRole('textbox', { name: 'Display name' }).fill('Demo player');
  await page.getByRole('button', { name: 'Start learning' }).click();
  await page.getByRole('button', { name: 'Skip introduction' }).click();

  // Scene 1: import a game; analysis runs on the server.
  const importing = scene('import');
  await page.goto(`${args.base}/import`);
  await page.getByLabel('Your side').selectOption({ label: args.side === 'white' ? 'White' : 'Black' });
  await page.getByLabel('PGN game text').fill(pgn);
  await page.getByLabel('PGN game text').scrollIntoViewIfNeeded();
  await importing.frame(3);
  await page.getByRole('button', { name: 'Import PGN', exact: true }).click();
  await page.waitForFunction(
    () => /Explore analysis board/.test(document.querySelector('main').innerText)
      && !/Analyzing game|Importing\.\.\./.test(document.querySelector('main').innerText),
    null, { timeout: 600_000, polling: 500 },
  );
  await page.getByRole('button', { name: 'Explore analysis board' }).scrollIntoViewIfNeeded();
  await importing.frame(2);
  await page.goto(`${args.base}/games`);
  await main().getByRole('button', { name: /diagnos/ }).first().waitFor();
  await importing.frame(3);
  await importing.done();

  // Scene 2: the turning point: the move on the board and the graph, then its key
  // moment, then a retry of the moment.
  const turning = scene('turning-point');
  await main().getByRole('button', { name: /diagnos/ }).first().click();
  await page.getByRole('heading', { name: 'Key moments' }).waitFor();
  await main().getByRole('button', { name: new RegExp(`^${escape(args.move)}`) }).click();
  await top();
  await turning.frame(3);
  await toTop(page.getByText('White win probability', { exact: true }));
  await turning.frame(3);
  const moment = main().getByRole('button', { name: `Key moment at ply ${ply}`, exact: true });
  await toTop(moment);
  await turning.frame(5);
  await moment.locator("xpath=following::button[normalize-space()='Retry this moment'][1]").click();
  const retry = page.getByRole('region', { name: `Retry key moment at ply ${ply}` });
  await retry.waitFor();
  await page.waitForLoadState('networkidle');
  await toTop(retry);
  await turning.frame(4);
  await turning.done();

  // Scene 3: the position before that move on the Analysis Board, its engine lines and
  // their explanation; selecting a claim highlights the moves it cites.
  const board = scene('analysis-board');
  await page.goto(`${args.base}/analysis`);
  await page.getByPlaceholder('Paste FEN here').fill(before.fen());
  await page.getByRole('button', { name: 'Load FEN' }).click();
  await page.getByText('Position loaded. Started a new variation tree.').waitFor();
  const lines = page.getByTestId('multipv-lines');
  await lines.getByRole('button', { name: 'Explain lines' }).waitFor();
  await top();
  await board.frame(3);
  await lines.scrollIntoViewIfNeeded();
  await board.frame(3);
  await lines.getByRole('button', { name: 'Explain lines' }).click();
  const claims = page.getByRole('list', { name: 'Explanation claims' });
  await claims.waitFor({ timeout: 300_000 });
  await claims.scrollIntoViewIfNeeded();
  await board.frame(4);
  const claim = claims.getByRole('button', { name: new RegExp(`After ${number}\\.{1,3}${escape(san)}`) }).first();
  await claim.click();
  await claim.scrollIntoViewIfNeeded();
  await board.frame(4);
  await board.done();
}

try {
  await capture();
} catch (error) {
  // Keep what the page showed, then fail.
  await page.screenshot({ path: `${args.out}/failure.png`, fullPage: true });
  throw error;
} finally {
  await browser.close();
}
