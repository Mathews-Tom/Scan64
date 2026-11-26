import { act, render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { AnalysisScreen } from './AnalysisScreen';
import { ApiClient, ApiRequestError } from '../api/client';
import { Chess } from 'chess.js';
import type { PositionAnalysisRead, PositionExplanationRead } from '../api/types';

vi.mock('chessground', () => ({
  Chessground: () => ({ set: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn() }),
}));

vi.mock('../api/client', () => ({
  ApiClient: {
    createGame: vi.fn(),
    createPlaySession: vi.fn(),
    analyzePosition: vi.fn(),
    explainPosition: vi.fn(),
  },
  ensurePlayerAuthorization: vi.fn(() => Promise.resolve('player-1')),
  getOrCreatePlayerId: vi.fn(() => 'player-1'),
  ApiRequestError: class ApiRequestError extends Error {
    status: number;

    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

describe('AnalysisScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(ApiClient.analyzePosition).mockReset();
    vi.mocked(ApiClient.analyzePosition).mockImplementation(async ({ fen }) => {
      const move = new Chess(fen).moves({ verbose: true })[0];
      return { fen, lines: move ? [{ score_cp: 20, mate: null, moves: [{ uci: `${move.from}${move.to}${move.promotion ?? ''}`, san: move.san }] }] : [] };
    });
  });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it('preserves prior branches and exported PGN when the backend rejects a parseable illegal FEN', async () => {
    render(<AnalysisScreen />);
    const enter = async (move: string) => {
      fireEvent.change(screen.getByLabelText('Move (UCI or SAN)'), { target: { value: move } });
      fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
      await waitFor(() => expect(screen.getByLabelText('Move (UCI or SAN)')).toHaveValue(''));
    };
    await enter('e4'); await enter('e5');
    fireEvent.click(screen.getByRole('button', { name: 'First' }));
    await enter('d4');
    await screen.findByLabelText('Line 1 evaluation +0.20');
    const illegal = 'rnbqkbnr/pppppppp/8/8/8/P7/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
    expect(new Chess(illegal).fen()).toBe(illegal);
    vi.mocked(ApiClient.analyzePosition).mockRejectedValueOnce(new ApiRequestError('Invalid chess position', 422));
    fireEvent.change(screen.getByLabelText('FEN'), { target: { value: illegal } });
    fireEvent.click(screen.getByRole('button', { name: 'Load FEN' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid FEN');
    const variations = within(screen.getByRole('list', { name: 'Retained variations' }));
    expect(variations.getByRole('button', { name: '1... e5' })).toBeInTheDocument();
    expect(variations.getByRole('button', { name: 'Variation: 1. d4' })).toHaveAttribute('aria-current', 'step');
    expect(screen.getByText('d4: White pawn')).toBeInTheDocument();
    let exported: Blob | undefined;
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn((blob: Blob) => { exported = blob; return 'blob:analysis'; }),
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole('button', { name: 'Download PGN' }));
    const file = Promise.withResolvers<string>();
    const reader = new FileReader();
    reader.onload = () => file.resolve(String(reader.result));
    reader.onerror = () => file.reject(reader.error);
    reader.readAsText(exported!);
    const pgn = await file.promise;
    expect(pgn).toContain('1. e4 (1. d4) 1... e5 *');
    expect(pgn).not.toContain(illegal);
  });

  it('ignores a successful pending FEN load after the user makes a move in the existing tree', async () => {
    render(<AnalysisScreen />);
    await screen.findByLabelText('Line 1 evaluation +0.20');
    const pending = Promise.withResolvers<PositionAnalysisRead>();
    vi.mocked(ApiClient.analyzePosition).mockReturnValueOnce(pending.promise);
    const candidate = '7k/4P3/8/8/8/8/8/K7 w - - 0 1';
    fireEvent.change(screen.getByLabelText('FEN'), { target: { value: candidate } });
    fireEvent.click(screen.getByRole('button', { name: 'Load FEN' }));
    await waitFor(() => expect(ApiClient.analyzePosition).toHaveBeenLastCalledWith({ player_id: 'player-1', fen: candidate }));
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Make move' })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Move (UCI or SAN)'), { target: { value: 'e4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
    const position = new Chess(); position.move('e4');
    await waitFor(() => expect(screen.getByLabelText('FEN')).toHaveValue(position.fen()));
    await act(async () => pending.resolve({ fen: candidate, lines: [{
      score_cp: 950, mate: null, moves: [{ uci: 'e7e8q', san: 'e8=Q+' }],
    }] }));
    expect(screen.getByLabelText('FEN')).toHaveValue(position.fen());
    expect(screen.getByText('e4: White pawn')).toBeInTheDocument();
    expect(screen.queryByLabelText('Line 1 evaluation +9.50')).not.toBeInTheDocument();
  });

  it('loads a server-validated busy FEN without immediately issuing a duplicate search', async () => {
    render(<AnalysisScreen />);
    await screen.findByLabelText('Line 1 evaluation +0.20');
    const candidate = '7k/4P3/8/8/8/8/8/K7 w - - 0 1';
    vi.mocked(ApiClient.analyzePosition).mockRejectedValueOnce(new ApiRequestError('Engine busy', 503));
    fireEvent.change(screen.getByLabelText('FEN'), { target: { value: candidate } });
    fireEvent.click(screen.getByRole('button', { name: 'Load FEN' }));
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.getByText('e7: White pawn')).toBeInTheDocument();
    expect(ApiClient.analyzePosition).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByLabelText('Line 1 evaluation +0.20');
    expect(ApiClient.analyzePosition).toHaveBeenCalledTimes(3);
  });

  it.each([
    new ApiRequestError('Player authorization required', 401),
    new Error('Network unavailable'),
  ])('keeps the existing tree if a candidate FEN cannot be validated: %s', async failure => {
    render(<AnalysisScreen />);
    await screen.findByLabelText('Line 1 evaluation +0.20');
    vi.mocked(ApiClient.analyzePosition).mockRejectedValueOnce(failure);
    fireEvent.change(screen.getByLabelText('FEN'), { target: { value: '7k/4P3/8/8/8/8/8/K7 w - - 0 1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load FEN' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(failure.message);
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Retained variations' })).toHaveTextContent('Initial position');
    expect(screen.queryByText('e7: White pawn')).not.toBeInTheDocument();
  });

  it('plays the entire legal prefix through a clicked PV move and refreshes the selected position', async () => {
    const initial = new Chess().fen();
    vi.mocked(ApiClient.analyzePosition).mockResolvedValueOnce({ fen: initial, lines: [{
      score_cp: 34, mate: null,
      moves: [{ uci: 'e2e4', san: 'e4' }, { uci: 'e7e5', san: 'e5' }, { uci: 'g1f3', san: 'Nf3' }],
    }, { score_cp: null, mate: -4, moves: [{ uci: 'd2d4', san: 'd4' }] }] });
    render(<AnalysisScreen />);
    expect(await screen.findByLabelText('Line 1 evaluation +0.34')).toBeInTheDocument();
    expect(screen.getByLabelText('Line 2 evaluation −M4')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Play line 1 through 2: e5' }));
    const position = new Chess();
    position.move('e4'); position.move('e5');
    await waitFor(() => expect(ApiClient.analyzePosition).toHaveBeenLastCalledWith({ player_id: 'player-1', fen: position.fen() }));
    expect(screen.getByLabelText('FEN')).toHaveValue(position.fen());
    const variations = within(screen.getByRole('list', { name: 'Retained variations' }));
    expect(variations.getByRole('button', { name: '1. e4' })).toBeInTheDocument();
    expect(variations.getByRole('button', { name: '1... e5' })).toHaveAttribute('aria-current', 'step');
    expect(variations.queryByRole('button', { name: /Nf3/ })).not.toBeInTheDocument();
  });

  it('retains and selects sibling lines, reuses a child move and only replaces the tree on valid FEN load', async () => {
    render(<AnalysisScreen />);
    const enter = async (san: string) => {
      fireEvent.change(screen.getByLabelText('Move (UCI or SAN)'), { target: { value: san } });
      fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
      await waitFor(() => expect(screen.getByLabelText('Move (UCI or SAN)')).toHaveValue(''));
    };
    await enter('e4');
    await enter('e5');
    fireEvent.click(screen.getByRole('button', { name: 'First' }));
    await enter('d4');
    await enter('d5');
    const variations = within(screen.getByRole('list', { name: 'Retained variations' }));
    expect(variations.getByRole('button', { name: '1... e5' })).toBeInTheDocument();
    expect(variations.getByRole('button', { name: 'Variation: 1... d5' })).toHaveAttribute('aria-current', 'step');
    fireEvent.click(variations.getByRole('button', { name: '1. e4' }));
    expect(screen.getByText('e4: White pawn')).toBeInTheDocument();
    await enter('e5');
    expect(variations.getAllByRole('button', { name: '1... e5' })).toHaveLength(1);
    fireEvent.change(screen.getByLabelText('FEN'), { target: { value: 'not a FEN' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load FEN' }));
    expect(variations.getByRole('button', { name: 'Variation: 1. d4' })).toBeInTheDocument();
    expect(variations.getByRole('button', { name: '1... e5' })).toHaveAttribute('aria-current', 'step');
    fireEvent.change(screen.getByLabelText('FEN'), { target: { value: new Chess().fen() } });
    fireEvent.click(screen.getByRole('button', { name: 'Load FEN' }));
    await waitFor(() => expect(variations.getAllByRole('button')).toHaveLength(1));
    expect(screen.getByText('Position loaded. Started a new variation tree.')).toBeInTheDocument();
  });

  it('ignores stale results even after returning to the same FEN (ABA)', async () => {
    const firstA = Promise.withResolvers<PositionAnalysisRead>();
    const positionB = Promise.withResolvers<PositionAnalysisRead>();
    const secondA = Promise.withResolvers<PositionAnalysisRead>();
    vi.mocked(ApiClient.analyzePosition)
      .mockReturnValueOnce(firstA.promise)
      .mockReturnValueOnce(positionB.promise)
      .mockReturnValueOnce(secondA.promise);
    render(<AnalysisScreen />);
    await waitFor(() => expect(ApiClient.analyzePosition).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText('Move (UCI or SAN)'), { target: { value: 'e4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
    await waitFor(() => expect(ApiClient.analyzePosition).toHaveBeenCalledTimes(2));
    const blackFen = vi.mocked(ApiClient.analyzePosition).mock.calls[1][0].fen;
    fireEvent.click(screen.getByRole('button', { name: 'First' }));
    await waitFor(() => expect(ApiClient.analyzePosition).toHaveBeenCalledTimes(3));
    await act(async () => secondA.resolve({ fen: new Chess().fen(), lines: [{
      score_cp: 55, mate: null, moves: [{ uci: 'e2e4', san: 'e4' }],
    }] }));
    expect(screen.getByLabelText('Line 1 evaluation +0.55')).toBeInTheDocument();
    await act(async () => {
      firstA.resolve({ fen: new Chess().fen(), lines: [{
        score_cp: 111, mate: null, moves: [{ uci: 'd2d4', san: 'd4' }],
      }] });
      positionB.resolve({ fen: blackFen, lines: [{
        score_cp: null, mate: -5, moves: [{ uci: 'e7e5', san: 'e5' }],
      }] });
    });
    expect(screen.getByLabelText('Line 1 evaluation +0.55')).toBeInTheDocument();
    expect(screen.queryByLabelText('Line 1 evaluation +1.11')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Line 1 evaluation −M5')).not.toBeInTheDocument();
  });

  it('keeps the board usable while evaluating and retries busy responses only on demand', async () => {
    const pending = Promise.withResolvers<PositionAnalysisRead>();
    vi.mocked(ApiClient.analyzePosition).mockReturnValueOnce(pending.promise);
    render(<AnalysisScreen />);
    await waitFor(() => expect(ApiClient.analyzePosition).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('button', { name: 'Make move' })).toBeEnabled();
    await act(async () => pending.reject(new ApiRequestError('All search slots are occupied. Retry shortly.', 503)));
    expect(screen.getByRole('alert')).toHaveTextContent('Engine busy or unavailable.');
    expect(ApiClient.analyzePosition).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByLabelText('Line 1 evaluation +0.20')).toBeInTheDocument();
    expect(ApiClient.analyzePosition).toHaveBeenCalledTimes(2);
  });

  it('discards a stale failure after selecting a newer position', async () => {
    const pending = Promise.withResolvers<PositionAnalysisRead>();
    vi.mocked(ApiClient.analyzePosition).mockReturnValueOnce(pending.promise);
    render(<AnalysisScreen />);
    await waitFor(() => expect(ApiClient.analyzePosition).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText('Move (UCI or SAN)'), { target: { value: 'd4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
    expect(await screen.findByLabelText('Line 1 evaluation +0.20')).toBeInTheDocument();
    await act(async () => pending.reject(new ApiRequestError('Old search busy', 503)));
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Line 1 evaluation +0.20')).toBeInTheDocument();
  });

  it('explains terminal positions without fabricating engine lines', async () => {
    render(<AnalysisScreen />);
    const fen = '7k/6Q1/5K2/8/8/8/8/8 b - - 0 42';
    fireEvent.change(screen.getByLabelText('FEN'), { target: { value: fen } });
    fireEvent.click(screen.getByRole('button', { name: 'Load FEN' }));
    expect(await screen.findByText('No legal continuation: this position has ended.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Play line/ })).not.toBeInTheDocument();
  });

  it('requests authenticated analysis without a gameId', async () => {
    render(<AnalysisScreen />);
    expect(screen.getByText('Analysis Board')).toBeInTheDocument();
    expect(await screen.findByLabelText('Line 1 evaluation +0.20')).toBeInTheDocument();
    expect(ApiClient.analyzePosition).toHaveBeenCalledWith({ player_id: 'player-1', fen: new Chess().fen() });
  });
  it('allows setting arbitrary FEN positions without repeating the accepted evaluation', async () => {
    render(<AnalysisScreen />);

    const fenInput = screen.getByPlaceholderText('Paste FEN here');
    const loadBtn = screen.getByText('Load FEN');
    const testFen = 'rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2';

    fireEvent.change(fenInput, { target: { value: testFen } });
    fireEvent.click(loadBtn);

    await waitFor(() => expect(fenInput).toHaveValue(new Chess(testFen).fen()));
    expect(vi.mocked(ApiClient.analyzePosition).mock.calls.filter(([data]) => data.fen === new Chess(testFen).fen())).toHaveLength(1);
    expect(screen.queryByText('Invalid FEN')).not.toBeInTheDocument();
  });

  it('uses the shared accessible board for UCI and SAN without losing the authoritative FEN', async () => {
    render(<AnalysisScreen />);
    expect(screen.getByText('Read board position')).toBeInTheDocument();
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
    const entry = screen.getByLabelText('Move (UCI or SAN)');
    fireEvent.change(entry, { target: { value: ' E2E4 ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
    await waitFor(() => expect(screen.getByLabelText('FEN')).toHaveValue(
      'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
    ));
    fireEvent.change(entry, { target: { value: 'e5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
    await waitFor(() => expect(screen.getByTestId('free-analysis-board-san-move-list')).toHaveTextContent('1... e5'));
    expect(screen.getByText('e4: White pawn')).toBeInTheDocument();
    expect(screen.getByText('e5: Black pawn')).toBeInTheDocument();
  });

  it('loads a promotion position without implicitly promoting a keyboard move', async () => {
    render(<AnalysisScreen />);
    fireEvent.change(screen.getByLabelText('FEN'), { target: { value: '7k/4P3/8/8/8/8/8/K7 w - - 0 1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load FEN' }));
    await screen.findByText('Position loaded. Started a new variation tree.');
    fireEvent.change(screen.getByLabelText('Move (UCI or SAN)'), { target: { value: 'e7e8' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
    expect(screen.getByRole('group', { name: 'Choose promotion piece' })).toBeInTheDocument();
    expect(screen.getByLabelText('FEN')).toHaveValue('7k/4P3/8/8/8/8/8/K7 w - - 0 1');
    fireEvent.click(screen.getByRole('button', { name: 'Promote to rook' }));
    await waitFor(() => expect(screen.getByText('e8: White rook')).toBeInTheDocument());
    expect(screen.queryByText('e8: White knight')).not.toBeInTheDocument();
  });

  it('announces an invalid FEN and restores focus to its labelled field', () => {
    render(<AnalysisScreen />);
    fireEvent.change(screen.getByLabelText('FEN'), { target: { value: 'not a position' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load FEN' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid FEN');
    expect(screen.getByLabelText('FEN')).toHaveFocus();
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
  });

  it('copies the current FEN to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    render(<AnalysisScreen />);
    fireEvent.click(screen.getByText('Copy FEN'));

    expect(writeText).toHaveBeenCalledWith(
      'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
    );
    expect(await screen.findByText('FEN copied.')).toBeInTheDocument();
  });

  it('starts a play session from the analysed position', async () => {
    const onPlayFromHere = vi.fn();
    const playSession = { id: 'session-1', player_id: 'player-1', game_id: 'game-1',
    opponent_config: { provider: 'stockfish', bot_level: '1' }, status: 'active', coach_mode: false, assistance_modes: [] };
    vi.mocked(ApiClient.createGame).mockResolvedValue({
      id: 'game-1',
      pgn: '',
      white: 'White',
      black: 'Black',
      result: '*',
    });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue(playSession);

    render(<AnalysisScreen onPlayFromHere={onPlayFromHere} />);
    fireEvent.click(screen.getByTestId('play-from-here'));

    await waitFor(() => {
      expect(ApiClient.createPlaySession).toHaveBeenCalledWith(
        expect.objectContaining({ game_id: 'game-1', opponent_config: { provider: 'stockfish', bot_level: '1' } })
      );
    });
    expect(onPlayFromHere).toHaveBeenCalledWith(
      playSession,
      'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
    );
  });

  describe('Explain lines', () => {
    const startFen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
    const explained: PositionExplanationRead = {
      fen: startFen,
      source: 'template',
      fallback_reason: null,
      text: 'White to move. Line 1: 1.e4 e5. Line 2 (1.d4) scores 0.11 pawns less than line 1 for the side to move.',
      lines: [
        { line_id: 'line-1', rank: 1, score_cp: 41, mate: null, depth: 14, seldepth: 24,
          moves: [{ uci: 'e2e4', san: 'e4' }, { uci: 'e7e5', san: 'e5' }] },
        { line_id: 'line-2', rank: 2, score_cp: 30, mate: null, depth: 13, seldepth: 20,
          moves: [{ uci: 'd2d4', san: 'd4' }, { uci: 'g8f6', san: 'Nf6' }] },
      ],
      claims: [
        { text: 'White to move. The engine\'s first choice is 1.e4.', evidence_ref: 'line-1', line_id: 'line-1', moves: ['e2e4'] },
        { text: 'Line 1: 1.e4 e5.', evidence_ref: 'line-1', line_id: 'line-1', moves: ['e2e4', 'e7e5'] },
        { text: 'Line 2 (1.d4) scores 0.11 pawns less than line 1 for the side to move.', evidence_ref: 'comparison',
          line_id: 'line-2', moves: ['d2d4', 'g8f6'] },
      ],
      facts: {},
    };
    const cited = () => Array.from(document.querySelectorAll('[data-cited="true"]')).map(node => node.textContent);

    it('shows the explanation next to the lines and highlights the moves each claim cites', async () => {
      vi.mocked(ApiClient.explainPosition).mockResolvedValue(explained);
      render(<AnalysisScreen />);
      fireEvent.click(await screen.findByRole('button', { name: 'Explain lines' }));
      const claims = within(await screen.findByRole('list', { name: 'Explanation claims' }));
      expect(ApiClient.explainPosition).toHaveBeenCalledWith({ player_id: 'player-1', fen: startFen });
      expect(screen.getByText(/Template explanation from the engine lines/)).toBeInTheDocument();
      // The board shows exactly the explained lines.
      expect(screen.getByLabelText('Line 2 evaluation +0.30')).toBeInTheDocument();
      expect(cited()).toEqual([]);

      const comparison = claims.getByRole('button', { name: /^Line 2: Line 2 \(1\.d4\)/ });
      fireEvent.mouseEnter(comparison);
      expect(cited()).toEqual(['d4', 'Nf6']);
      fireEvent.mouseLeave(comparison);
      expect(cited()).toEqual([]);

      const opening = claims.getByRole('button', { name: /^Line 1: White to move/ });
      fireEvent.click(opening);
      expect(opening).toHaveAttribute('aria-pressed', 'true');
      expect(cited()).toEqual(['e4']);
      fireEvent.mouseEnter(comparison);
      expect(cited()).toEqual(['d4', 'Nf6']);
      fireEvent.mouseLeave(comparison);
      expect(cited()).toEqual(['e4']);
      fireEvent.click(opening);
      expect(cited()).toEqual([]);
    });

    it('labels provider wording that passed grounding', async () => {
      vi.mocked(ApiClient.explainPosition).mockResolvedValue({ ...explained, source: 'narrator' });
      render(<AnalysisScreen />);
      fireEvent.click(await screen.findByRole('button', { name: 'Explain lines' }));
      expect(await screen.findByText(/Worded by your configured model/)).toBeInTheDocument();
    });

    it('reports a failed explanation without discarding the engine lines', async () => {
      vi.mocked(ApiClient.explainPosition).mockRejectedValue(
        new ApiRequestError('Failed to explain engine lines: Analysis capacity is busy; try again shortly', 503),
      );
      render(<AnalysisScreen />);
      fireEvent.click(await screen.findByRole('button', { name: 'Explain lines' }));
      expect(await screen.findByRole('alert')).toHaveTextContent('Analysis capacity is busy');
      expect(screen.getByLabelText('Line 1 evaluation +0.20')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Explain lines' })).toBeEnabled();
    });

    it('drops an explanation that arrives after the position changed', async () => {
      const pending = Promise.withResolvers<PositionExplanationRead>();
      vi.mocked(ApiClient.explainPosition).mockReturnValue(pending.promise);
      render(<AnalysisScreen />);
      fireEvent.click(await screen.findByRole('button', { name: 'Explain lines' }));
      fireEvent.change(screen.getByLabelText('Move (UCI or SAN)'), { target: { value: 'e4' } });
      fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
      await act(async () => { pending.resolve(explained); await pending.promise; });
      await screen.findByRole('button', { name: 'Explain lines' });
      expect(screen.queryByRole('list', { name: 'Explanation claims' })).not.toBeInTheDocument();
    });
  });
});
