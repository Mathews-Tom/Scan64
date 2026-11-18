import type { ProfileInsightsRead } from '../api/types';

export const insightsFixture: ProfileInsightsRead = {
  overall: { label: 'Overall', accuracy: 86, scored_moves: 5, eligible_moves: 8 },
  phases: [
    { label: 'Opening', accuracy: 86, scored_moves: 5, eligible_moves: 8 },
    { label: 'Middlegame', accuracy: null, scored_moves: 0, eligible_moves: 0 },
    { label: 'Endgame', accuracy: null, scored_moves: 0, eligible_moves: 0 },
  ],
  colours: [
    { label: 'White', accuracy: 95, scored_moves: 2, eligible_moves: 4 },
    { label: 'Black', accuracy: 80, scored_moves: 3, eligible_moves: 4 },
  ],
  openings: [{ label: 'Caro-Kann Defense', accuracy: null, scored_moves: 0, eligible_moves: 1 }],
  time_pressure: { share: 50, pressure_moves: 2, valid_clock_moves: 4, eligible_moves: 7 },
};
