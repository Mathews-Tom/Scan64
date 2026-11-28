import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId } from '../api/client';
import { GamesListScreen } from './GamesListScreen';

vi.mock('../api/client', () => ({
  ApiClient: { getPlayerGames: vi.fn() },
  ensurePlayerAuthorization: vi.fn(),
  getOrCreatePlayerId: vi.fn(),
}));

describe('GamesListScreen', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getOrCreatePlayerId).mockReturnValue('player-1');
    vi.mocked(ensurePlayerAuthorization).mockResolvedValue('player-1');
  });

  it('lists every returned game and opens the selected game', async () => {
    vi.mocked(ApiClient.getPlayerGames).mockResolvedValue({
      items: [{ variant: 'chess', id: 'game-1', white: 'Alice', black: 'Bob', result: '1-0', date: '2026.07.28', created_at: '2026-07-28T12:00:00Z', diagnosis_count: 2 }],
      next_cursor: null,
    });
    const onOpenGame = vi.fn();

    render(<GamesListScreen onOpenGame={onOpenGame} onImportGames={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole('button', { name: /Alice vs Bob/ })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Alice vs Bob/ })).toHaveTextContent('2026');
    expect(ApiClient.getPlayerGames).toHaveBeenCalledWith('player-1');
    fireEvent.click(screen.getByRole('button', { name: /Alice vs Bob/ }));
    expect(onOpenGame).toHaveBeenCalledWith('game-1');
  });

  it('labels a variant game as not analysed instead of counting diagnoses', async () => {
    vi.mocked(ApiClient.getPlayerGames).mockResolvedValue({
      items: [
        { variant: 'crazyhouse', id: 'game-1', white: 'Alice', black: 'Bob', result: '1-0', date: '2026.07.28', created_at: '2026-07-28T12:00:00Z', diagnosis_count: 0 },
        { variant: 'Bughouse', id: 'game-2', white: 'Carol', black: 'Dan', result: '0-1', date: '2026.07.29', created_at: '2026-07-29T12:00:00Z', diagnosis_count: 0 },
      ],
      next_cursor: null,
    });
    render(<GamesListScreen onOpenGame={vi.fn()} onImportGames={vi.fn()} />);
    expect(await screen.findByRole('button', { name: /Alice vs Bob/ })).toHaveTextContent('Crazyhouse, not analysed');
    expect(screen.getByRole('button', { name: /Alice vs Bob/ })).not.toHaveTextContent('diagnoses');
    expect(screen.getByRole('button', { name: /Carol vs Dan/ })).toHaveTextContent('Bughouse, not analysed');
  });

  it('offers username import when there are no games yet', async () => {
    vi.mocked(ApiClient.getPlayerGames).mockResolvedValue({ items: [], next_cursor: null });
    const onImportGames = vi.fn();

    render(<GamesListScreen onOpenGame={vi.fn()} onImportGames={onImportGames} />);

    expect(await screen.findByText(/Lichess or Chess\.com games by username/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Import games by username' }));
    expect(onImportGames).toHaveBeenCalledOnce();
  });

  it.each([
    ['2013.01.??', '2013'],
    ['????.??.??', null],
  ] as const)('does not replace partial PGN date %s with the import date', async (date, knownYear) => {
    vi.mocked(ApiClient.getPlayerGames).mockResolvedValue({
      items: [{ variant: 'chess', id: 'partial-date', white: 'Alice', black: 'Bob', result: '1-0', date, created_at: '2026-11-14T12:00:00Z', diagnosis_count: 0 }],
      next_cursor: null,
    });
    render(<GamesListScreen onOpenGame={vi.fn()} onImportGames={vi.fn()} />);
    const game = await screen.findByRole('button', { name: /Alice vs Bob/ });
    expect(game).not.toHaveTextContent('2026');
    if (knownYear) expect(game).toHaveTextContent(knownYear);
    else expect(game).not.toHaveTextContent(/\d{4}/);
  });
});
