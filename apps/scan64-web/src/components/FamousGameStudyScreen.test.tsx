import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import type { FamousGameRead } from '../api/types';
import { FamousGameStudyScreen } from './FamousGameStudyScreen';

const chessgroundMock = vi.hoisted(() => ({
  destroy: vi.fn(),
  fens: [] as string[],
}));

vi.mock('chessground', () => ({
  Chessground: (_element: Element, config: { fen: string }) => {
    chessgroundMock.fens.push(config.fen);
    return {
      destroy: chessgroundMock.destroy,
      set: vi.fn(),
    };
  },
}));

vi.mock('../api/client', () => ({
  ApiClient: {
    createPlaySession: vi.fn(),
    getFamousGames: vi.fn(),
    recordFamousGameAttempt: vi.fn(),
  },
}));

const games: FamousGameRead[] = [
  {
    id: 'morphy-opera-1858',
    payload: {
      title: 'Opera Game',
      historical_context: 'A public-domain game from Paris.',
      strategic_context: 'Open lines decide the attack.',
      moves: ['e4', 'e5'],
      decisions: [
        {
          id: 'opera-open-lines',
          ply: 0,
          fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
          prompt: 'Which move starts the attack?',
          played_move: 'e4',
          accepted_moves: ['e4'],
          verified_alternatives: [{ san: 'Nf3', explanation: 'Develops a knight.' }],
          hints: ['Claim the centre.'],
          comparison: 'e4 claims the centre.',
        },
      ],
    },
    skill_mapping: { 'tactics.development': 1 },
  },
  {
    id: 'morphy-paulsen-1857',
    payload: {
      title: 'Paulsen Game',
      historical_context: 'A public-domain game from New York.',
      strategic_context: 'Space needs activity.',
      moves: ['e4', 'c5'],
      decisions: [
        {
          id: 'paulsen-outpost',
          ply: 0,
          fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
          prompt: 'Which square supports the bishop?',
          played_move: 'e4',
          accepted_moves: ['e4'],
          verified_alternatives: [{ san: 'Nf3', explanation: 'Develops a knight.' }],
          hints: ['Claim the centre.'],
          comparison: 'e4 claims the centre.',
        },
      ],
    },
    skill_mapping: { 'positional.outpost': 1 },
  },
];

describe('FamousGameStudyScreen', () => {
  beforeEach(() => {
    chessgroundMock.destroy.mockReset();
    chessgroundMock.fens.length = 0;
    localStorage.setItem('scan64_player_id', 'player-1');
    vi.mocked(ApiClient.getFamousGames).mockResolvedValue(games);
    vi.mocked(ApiClient.recordFamousGameAttempt).mockResolvedValue({
      id: 'attempt-1',
      success: true,
      hint_assisted: false,
    });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue({
      id: 'session-1',
      player_id: 'player-1',
      game_id: 'game-1',
      opponent_config: { strength: '1' },
      status: 'active',
    });
  });

  it('records a prediction against the selected decision and reveals its comparison', async () => {
    render(<FamousGameStudyScreen onPlayFromHere={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
    expect(screen.getByText('A public-domain game from Paris.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show Hint' }));
    expect(screen.getByRole('status')).toHaveTextContent('Claim the centre.');

    fireEvent.change(screen.getByLabelText('Your move'), { target: { value: 'e4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));

    await waitFor(() => {
      expect(ApiClient.recordFamousGameAttempt).toHaveBeenCalledWith('morphy-opera-1858', {
        player_id: 'player-1',
        decision_id: 'opera-open-lines',
        hint_assisted: true,
        response_payload: { move: 'e4' },
      });
    });
    expect(screen.getByTestId('move-comparison')).toHaveTextContent('e4 claims the centre.');
  });

  it('destroys and recreates Chessground when selecting another study', async () => {
    render(<FamousGameStudyScreen onPlayFromHere={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
    await waitFor(() => expect(chessgroundMock.fens).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: 'Back to Games' }));
    fireEvent.click(screen.getByRole('button', { name: 'Paulsen Game' }));

    await waitFor(() => expect(chessgroundMock.fens).toHaveLength(2));
    expect(chessgroundMock.destroy).toHaveBeenCalledTimes(1);
  });

  it('starts a server-backed play session from the selected decision FEN', async () => {
    const onPlayFromHere = vi.fn();
    render(<FamousGameStudyScreen onPlayFromHere={onPlayFromHere} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue from here against engine' }));

    await waitFor(() => {
      expect(ApiClient.createPlaySession).toHaveBeenCalledWith({
        player_id: 'player-1',
        opponent_config: { strength: '1' },
        initial_fen: games[0].payload.decisions[0].fen,
      });
    });
    expect(onPlayFromHere).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'session-1' }),
      games[0].payload.decisions[0].fen,
    );
  });
});
