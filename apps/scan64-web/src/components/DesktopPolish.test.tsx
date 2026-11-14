import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { latestGameLabel } from './gameLabel';
import { PuzzleImportInstructions } from './PuzzlesScreen';

const player = '12345678-1234-1234-1234-123456789abc';
const game = { id: 'game-id', white: player, black: 'Stockfish (level 8: Expert)', result: '*', date: '2026.11.13', created_at: '2026-11-14T12:00:00Z', diagnosis_count: 1 };

describe('desktop polish', () => {
  it('names the human side, opponent and PGN date without UUIDs', () => {
    expect(latestGameLabel(game, player)).toBe('You (White) vs Stockfish (level 8: Expert) · 13 Nov');
    expect(latestGameLabel({ ...game, white: 'Ada', black: 'Grace' }, player)).toBe('Ada vs Grace · 13 Nov');
    expect(latestGameLabel({ ...game, white: 'Grace', black: player, date: '????.??.??' }, player)).toBe('Grace vs You (Black) · 14 Nov');
    expect(latestGameLabel(game, 'other')).not.toContain(player);
  });
  it('directs desktop users to the window without a terminal command', () => {
    render(<PuzzleImportInstructions distribution="desktop" />);
    expect(screen.getByText('Use Import puzzles… in the Scan64 window.')).toBeTruthy();
    expect(screen.queryByText(/uv run/)).toBeNull();
  });
  it('preserves source installation import instructions', () => {
    render(<PuzzleImportInstructions distribution="source" />);
    expect(screen.getByText(/uv run scan64-cli puzzles import/)).toBeTruthy();
  });
});
