import { Chess } from 'chess.js';

/** Calculate a read-only UCI line preview without changing the live game. */
export function linePosition(fen: string, moves: string[], count: number): string {
  const chess = new Chess(fen);
  for (const move of moves.slice(0, count)) {
    chess.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move.slice(4) || undefined });
  }
  return chess.fen();
}
