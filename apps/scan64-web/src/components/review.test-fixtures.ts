import { Chess } from 'chess.js';
import type { GameReviewRead, LessonSpec, ReviewBand } from '../api/types';

export const reviewLesson: LessonSpec = {
  schema_version: '0.1.0', lesson_id: 'opportunity-1',
  source: { kind: 'player_game', fen: new Chess().fen() },
  diagnosis: { primary: 'board_awareness.hanging_piece', secondary: [], confidence: 0.9, evidence_refs: [] },
  objective: { type: 'find_move', instruction: 'Find a sound continuation.' },
  interaction: { input: 'move', maximum_attempts: 3, accepted_moves: [{ san: 'Nf3', lan: 'g1f3', reason: 'A sound continuation.' }] },
  hints: [
    { level: 1, kind: 'nudge', text: 'Which piece needs attention?', visualizations: [] },
    { level: 2, kind: 'idea', text: 'Consider your knight.', visualizations: [] },
    { level: 3, kind: 'moves', text: 'Try Nf3.', visualizations: [] },
  ],
  explanation: { text: 'A loose piece can be captured without sufficient compensation.', visualizations: [] },
  verification: { status: 'verified', engine: 'stockfish', engine_binary_digest: 'digest', nodes: 10000, multipv: 8, verified_at: '2026-11-12' },
  mastery: { skill_key: 'board_awareness.hanging_piece', delta: 0.1 },
};

export function makeGameReview(overrides: Partial<GameReviewRead> = {}): GameReviewRead {
  const chess = new Chess();
  const initialFen = chess.fen();
  const bands: (ReviewBand | null)[] = ['sound', null, 'inaccuracy', null, 'unscored', null, 'sound'];
  const probabilities = [0.52, 0.5, 0.4, 0.45, null, 0.99, 1];
  const losses = [0.03, null, 0.1, null, null, null, 0];
  const moves = ['e2e4', 'e7e5', 'f1c4', 'b8c6', 'd1h5', 'g8f6', 'h5f7'].map((uci, index) => {
    const played = chess.move(uci);
    return {
      ply: index + 1, source_fen: played.before, fen: played.after, move_uci: uci, san: played.san,
      mover: played.color === 'w' ? 'white' as const : 'black' as const,
      before_probability: index === 4 ? null : 0.55,
      after_probability: index === 4 ? null : 0.5,
      white_probability: probabilities[index], loss: losses[index], band: bands[index], is_owner_move: played.color === 'w',
    };
  });
  return {
    game_id: 'game-1', owner_color: 'white', initial_fen: initialFen, initial_white_probability: 0.5,
    moves, accuracy: 95.6667, scored_moves: 3, total_owner_moves: 4,
    band_counts: { sound: 2, inaccuracy: 1, mistake: 0, blunder: 0, unscored: 1 },
    key_moments: [{ ply: 3, lesson_id: reviewLesson.lesson_id,
      lesson: { ...reviewLesson, source: { ...reviewLesson.source, fen: moves[2].source_fen } },
      attempts_used: 0, attempts_remaining: 3 }],
    session_id: 'study-1', backfill_required: false, nodes: 10000,
    ...overrides,
  };
}
