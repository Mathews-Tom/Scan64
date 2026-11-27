import { ApiClient, BotPlayUnavailableError } from './client';
import type { PlayMoveResponse } from './types';

import { get, set, update } from 'idb-keyval';
import { Chess } from 'chess.js';

const QUEUED_MOVES_KEY = 'scan64_offline_moves';

export const QUEUED_MOVE_SYNC_SUCCEEDED = 'scan64-queued-move-sync-succeeded';
export const QUEUED_MOVE_SYNC_FAILED = 'scan64-queued-move-sync-failed';

export interface QueuedMove {
  sessionId: string;
  move: string;
  timestamp: number;
  sourcePgn?: string;
}

export interface QueuedMoveSyncSuccess {
  queuedMove: QueuedMove;
  response: PlayMoveResponse | null;
  /** Set when the move committed but the server's engine profile cannot play the bot. */
  botPlayUnavailable?: string;
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

export async function queueMove(sessionId: string, move: string, sourcePgn: string): Promise<void> {
  await update<QueuedMove[]>(QUEUED_MOVES_KEY, storedQueue => {
    const queue = storedQueue ?? [];
    queue.push({ sessionId, move, timestamp: Date.now(), sourcePgn });
    return queue;
  });
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
  await update<QueuedMove[]>(QUEUED_MOVES_KEY, queue =>
    (queue ?? []).filter(
      queuedMove =>
        !(
          queuedMove.sessionId === sessionId &&
          queuedMove.move === move &&
          queuedMove.timestamp === timestamp
        ),
    ),
  );
}

let activeMoveSync: Promise<void> | null = null;

async function inspectQueuedMove(queuedMove: QueuedMove): Promise<
  { committed: true } | { committed: false; generation: number }
> {
  if (typeof queuedMove.sourcePgn !== 'string') {
    throw new Error('Queued move has no recorded source position; refusing to replay it.');
  }
  const source = new Chess();
  source.loadPgn(queuedMove.sourcePgn);
  const sourceMoves = source.history({ verbose: true });
  const play = await ApiClient.getPlaySession(queuedMove.sessionId);
  const canonical = new Chess();
  if (play.game_id) canonical.loadPgn((await ApiClient.getGame(play.game_id)).pgn);
  const canonicalMoves = canonical.history({ verbose: true });
  if (
    (sourceMoves[0]?.before ?? source.fen()) !== (canonicalMoves[0]?.before ?? canonical.fen()) ||
    sourceMoves.some((move, index) => move.lan !== canonicalMoves[index]?.lan)
  ) {
    throw new Error('Queued move source does not match the saved game; move retained.');
  }
  if (canonicalMoves.length > sourceMoves.length) {
    if (canonicalMoves[sourceMoves.length].lan === queuedMove.move) return { committed: true };
    throw new Error('Saved game contains a different move; queued move retained.');
  }
  if (play.status !== 'active') throw new Error('Saved game is no longer active; queued move retained.');
  const generation = Number(play.opponent_config.assistance_generation ?? '0');
  if (!Number.isSafeInteger(generation) || generation < 0) {
    throw new Error('Saved game has an invalid move generation; queued move retained.');
  }
  return { committed: false, generation };
}

async function drainQueuedMoves(): Promise<void> {
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

  const blockedSessions = new Set<string>();
  for (const queuedMove of queuedMoves) {
    if (blockedSessions.has(queuedMove.sessionId)) continue;
    try {
      const saved = await inspectQueuedMove(queuedMove);
      let response: PlayMoveResponse | null = null;
      let botPlayUnavailable: string | undefined;
      if (!saved.committed) {
        try {
          response = await ApiClient.makePlaySessionMove(queuedMove.sessionId, {
            move: queuedMove.move,
            expected_generation: saved.generation,
          });
        } catch (error: unknown) {
          // Bot failure or response loss can follow an accepted human move.
          // Reconcile the replay prefix, never retry the human submission.
          if (!(await inspectQueuedMove(queuedMove)).committed) throw error;
          if (error instanceof BotPlayUnavailableError) botPlayUnavailable = error.message;
        }
      }
      await removeQueuedMove(queuedMove.sessionId, queuedMove.move, queuedMove.timestamp);
      dispatchSyncEvent<QueuedMoveSyncSuccess>(QUEUED_MOVE_SYNC_SUCCEEDED, {
        queuedMove,
        response,
        ...(botPlayUnavailable !== undefined ? { botPlayUnavailable } : {}),
      });
    } catch (error: unknown) {
      blockedSessions.add(queuedMove.sessionId);
      dispatchSyncEvent<QueuedMoveSyncFailure>(QUEUED_MOVE_SYNC_FAILED, {
        queuedMove,
        message: errorMessage(error),
      });
    }
  }
}

export function syncQueuedMoves(): Promise<void> {
  if (activeMoveSync) return activeMoveSync;

  activeMoveSync = drainQueuedMoves().finally(() => {
    activeMoveSync = null;
  });
  return activeMoveSync;
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', () => {
    void syncQueuedMoves();
  });
}
