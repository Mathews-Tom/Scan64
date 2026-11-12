import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import type { Key } from 'chessground/types';
import { AnalysisScreen } from './AnalysisScreen';
import { ApiClient, ApiRequestError } from '../api/client';
import { makeGameReview, reviewLesson } from './review.test-fixtures';
import type { GameReviewRead } from '../api/types';

const boards = vi.hoisted(() => new Map<string, { set: Mock; after?: (from: Key, to: Key) => void }>());
vi.mock('chessground', () => ({ Chessground: (element: Element,
  config: { movable?: { events?: { after?: (from: Key, to: Key) => void } } }) => {
  const board = { set: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn(), after: config.movable?.events?.after };
  boards.set(element.getAttribute('data-testid') ?? '', board);
  return board;
} }));

beforeEach(() => {
  boards.clear();
  localStorage.setItem('scan64_player_id', 'player-1');
  localStorage.setItem('scan64_player_token:player-1', 'secret');
  vi.spyOn(ApiClient, 'getGameReview').mockResolvedValue(makeGameReview());
  vi.spyOn(ApiClient, 'getGameAnalysisStatus').mockResolvedValue({ status: 'completed' });
  vi.spyOn(ApiClient, 'backfillGameReview').mockResolvedValue(makeGameReview({ key_moments: [], session_id: null }));
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

describe('owned move-by-move review', () => {
  it.each([[3, 3], [999, 7], [Number.NaN, 0], [-1, 0]])('opens a linked source ply %s safely at %s', async (initialPly, expectedPly) => {
    render(<AnalysisScreen gameId="game-1" initialPly={initialPly} />);
    await screen.findByTestId('review-move-list');
    expect(screen.getByTestId(`review-graph-ply-${expectedPly}`)).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows tactical proof and exclusions, links credited ply, and makes no external-independence claim', async () => {
    const review = makeGameReview();
    review.moves[0].credit = { skill_id: 'threat_processing.missed_capture', explanation: 'The legal line wins a pawn without merely restoring material.' };
    review.moves[2].credit_exclusion = 'coach mode';
    review.moves[4].credit_exclusion = 'per-game credit cap reached';
    vi.mocked(ApiClient.getGameReview).mockResolvedValue(review);
    render(<AnalysisScreen gameId="game-1" />);
    const credits = await screen.findByRole('region', { name: 'Good move credit' });
    expect(credits).toHaveTextContent('The legal line wins a pawn without merely restoring material.');
    expect(credits).toHaveTextContent('not independence from external assistance');
    expect(credits).toHaveTextContent('at most two per skill per game');
    expect(screen.getByTestId('review-credit-1')).toHaveTextContent('no in-app hint');
    expect(screen.getByTestId('review-credit-3')).toHaveTextContent('coach mode');
    expect(screen.getByTestId('review-credit-5')).toHaveTextContent('per-game credit cap reached');
    expect(screen.queryByTestId('review-credit-2')).not.toBeInTheDocument();
    fireEvent.click(within(credits).getByRole('button', { name: /Credited move at ply 1/ }));
    expect(screen.getByTestId('review-move-1')).toHaveAttribute('aria-current', 'step');
    expect(boards.get('review-board')!.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: review.moves[0].fen }));
  });

  it('continues graph arrow navigation from the current selection while focus stays on one point', async () => {
    const review = makeGameReview();
    render(<AnalysisScreen gameId="game-1" />);
    const point = await screen.findByTestId('review-graph-ply-0');
    point.focus();
    for (const move of review.moves) {
      fireEvent.keyDown(point, { key: 'ArrowRight' });
      expect(screen.getByTestId(`review-graph-ply-${move.ply}`)).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getByTestId(`review-move-${move.ply}`)).toHaveAttribute('aria-current', 'step');
      expect(boards.get('review-board')!.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: move.fen }));
    }
    expect(point).toHaveFocus();
    fireEvent.keyDown(point, { key: 'ArrowRight' });
    expect(screen.getByTestId('review-graph-ply-7')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.keyDown(point, { key: 'ArrowLeft' });
    fireEvent.keyDown(point, { key: 'ArrowLeft' });
    expect(screen.getByTestId('review-graph-ply-5')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('review-move-5')).toHaveAttribute('aria-current', 'step');
  });

  it('links every move, including unflagged mate, to the board, graph and markers', async () => {
    const review = makeGameReview();
    render(<AnalysisScreen gameId="game-1" />);
    await screen.findByTestId('review-move-list');
    expect(screen.getByTestId('review-selected-position')).toHaveTextContent('Initial position');
    expect(boards.get('review-board')!.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: review.initial_fen, lastMove: [] }));
    for (const move of review.moves) {
      fireEvent.click(screen.getByTestId(`review-move-${move.ply}`));
      expect(screen.getByTestId(`review-move-${move.ply}`)).toHaveAttribute('aria-current', 'step');
      expect(screen.getByTestId(`review-graph-ply-${move.ply}`)).toHaveAttribute('aria-pressed', 'true');
      expect(boards.get('review-board')!.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: move.fen }));
    }
    expect(screen.getByTestId('review-selected-position')).toHaveTextContent('Qxf7# (final position)');
    fireEvent.click(screen.getByRole('button', { name: 'Key moment at ply 3' }));
    expect(screen.getByTestId('review-move-3')).toHaveAttribute('aria-current', 'step');
    expect(screen.getByTestId('review-graph-ply-3')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByTestId('review-graph-ply-4'));
    expect(screen.getByTestId('review-move-4')).toHaveAttribute('aria-current', 'step');
    expect(boards.get('review-board')!.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: review.moves[3].fen }));
    fireEvent.keyDown(screen.getByTestId('review-graph-ply-0'), { key: 'Enter' });
    expect(screen.getByTestId('review-selected-position')).toHaveTextContent('Initial position');
    fireEvent.click(screen.getByRole('button', { name: 'Last' }));
    expect(screen.getByTestId('review-move-7')).toHaveAttribute('aria-current', 'step');
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  });

  it('shows server owner accuracy with all band counts, coverage, unscored and opponent labels', async () => {
    render(<AnalysisScreen gameId="game-1" />);
    expect(await screen.findByTestId('review-accuracy')).toHaveTextContent('95.7%');
    expect(screen.getByTestId('review-coverage')).toHaveTextContent('3 / 4 owner moves (75.0%)');
    const counts = screen.getByTestId('review-band-counts');
    for (const label of ['Sound', 'Inaccuracy', 'Mistake', 'Blunder', 'Unscored']) expect(counts).toHaveTextContent(label);
    expect(screen.getByTestId('review-move-1')).toHaveTextContent('Your move · Sound');
    expect(screen.getByTestId('review-move-2')).toHaveTextContent('Opponent move');
    expect(screen.getByTestId('review-move-5')).toHaveTextContent('Your move · Unscored');
    expect(screen.getByTestId('review-graph-ply-5')).toHaveAccessibleName(/probability unavailable/);
    expect(screen.getByText(/not Elo or Lichess accuracy/)).toBeInTheDocument();
  });

  it('keeps the graph White-perspective for a Black owner and uses only the server owner summary', async () => {
    const review = makeGameReview();
    vi.mocked(ApiClient.getGameReview).mockResolvedValue({ ...review, owner_color: 'black', accuracy: 80,
      scored_moves: 2, total_owner_moves: 3,
      moves: review.moves.map(move => ({ ...move, is_owner_move: move.mover === 'black', band: move.mover === 'black' ? 'sound' : null })),
    });
    render(<AnalysisScreen gameId="game-1" />);
    expect(await screen.findByTestId('review-accuracy')).toHaveTextContent('80.0%');
    expect(screen.getByTestId('review-coverage')).toHaveTextContent('2 / 3 owner moves');
    expect(screen.getByTestId('review-graph-ply-7')).toHaveAccessibleName(/100.0% White win probability/);
    await waitFor(() => expect(boards.get('review-board')?.set).toHaveBeenLastCalledWith(expect.objectContaining({ orientation: 'black' })));
    expect(screen.getByTestId('review-move-1')).toHaveTextContent('Opponent move');
  });

  it('does not infer an unknown owner or present unavailable accuracy as zero', async () => {
    const review = makeGameReview();
    vi.mocked(ApiClient.getGameReview).mockResolvedValue({ ...review, owner_color: null, accuracy: null,
      moves: review.moves.map(move => ({ ...move, is_owner_move: false, band: null })), key_moments: [], session_id: null,
    });
    render(<AnalysisScreen gameId="game-1" />);
    expect(await screen.findByTestId('review-unknown-owner')).toHaveTextContent('Your side is unknown');
    expect(screen.getByTestId('review-accuracy')).toHaveTextContent('Unavailable');
    expect(screen.getByTestId('review-move-1')).toHaveTextContent('White · owner unknown');
    expect(screen.queryByRole('button', { name: 'Retry this moment' })).not.toBeInTheDocument();
  });

  it('offers explicit review-only backfill and then GETs the learning context separately', async () => {
    const review = makeGameReview();
    vi.mocked(ApiClient.getGameReview).mockResolvedValueOnce({ ...review, backfill_required: true, accuracy: null,
      scored_moves: 0, moves: review.moves.map(move => ({ ...move, loss: null, white_probability: null,
        band: move.is_owner_move ? 'unscored' : null })), key_moments: [], session_id: null,
    }).mockResolvedValue(review);
    render(<AnalysisScreen gameId="game-1" />);
    const action = await screen.findByTestId('backfill-review');
    expect(ApiClient.backfillGameReview).not.toHaveBeenCalled();
    expect(screen.getByTestId('review-move-list').children).toHaveLength(7);
    expect(screen.getByTestId('review-accuracy')).toHaveTextContent('Unavailable');
    fireEvent.click(action);
    await screen.findByTestId('review-key-moment-3');
    expect(ApiClient.backfillGameReview).toHaveBeenCalledTimes(1);
    expect(ApiClient.backfillGameReview).toHaveBeenCalledWith('game-1');
    expect(ApiClient.getGameReview).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('backfill-review')).not.toBeInTheDocument();
  });

  it('starts retry at server lifetime counts and cannot reset the final attempt by reopening', async () => {
    const review = makeGameReview();
    review.key_moments[0].attempts_used = 2;
    review.key_moments[0].attempts_remaining = 1;
    vi.mocked(ApiClient.getGameReview).mockResolvedValue(review);
    const record = vi.spyOn(ApiClient, 'recordLessonAttempt').mockResolvedValue({ id: 'attempt', success: false, grading_status: 'verified', profile_update_result: 'applied' });
    render(<AnalysisScreen gameId="game-1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry this moment' }));
    const retry = screen.getByTestId('critical-moment-review');
    expect(retry).toHaveTextContent('2 lifetime attempts used; 1 remaining.');
    expect(retry).toHaveTextContent('Consider your knight.');
    fireEvent.click(screen.getByTestId('request-next-hint'));
    expect(screen.getByTestId('hint-2')).toHaveTextContent('Try Nf3.');
    fireEvent.click(screen.getByRole('button', { name: 'Close retry' }));
    fireEvent.click(screen.getByRole('button', { name: 'Retry this moment' }));
    expect(screen.getByTestId('critical-moment-review')).toHaveTextContent('2 lifetime attempts used; 1 remaining.');
    expect(screen.getByTestId('hint-2')).toHaveTextContent('Try Nf3.');
    await act(async () => { boards.get('lesson-board')!.after!('g1', 'f3'); });
    await waitFor(() => expect(record).toHaveBeenCalledWith(expect.objectContaining({ session_id: 'study-1', lesson_id: 'opportunity-1', source_kind: 'persisted_opportunity', hints_used: 3 })));
    const moment = screen.getByTestId('review-key-moment-3');
    expect(moment).toHaveTextContent('3 lifetime attempts used; 0 remaining.');
    expect(moment).toHaveTextContent('Accepted moves: Nf3');
    expect(within(moment).queryByRole('button', { name: 'Retry this moment' })).not.toBeInTheDocument();
  });

  it('preserves Correct and the recorded result panel after success on the last lifetime attempt', async () => {
    const review = makeGameReview();
    review.key_moments[0].attempts_used = 2;
    review.key_moments[0].attempts_remaining = 1;
    vi.mocked(ApiClient.getGameReview).mockResolvedValue(review);
    const record = vi.spyOn(ApiClient, 'recordLessonAttempt').mockResolvedValue({
      id: 'last-attempt', success: true, grading_status: 'verified', profile_update_result: 'applied',
    });
    render(<AnalysisScreen gameId="game-1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry this moment' }));
    await act(async () => { boards.get('lesson-board')!.after!('g1', 'f3'); });
    const recorded = await screen.findByTestId('critical-attempt-recorded');
    expect(recorded).toBeVisible();
    const panel = screen.getByTestId('critical-moment-review');
    expect(panel).toBeVisible();
    expect(within(panel).getByRole('alert')).toHaveTextContent('Correct. Attempt recorded.');
    expect(within(panel).getByTestId('explanation')).toHaveTextContent('Accepted moves: Nf3');
    expect(screen.getByTestId('review-key-moment-3')).toHaveTextContent('3 lifetime attempts used; 0 remaining.');
    expect(screen.queryByRole('button', { name: 'Retry this moment' })).not.toBeInTheDocument();
    expect(boards.get('lesson-board')!.set).toHaveBeenLastCalledWith(expect.objectContaining({
      movable: { color: undefined, dests: undefined },
    }));
    await act(async () => { boards.get('lesson-board')!.after!('g1', 'f3'); });
    expect(record).toHaveBeenCalledTimes(1);
    expect(within(panel).getByRole('alert')).toHaveTextContent('Correct. Attempt recorded.');
  });

  it('shows exhausted moments as explanations and accepted moves without a retry board', async () => {
    const review = makeGameReview();
    review.key_moments[0].attempts_used = 3;
    review.key_moments[0].attempts_remaining = 0;
    vi.mocked(ApiClient.getGameReview).mockResolvedValue(review);
    render(<AnalysisScreen gameId="game-1" />);
    const moment = await screen.findByTestId('review-key-moment-3');
    expect(moment).toHaveTextContent(reviewLesson.explanation.text);
    expect(moment).toHaveTextContent('Accepted moves: Nf3');
    expect(screen.queryByTestId('lesson-board')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry this moment' })).not.toBeInTheDocument();
  });

  it('preserves play-from-here at the selected replay position', async () => {
    const review = makeGameReview();
    const session = { id: 'play-1', player_id: 'player-1', game_id: 'new-game', opponent_config: { provider: 'stockfish', bot_level: '1' }, status: 'active', coach_mode: false };
    vi.spyOn(ApiClient, 'createGame').mockResolvedValue({ id: 'new-game', pgn: '', white: 'White', black: 'Black', result: '*' });
    vi.spyOn(ApiClient, 'createPlaySession').mockResolvedValue(session);
    const onPlay = vi.fn();
    render(<AnalysisScreen gameId="game-1" onPlayFromHere={onPlay} />);
    fireEvent.click(await screen.findByTestId('review-move-4'));
    fireEvent.click(screen.getByTestId('play-from-here'));
    await waitFor(() => expect(onPlay).toHaveBeenCalledWith(session, review.moves[3].fen));
    expect(ApiClient.createGame).toHaveBeenCalledWith(expect.objectContaining({ owner_color: 'white', pgn: `[FEN "${review.moves[3].fen}"]\n[SetUp "1"]\n\n` }));
    expect(ApiClient.createPlaySession).toHaveBeenCalledWith(expect.objectContaining({ game_id: 'new-game', opponent_config: { provider: 'stockfish', bot_level: '1' } }));
  });

  it('distinguishes not-analysed, pending and failed states without hiding the replay', async () => {
    vi.mocked(ApiClient.getGameAnalysisStatus).mockResolvedValueOnce({ status: 'not_analysed' }).mockResolvedValue({ status: 'failed' });
    vi.spyOn(ApiClient, 'createAnalysisJob').mockResolvedValue({ id: 'job-1', game_id: 'game-1', status: 'pending' });
    render(<AnalysisScreen gameId="game-1" />);
    expect(await screen.findByTestId('analysis-not-analysed')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('start-analysis'));
    expect(await screen.findByTestId('analysis-in-progress')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh review' }));
    expect(await screen.findByTestId('analysis-failed')).toBeInTheDocument();
    expect(screen.getByTestId('retry-analysis')).toBeInTheDocument();
    expect(screen.getByTestId('review-move-list')).toBeInTheDocument();
  });

  it.each([
    ['not_analysed', 'analysis-not-analysed'],
    ['pending', 'analysis-in-progress'],
    ['running', 'analysis-in-progress'],
    ['failed', 'analysis-failed'],
  ] as const)('keeps %s status visible when move-review loading fails', async (status, notice) => {
    vi.mocked(ApiClient.getGameReview).mockRejectedValue(new ApiRequestError('Review data is unavailable', 503));
    vi.mocked(ApiClient.getGameAnalysisStatus).mockResolvedValue({ status });
    render(<AnalysisScreen gameId="game-1" />);
    expect(await screen.findByTestId(notice)).toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent('Review data is unavailable');
    expect(screen.queryByTestId('review-board')).not.toBeInTheDocument();
    expect(screen.queryByTestId('review-move-list')).not.toBeInTheDocument();
  });

  it('reports not-found and ignores stale responses after changing games', async () => {
    const first = Promise.withResolvers<GameReviewRead>();
    vi.mocked(ApiClient.getGameReview).mockReturnValueOnce(first.promise);
    const view = render(<AnalysisScreen gameId="missing" />);
    view.rerender(<AnalysisScreen gameId="game-1" />);
    await screen.findByTestId('review-move-list');
    await act(async () => { first.reject(new ApiRequestError('Not Found', 404)); });
    expect(screen.queryByText('Game analysis was not found.')).not.toBeInTheDocument();
    view.unmount();
    vi.mocked(ApiClient.getGameReview).mockRejectedValue(new ApiRequestError('Not Found', 404));
    render(<AnalysisScreen gameId="missing" />);
    expect(await screen.findByText('Game analysis was not found.')).toBeInTheDocument();
  });
});
