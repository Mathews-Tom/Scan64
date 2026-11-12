import type { PlayerOnboardingRead, StrengthRead } from '../api/types';

export const onboardingFixture: PlayerOnboardingRead = {
  player_id: 'player-1', status: 'pending', declared_level: null, suggested_bot_level: null,
  mapping_version: null, intro_started_at: null, skipped_at: null, completed_at: null,
  intro_started: 0, intro_closed: 0, intro_target: 5, preferred_bot_level: 1, manual_bot_level: null,
};

export const strengthFixture: StrengthRead = {
  declared_level: 'developing', suggested_bot_level: 3, puzzle_rating: 1216,
  puzzle_rated_count: 2, puzzle_provisional: true,
  puzzle_events: [{ sequence: 1, before: 1200, after: 1216, score: 1 }],
  bot_cohorts: [{
    provider: 'stockfish', bot_level: 3, bot_policy_version: 'v1',
    sample_size: 10, wins: 5, draws: 2, losses: 3,
    latest: { sample_size: 5, wins: 3, draws: 1, losses: 1 },
    previous: { sample_size: 5, wins: 2, draws: 1, losses: 2 }, trend: 'improving',
  }],
  disclosure: 'Only eligible tracked, completed, standard-start, non-coach Stockfish games without recorded hints count.',
};
