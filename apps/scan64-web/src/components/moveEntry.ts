import type { Chess, Move } from 'chess.js';
import type { Key } from 'chessground/types';

export type MoveResolution = { kind: 'move'; move: Move }
  | { kind: 'promotion'; from: Key; to: Key }
  | { kind: 'error'; message: string };

/** Resolve notation only against this position's legal moves, without mutating it. */
export function resolveMove(chess: Chess, text: string): MoveResolution {
  const input = text.trim();
  const legal = chess.moves({ verbose: true });
  const uci = /^([a-h][1-8])([a-h][1-8])([qrbn])?$/.exec(input.toLowerCase());
  if (uci) {
    const matches = legal.filter(move => move.from === uci[1] && move.to === uci[2]);
    if (!uci[3] && matches.some(move => move.promotion)) {
      return { kind: 'promotion', from: uci[1] as Key, to: uci[2] as Key };
    }
    const move = matches.find(move => move.promotion === uci[3]);
    if (move) return { kind: 'move', move };
  } else {
    const matches = legal.filter(move => move.san === input);
    if (matches.length === 1) return { kind: 'move', move: matches[0] };
  }
  return { kind: 'error', message: 'Enter a legal move in UCI (e2e4, e7e8q) or exact English SAN (Nf3, O-O). Include SAN disambiguation and check signs.' };
}
