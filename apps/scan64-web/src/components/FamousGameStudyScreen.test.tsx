import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ensurePlayerAuthorization } from '../api/client';
import type { FamousGameRead } from '../api/types';
import type { Key } from 'chessground/types';
import { FamousGameStudyScreen } from './FamousGameStudyScreen';

const chessgroundMock = vi.hoisted(() => ({
  destroy: vi.fn(),
  fens: [] as string[],
  after: undefined as ((from: Key, to: Key) => void) | undefined,
}));

vi.mock('chessground', () => ({
  Chessground: (_element: Element, config: {
    fen: string;
    movable: { events: { after: (from: Key, to: Key) => void } };
  }) => {
    chessgroundMock.fens.push(config.fen);
    chessgroundMock.after = config.movable.events.after;
    return {
      destroy: chessgroundMock.destroy,
      set: vi.fn(),
      redrawAll: vi.fn(),
    };
  },
}));

vi.mock('../api/client', () => ({
  ApiClient: {
    createPlaySession: vi.fn(),
    getFamousGames: vi.fn(),
    recordFamousGameAttempt: vi.fn(),
  },
  getOrCreatePlayerId: vi.fn(() => 'player-1'),
  ensurePlayerAuthorization: vi.fn(async () => 'player-1'),
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
    vi.clearAllMocks();
    vi.mocked(ensurePlayerAuthorization).mockResolvedValue('player-1');
    chessgroundMock.destroy.mockReset();
    chessgroundMock.fens.length = 0;
    chessgroundMock.after = undefined;
    localStorage.setItem('scan64_player_id', 'player-1');
    vi.mocked(ApiClient.getFamousGames).mockResolvedValue(games);
    vi.mocked(ApiClient.recordFamousGameAttempt).mockResolvedValue({
      id: 'attempt-1',
      success: true,
      hint_assisted: false,
    });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue({ id: 'session-1', player_id: 'player-1', game_id: 'game-1',
    opponent_config: { provider: 'stockfish', bot_level: '1' }, status: 'active', coach_mode: false });
  });

  it('keeps public study available when player authorization fails', async () => {
    vi.mocked(ensurePlayerAuthorization).mockRejectedValueOnce(new Error('Registration unavailable'));
    render(<FamousGameStudyScreen onPlayFromHere={vi.fn()} />);
    const game = await screen.findByRole('button', { name: 'Opera Game' });
    expect(await screen.findByRole('alert')).toHaveTextContent('Registration unavailable');
    fireEvent.click(game);
    fireEvent.change(screen.getByLabelText('Your move'), { target: { value: 'e4' } });
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Continue from here against engine' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('Predictions and engine play unavailable');
    expect(screen.getByRole('button', { name: 'Show Move' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Show Hint' })).toBeEnabled();
  });

  it('authorizes the study identity before recording a prediction', async () => {
    vi.mocked(ensurePlayerAuthorization).mockResolvedValueOnce('authorized-player');
    render(<FamousGameStudyScreen onPlayFromHere={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
    fireEvent.change(screen.getByLabelText('Your move'), { target: { value: 'Nf3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await waitFor(() => expect(ApiClient.recordFamousGameAttempt).toHaveBeenCalledWith(
      'morphy-opera-1858', expect.objectContaining({ player_id: 'authorized-player' }),
    ));
  });

  it('records a prediction against the selected decision and reveals its comparison', async () => {
    render(<FamousGameStudyScreen onPlayFromHere={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
    expect(screen.getByText('A public-domain game from Paris.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show Hint' }));
    expect(screen.getByText('Claim the centre.')).toHaveAttribute('role', 'status');

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

  it('shares a readable read-only board and rejects pointer moves without revealing the decision', async () => {
    render(<FamousGameStudyScreen onPlayFromHere={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
    expect(screen.getByRole('heading', { name: 'Opera Game' })).toHaveFocus();
    expect(screen.getByText('Read board position')).toBeInTheDocument();
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
    expect(screen.queryByLabelText('Move (UCI or SAN)')).not.toBeInTheDocument();
    expect(screen.queryByTestId('move-comparison')).not.toBeInTheDocument();
    expect(screen.queryByText('Claim the centre.')).not.toBeInTheDocument();
    await act(async () => { chessgroundMock.after?.('e2', 'e4'); });
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
    expect(ApiClient.recordFamousGameAttempt).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Show Move' }));
    expect(screen.getByText('e4: White pawn')).toBeInTheDocument();
    expect(screen.queryByLabelText('Move (UCI or SAN)')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Back to Games' }));
    expect(screen.getByRole('heading', { name: 'Study Famous Games' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Paulsen Game' }));
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
    expect(chessgroundMock.destroy).toHaveBeenCalledTimes(1);
  });

  it('converts normalized UCI to canonical SAN before posting the unchanged grading payload', async () => {
    render(<FamousGameStudyScreen onPlayFromHere={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
    fireEvent.change(screen.getByLabelText('Your move'), { target: { value: ' E2E4 ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await waitFor(() => expect(ApiClient.recordFamousGameAttempt).toHaveBeenCalledWith('morphy-opera-1858', {
      player_id: 'player-1',
      decision_id: 'opera-open-lines',
      hint_assisted: false,
      response_payload: { move: 'e4' },
    }));
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
  });

  it.each(['Nxb5', 'e2e5', 'nf3', 'e2-e4', 'e2e4q'])(
    'rejects illegal or noncanonical prediction %s without recording an attempt',
    async (prediction) => {
      render(<FamousGameStudyScreen onPlayFromHere={vi.fn()} />);
      fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
      const entry = screen.getByLabelText('Your move');
      fireEvent.change(entry, { target: { value: prediction } });
      fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
      expect(screen.getByRole('alert')).toHaveTextContent('Enter a legal move');
      expect(entry).toHaveAttribute('aria-invalid', 'true');
      expect(entry).toHaveFocus();
      expect(ApiClient.recordFamousGameAttempt).not.toHaveBeenCalled();
      expect(screen.queryByTestId('move-comparison')).not.toBeInTheDocument();
    },
  );

  it('rejects ambiguous SAN but posts disambiguated canonical SAN', async () => {
    const game = structuredClone(games[0]);
    game.payload.decisions[0].fen = '4k3/8/8/8/8/8/8/1N2KN2 w - - 0 1';
    vi.mocked(ApiClient.getFamousGames).mockResolvedValueOnce([game]);
    render(<FamousGameStudyScreen onPlayFromHere={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
    const entry = screen.getByLabelText('Your move');
    fireEvent.change(entry, { target: { value: 'Nd2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a legal move');
    expect(ApiClient.recordFamousGameAttempt).not.toHaveBeenCalled();
    fireEvent.change(entry, { target: { value: 'Nbd2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await waitFor(() => expect(ApiClient.recordFamousGameAttempt).toHaveBeenCalledWith(
      game.id, expect.objectContaining({ response_payload: { move: 'Nbd2' } }),
    ));
  });

  it('requires an explicit promotion and validates against the decision even after revealing its move', async () => {
    const game = structuredClone(games[0]);
    game.payload.decisions[0].fen = '7k/4P3/8/8/8/8/8/K7 w - - 0 1';
    game.payload.decisions[0].played_move = 'e8=Q+';
    vi.mocked(ApiClient.getFamousGames).mockResolvedValueOnce([game]);
    render(<FamousGameStudyScreen onPlayFromHere={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
    const entry = screen.getByLabelText('Your move');
    fireEvent.change(entry, { target: { value: 'e7e8' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Choose a promotion piece');
    expect(entry).toHaveFocus();
    expect(ApiClient.recordFamousGameAttempt).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Show Move' }));
    expect(screen.getByText('e8: White queen')).toBeInTheDocument();
    fireEvent.change(entry, { target: { value: 'e7e8n' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await waitFor(() => expect(ApiClient.recordFamousGameAttempt).toHaveBeenCalledWith(
      game.id, expect.objectContaining({ response_payload: { move: 'e8=N' } }),
    ));
  });

  it.each([
    ['7k/4P3/8/8/8/8/8/K7 w - - 0 1', 'e7e8q', 'e8=Q+'],
    ['7k/4P3/8/8/8/8/8/K7 w - - 0 1', 'e7e8r', 'e8=R+'],
    ['7k/4P3/8/8/8/8/8/K7 w - - 0 1', 'e7e8b', 'e8=B'],
    ['7k/4P3/8/8/8/8/8/K7 w - - 0 1', 'e7e8n', 'e8=N'],
    ['4k3/8/8/8/8/8/8/4K2R w K - 0 1', 'e1g1', 'O-O'],
  ])('posts canonical special-move SAN for %s / %s', async (fen, uci, san) => {
    const game = structuredClone(games[0]);
    game.payload.decisions[0].fen = fen;
    vi.mocked(ApiClient.getFamousGames).mockResolvedValueOnce([game]);
    render(<FamousGameStudyScreen onPlayFromHere={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
    fireEvent.change(screen.getByLabelText('Your move'), { target: { value: uci } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await waitFor(() => expect(ApiClient.recordFamousGameAttempt).toHaveBeenCalledWith(
      game.id, expect.objectContaining({ response_payload: { move: san } }),
    ));
  });

  it('starts a server-backed play session from the selected decision FEN', async () => {
    const onPlayFromHere = vi.fn();
    render(<FamousGameStudyScreen onPlayFromHere={onPlayFromHere} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Opera Game' }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue from here against engine' }));

    await waitFor(() => {
      expect(ApiClient.createPlaySession).toHaveBeenCalledWith({
        player_id: 'player-1',
        opponent_config: { provider: 'stockfish', bot_level: '1' },
        initial_fen: games[0].payload.decisions[0].fen,
      });
    });
    expect(onPlayFromHere).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'session-1' }),
      games[0].payload.decisions[0].fen,
    );
  });
});
