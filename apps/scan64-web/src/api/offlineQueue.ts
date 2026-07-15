import { ApiClient } from './client';

import { get, set } from 'idb-keyval';

export interface QueuedMove {
  sessionId: string;
  move: string;
  timestamp: number;
}

export async function queueMove(sessionId: string, move: string) {
  const queue = await get<QueuedMove[]>('scan64_offline_moves') || [];
  queue.push({ sessionId, move, timestamp: Date.now() });
  await set('scan64_offline_moves', queue);
}

export async function getQueuedMoves(): Promise<QueuedMove[]> {
  return (await get<QueuedMove[]>('scan64_offline_moves')) || [];
}

export async function clearQueuedMoves() {
  await set('scan64_offline_moves', []);
}

export async function removeQueuedMove(sessionId: string, move: string, timestamp: number) {
  const queue = await get<QueuedMove[]>('scan64_offline_moves') || [];
  const filtered = queue.filter(m => !(m.sessionId === sessionId && m.move === move && m.timestamp === timestamp));
  await set('scan64_offline_moves', filtered);
}

export async function syncMoves() {
  const moves = await getQueuedMoves();
  if (moves.length === 0) return;
  
  for (const queued of moves) {
    try {
      await ApiClient.makePlaySessionMove(queued.sessionId, { move: queued.move });
      await removeQueuedMove(queued.sessionId, queued.move, queued.timestamp);
    } catch (e) {
      if (!navigator.onLine) {
        break;
      }
      console.error('Failed to sync move', queued, e);
      // We should probably remove it if it's a 4xx error but ApiClient just throws Error
      // We will leave it in the queue for now or we could add more granular error handling
    }
  }
  
  window.dispatchEvent(new CustomEvent('scan64-moves-synced'));
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', syncMoves);
}
