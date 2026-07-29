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
      items: [{ id: 'game-1', white: 'Alice', black: 'Bob', result: '1-0', date: '2026.07.28', created_at: '2026-07-28T12:00:00Z', diagnosis_count: 2 }],
      next_cursor: null,
    });
    const onOpenGame = vi.fn();

    render(<GamesListScreen onOpenGame={onOpenGame} />);

    await waitFor(() => expect(screen.getByRole('button', { name: /Alice vs Bob/ })).toBeInTheDocument());
    expect(ApiClient.getPlayerGames).toHaveBeenCalledWith('player-1');
    fireEvent.click(screen.getByRole('button', { name: /Alice vs Bob/ }));
    expect(onOpenGame).toHaveBeenCalledWith('game-1');
  });
});
