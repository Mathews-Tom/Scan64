import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { QueuedMove } from '../../src/api/offlineQueue';
import type * as OfflineQueue from '../../src/api/offlineQueue';

type QueueModule = typeof OfflineQueue;
interface QueueTestWindow extends Window {
  queueModule: QueueModule;
  queueOperation: Promise<void>;
  releaseQueue: () => Promise<void>;
}

async function prepareQueuePage(page: Page): Promise<void> {
  await page.route('**/queue-atomicity-test', route => route.fulfill({
    contentType: 'text/html', body: '<!doctype html><title>Queue atomicity test</title>',
  }));
  await page.goto('/queue-atomicity-test');
  await page.evaluate(async () => {
    const modulePath = '/src/api/offlineQueue.ts';
    const state = window as unknown as QueueTestWindow;
    state.queueModule = await import(modulePath) as QueueModule;
    // Open a separate real IndexedDB connection in each page before the barrier.
    await state.queueModule.getQueuedMoves();
  });
}

async function holdQueue(page: Page, initial: QueuedMove[]): Promise<void> {
  await page.evaluate(async initialQueue => {
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('keyval-store');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const transaction = database.transaction('keyval', 'readwrite');
    const store = transaction.objectStore('keyval');
    store.put(initialQueue, 'scan64_offline_moves');
    let held = true;
    const keepAlive = () => {
      const request = store.get('scan64_offline_moves');
      request.onsuccess = () => { if (held) keepAlive(); };
    };
    keepAlive();
    (window as unknown as QueueTestWindow).releaseQueue = () => new Promise<void>((resolve, reject) => {
      held = false;
      transaction.oncomplete = () => { database.close(); resolve(); };
      transaction.onerror = transaction.onabort = () => {
        database.close();
        reject(transaction.error);
      };
    });
  }, initial);
}

async function releaseQueue(page: Page): Promise<void> {
  await page.evaluate(() => (window as unknown as QueueTestWindow).releaseQueue());
}

async function finishQueueOperation(page: Page): Promise<QueuedMove[]> {
  return page.evaluate(async () => {
    const state = window as unknown as QueueTestWindow;
    if (!state.queueOperation) throw new Error('Queue operation was not started');
    await state.queueOperation;
    return state.queueModule.getQueuedMoves();
  });
}

// The two tabs reach IndexedDB over separate channels, so a request made first can still
// arrive second (#434). Each cross-tab test asserts what holds in either order: every
// intent survives and no transaction applies by half.

test('preserves concurrent appends from independent tabs', async ({ page, context }) => {
  const other = await context.newPage();
  await Promise.all([prepareQueuePage(page), prepareQueuePage(other)]);
  await holdQueue(page, []);
  try {
    await page.evaluate(() => {
      const state = window as unknown as QueueTestWindow;
      state.queueOperation = state.queueModule.queueMove('first', 'e2e4', '');
    });
    await other.evaluate(() => {
      const state = window as unknown as QueueTestWindow;
      state.queueOperation = state.queueModule.queueMove('second', 'd2d4', '');
    });
  } finally {
    await releaseQueue(page);
  }
  await finishQueueOperation(page);
  const queue = await finishQueueOperation(other);
  expect(queue).toHaveLength(2);
  expect(queue).toEqual(expect.arrayContaining([
    { sessionId: 'first', move: 'e2e4', sourcePgn: '', timestamp: expect.any(Number) },
    { sessionId: 'second', move: 'd2d4', sourcePgn: '', timestamp: expect.any(Number) },
  ]));
});

for (const first of ['append', 'discard'] as const) {
  test(`preserves unselected intent during cross-tab legacy discard (${first} first)`, async ({ page, context }) => {
    const other = await context.newPage();
    await Promise.all([prepareQueuePage(page), prepareQueuePage(other)]);
    const retained = { sessionId: 'retained', move: 'g1f3', timestamp: 2, sourcePgn: '' };
    await holdQueue(page, [
      { sessionId: 'legacy', move: 'e2e4', timestamp: 1 }, retained,
    ]);
    const append = () => other.evaluate(() => {
      const state = window as unknown as QueueTestWindow;
      state.queueOperation = state.queueModule.queueMove('new', 'd2d4', '');
    });
    const discard = () => page.evaluate(() => {
      const state = window as unknown as QueueTestWindow;
      state.queueOperation = state.queueModule.removeQueuedMove('legacy', 'e2e4', 1);
    });
    try {
      if (first === 'append') { await append(); await discard(); }
      else { await discard(); await append(); }
    } finally {
      await releaseQueue(page);
    }
    await finishQueueOperation(page);
    expect(await finishQueueOperation(other)).toEqual([
      retained,
      { sessionId: 'new', move: 'd2d4', sourcePgn: '', timestamp: expect.any(Number) },
    ]);
  });
}

test('does not resurrect another discarded intent across tabs', async ({ page, context }) => {
  const other = await context.newPage();
  await Promise.all([prepareQueuePage(page), prepareQueuePage(other)]);
  await holdQueue(page, [
    { sessionId: 'legacy', move: 'e2e4', timestamp: 1 },
    { sessionId: 'legacy', move: 'd2d4', timestamp: 2 },
  ]);
  try {
    await page.evaluate(() => {
      const state = window as unknown as QueueTestWindow;
      state.queueOperation = state.queueModule.removeQueuedMove('legacy', 'e2e4', 1);
    });
    await other.evaluate(() => {
      const state = window as unknown as QueueTestWindow;
      state.queueOperation = state.queueModule.removeQueuedMove('legacy', 'd2d4', 2);
    });
  } finally {
    await releaseQueue(page);
  }
  await finishQueueOperation(page);
  expect(await finishQueueOperation(other)).toEqual([]);
});

test('a clear and an append from another tab each apply whole, in either order', async ({ page, context }) => {
  const other = await context.newPage();
  await Promise.all([prepareQueuePage(page), prepareQueuePage(other)]);
  await holdQueue(page, [{ sessionId: 'legacy', move: 'e2e4', timestamp: 1 }]);
  try {
    await page.evaluate(() => {
      const state = window as unknown as QueueTestWindow;
      state.queueOperation = state.queueModule.queueMove('earlier', 'd2d4', '');
    });
    await other.evaluate(() => {
      const state = window as unknown as QueueTestWindow;
      state.queueOperation = state.queueModule.clearQueuedMoves();
    });
  } finally {
    await releaseQueue(page);
  }
  await finishQueueOperation(page);
  // Append then clear leaves nothing; clear then append leaves only the append.
  // The legacy entry is gone either way, and nothing is applied by half.
  const queue = (await finishQueueOperation(other)).map(({ sessionId, move }) => `${sessionId} ${move}`);
  expect([[], ['earlier d2d4']]).toContainEqual(queue);
});

test('clear removes earlier appends but preserves a later append', async ({ page }) => {
  await prepareQueuePage(page);
  await holdQueue(page, [{ sessionId: 'legacy', move: 'e2e4', timestamp: 1 }]);
  try {
    // One tab, one connection: transactions start in the order they are made.
    await page.evaluate(() => {
      const state = window as unknown as QueueTestWindow;
      state.queueOperation = state.queueModule.queueMove('earlier', 'd2d4', '');
      (window as unknown as { clearOperation: Promise<void> }).clearOperation = state.queueModule.clearQueuedMoves();
    });
  } finally {
    await releaseQueue(page);
  }
  await page.evaluate(() => (window as unknown as { clearOperation: Promise<void> }).clearOperation);
  expect(await finishQueueOperation(page)).toEqual([]);
  const later = await page.evaluate(async () => {
    const queue = (window as unknown as QueueTestWindow).queueModule;
    await queue.queueMove('later', 'g1f3', '');
    return queue.getQueuedMoves();
  });
  expect(later).toEqual([
    { sessionId: 'later', move: 'g1f3', sourcePgn: '', timestamp: expect.any(Number) },
  ]);
});
