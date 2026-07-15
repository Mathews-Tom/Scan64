import { ApiClient } from './client';
import type { PlayMoveResponse } from './types';

import { get, set } from 'idb-keyval';

const QUEUED_MOVES_KEY = 'scan64_offline_moves';

export const QUEUED_MOVE_SYNC_SUCCEEDED = 'scan64-queued-move-sync-succeeded';
export const QUEUED_MOVE_SYNC_FAILED = 'scan64-queued-move-sync-failed';

export interface QueuedMove {
  sessionId: string;
  move: string;
  timestamp: number;
}

export interface QueuedMoveSyncSuccess {
  queuedMove: QueuedMove;
  response: PlayMoveResponse;
}

export interface QueuedMoveSyncFailure {
  queuedMove: QueuedMove | null;
  message: string;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown synchronization error';
}

function dispatchSyncEvent<T>(name: string, detail: T): void {
  window.dispatchEvent(new CustomEvent<T>(name, { detail }));
}

export async function queueMove(sessionId: string, move: string): Promise<void> {
  const queue = (await get<QueuedMove[]>(QUEUED_MOVES_KEY)) ?? [];
  queue.push({ sessionId, move, timestamp: Date.now() });
  await set(QUEUED_MOVES_KEY, queue);
}

export async function getQueuedMoves(): Promise<QueuedMove[]> {
  return (await get<QueuedMove[]>(QUEUED_MOVES_KEY)) ?? [];
}

export async function clearQueuedMoves(): Promise<void> {
  await set(QUEUED_MOVES_KEY, []);
}

export async function removeQueuedMove(
  sessionId: string,
  move: string,
  timestamp: number,
): Promise<void> {
  const queue = (await get<QueuedMove[]>(QUEUED_MOVES_KEY)) ?? [];
  const filtered = queue.filter(
    (queuedMove) =>
      !(
        queuedMove.sessionId === sessionId &&
        queuedMove.move === move &&
        queuedMove.timestamp === timestamp
      ),
  );
  await set(QUEUED_MOVES_KEY, filtered);
}

export async function syncQueuedMoves(): Promise<void> {
  let queuedMoves: QueuedMove[];

  try {
    queuedMoves = await getQueuedMoves();
  } catch (error: unknown) {
    dispatchSyncEvent<QueuedMoveSyncFailure>(QUEUED_MOVE_SYNC_FAILED, {
      queuedMove: null,
      message: errorMessage(error),
    });
    return;
  }

  for (const queuedMove of queuedMoves) {
    try {
      const response = await ApiClient.makePlaySessionMove(queuedMove.sessionId, {
        move: queuedMove.move,
      });
      await removeQueuedMove(queuedMove.sessionId, queuedMove.move, queuedMove.timestamp);
      dispatchSyncEvent<QueuedMoveSyncSuccess>(QUEUED_MOVE_SYNC_SUCCEEDED, {
        queuedMove,
        response,
      });
    } catch (error: unknown) {
      dispatchSyncEvent<QueuedMoveSyncFailure>(QUEUED_MOVE_SYNC_FAILED, {
        queuedMove,
        message: errorMessage(error),
      });
      return;
    }
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', () => {
    void syncQueuedMoves();
  });
}
