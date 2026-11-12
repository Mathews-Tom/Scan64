import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PuzzlesScreen } from './PuzzlesScreen';
import { ApiClient, PuzzleConflictError } from '../api/client';
import type { PuzzleDailyRead, PuzzleRunRead, PuzzleStartRead } from '../api/types';
import type * as ClientModule from '../api/client';

import { reviewLesson } from './review.test-fixtures';
const board = vi.hoisted(() => ({ props: null as null | {
  fen: string; orientation: string; disabled: boolean; resetAfterAttempt: boolean;
  onMove: (move: string) => void | Promise<void>;
} }));
vi.mock('./CoachBoard', () => ({ CoachBoard: (props: NonNullable<typeof board.props> & { testId: string }) => {
  board.props = props;
  return <div data-testid={props.testId} data-fen={props.fen} data-orientation={props.orientation} data-disabled={String(props.disabled)} />;
} }));
vi.mock('../api/client', async importOriginal => {
  const actual = await importOriginal<typeof ClientModule>();
  return { ...actual, ApiClient: { ...actual.ApiClient,
    startPuzzleRun: vi.fn(), getPuzzleRun: vi.fn(), submitPuzzleMove: vi.fn(),
    getActivePuzzleRun: vi.fn(),
    revealPuzzleHint: vi.fn(), abandonPuzzleRun: vi.fn(),
    getPuzzleModes: vi.fn(), getPuzzleMistakes: vi.fn(), startPuzzleMistake: vi.fn(),
  } };
});

// This is the post-setup position (1. e4). No future continuation is sent to the client.
const SETUP = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1';
const REPLY = 'rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2';
const FINISH = 'r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3';
const streak = { current: 0, best: 0, reset_reason: null };
const daily: PuzzleDailyRead = {
  utc_day: '2026-11-13', rating: 1200, rated_count: 0, provisional: true,
  overdue_count: 4, total: 10, completed: 0, remaining: 10,
  actual_mix: { due: 4, weak: 5, exploration: 1 }, message: 'Your stable UTC daily set.',
};
function run(overrides: Partial<PuzzleRunRead> = {}): PuzzleRunRead {
  return { run_id: 'run-1', puzzle_id: 'puzzle-1', fen: SETUP, setup: 'e2e4', played_moves: [],
    cursor: 0, revision: 0, wrong_attempts: 0, maximum_wrong_attempts: 3,
    state: 'active', hint_step: 0, hints: [], selection_reason: 'weak', widening: '400', rated: true, daily, mode: 'daily', theme: null, streak, ...overrides };
}
function deferred<T>() {
  return Promise.withResolvers<T>();
}
async function mount(value = run()) {
  vi.mocked(ApiClient.getActivePuzzleRun).mockResolvedValue({ status: 'ready', run: value, message: '', daily: value.daily, streak });
  const view = render(<PuzzlesScreen />);
  await screen.findByTestId('puzzle-board');
  return view;
}
async function move(uci = 'e7e5') {
  await act(async () => { await board.props!.onMove(uci); });
}
function expectBoard(fen: string, disabled = false) {
  expect(screen.getByTestId('puzzle-board')).toHaveAttribute('data-fen', fen);
  expect(board.props?.disabled).toBe(disabled);
}
beforeEach(() => {
  vi.resetAllMocks(); board.props = null;
  vi.mocked(ApiClient.getPuzzleModes).mockResolvedValue({ themes: ['fork', 'pin'], streak });
});
afterEach(cleanup);

describe('PuzzlesScreen server-authoritative practice', () => {
  it('clears loading speech when initial puzzle loading fails', async () => {
    vi.mocked(ApiClient.getActivePuzzleRun).mockRejectedValue(new Error('Puzzle service unavailable'));
    render(<PuzzlesScreen />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Puzzle service unavailable');
    expect(screen.getByRole('status', { name: 'Puzzle progress' })).not.toHaveTextContent('Loading');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled();
  });
  it('resumes the post-setup position with solver orientation and no leaked future line', async () => {
    await mount();
    expect(ApiClient.getActivePuzzleRun).toHaveBeenCalledWith();
    expect(ApiClient.startPuzzleRun).not.toHaveBeenCalled();
    expectBoard(SETUP);
    expect(board.props?.orientation).toBe('black');
    expect(board.props?.resetAfterAttempt).toBe(true);
    expect(screen.getByText('3 wrong attempts remaining')).toBeInTheDocument();
    expect(screen.queryByText(/^(rating gained|mastery improved)/i)).not.toBeInTheDocument();
  });

  it('does not reset the total wrong budget when a correct move advances the turn', async () => {
    await mount(run({ wrong_attempts: 2, revision: 4 }));
    vi.mocked(ApiClient.submitPuzzleMove).mockResolvedValueOnce(run({
      fen: REPLY, cursor: 2, revision: 5, wrong_attempts: 2, played_moves: ['e7e5', 'g1f3'],
    })).mockResolvedValueOnce(run({
      fen: REPLY, cursor: 2, revision: 6, wrong_attempts: 3, played_moves: ['e7e5', 'g1f3'], state: 'failed',
    }));
    await move();
    expect(screen.getByText('1 wrong attempts remaining')).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Puzzle progress' }))
      .toHaveTextContent('Black played e7 to e5. White played g1 to f3.');
    await move('a7a6');
    expectBoard(REPLY, true);
    expect(screen.getByText(/puzzle failed/i)).toBeInTheDocument();
    expect(ApiClient.submitPuzzleMove).toHaveBeenLastCalledWith('run-1', {
      expected_cursor: 2, expected_revision: 5, move: 'a7a6',
    });
  });

  it.each(['hint', 'abandon'] as const)('serializes %s requests and reconciles their conflicts without retrying', async action => {
    const pending = deferred<PuzzleRunRead>();
    const method = action === 'hint' ? ApiClient.revealPuzzleHint : ApiClient.abandonPuzzleRun;
    vi.mocked(method).mockReturnValueOnce(pending.promise);
    await mount();
    const label = action === 'hint' ? 'Show hint' : 'Abandon puzzle';
    fireEvent.click(screen.getByRole('button', { name: label }));
    expectBoard(SETUP, true);
    expect(screen.getByRole('button', { name: 'Show hint' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Abandon puzzle' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: label }));
    expect(method).toHaveBeenCalledTimes(1);
    const current = run({ revision: 9, wrong_attempts: 1 });
    await act(async () => pending.reject(new PuzzleConflictError('Server state changed', current)));
    await waitFor(() => expectBoard(SETUP));
    expect(method).toHaveBeenCalledTimes(1);
    expect(screen.getByText('2 wrong attempts remaining')).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Puzzle progress' })).toHaveTextContent(/Server state changed/);
    expect(ApiClient.submitPuzzleMove).not.toHaveBeenCalled();
  });

  it('renders the automatic opponent reply and uses the returned cursor/revision for the next move', async () => {
    vi.mocked(ApiClient.submitPuzzleMove).mockResolvedValueOnce(run({ fen: REPLY, cursor: 2, revision: 1, played_moves: ['e7e5', 'g1f3'] }))
      .mockResolvedValueOnce(run({ fen: FINISH, cursor: 3, revision: 2, state: 'solved', played_moves: ['e7e5', 'g1f3', 'b8c6'] }));
    await mount();
    await move();
    expectBoard(REPLY);
    expect(board.props?.orientation).toBe('black');
    await move('b8c6');
    expect(ApiClient.submitPuzzleMove).toHaveBeenNthCalledWith(2, 'run-1', { expected_cursor: 2, expected_revision: 1, move: 'b8c6' });
    expectBoard(FINISH, true);
    expect(board.props?.orientation).toBe('black');
    expect(screen.getByText(/solved/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next puzzle' })).toBeEnabled();
  });

  it('preserves board and cursor for wrong moves and fails after three total wrong submissions', async () => {
    for (let wrong = 1; wrong <= 3; wrong++) {
      vi.mocked(ApiClient.submitPuzzleMove).mockResolvedValueOnce(run({ wrong_attempts: wrong, revision: wrong, state: wrong === 3 ? 'failed' : 'active' }));
    }
    await mount();
    for (let wrong = 1; wrong <= 3; wrong++) {
      await move('a7a6');
      expect(ApiClient.submitPuzzleMove).toHaveBeenNthCalledWith(wrong, 'run-1', { expected_cursor: 0, expected_revision: wrong - 1, move: 'a7a6' });
      expectBoard(SETUP, wrong === 3);
      if (wrong < 3) expect(screen.getByText(`${3 - wrong} wrong attempts remaining`)).toBeInTheDocument();
    }
    expect(screen.getByText(/failed/i)).toBeInTheDocument();
    vi.mocked(ApiClient.startPuzzleRun).mockResolvedValue({ status: 'ready', run: run({ run_id: 'run-2' }), message: '', daily, streak });
    fireEvent.click(screen.getByRole('button', { name: 'Next puzzle' }));
    await waitFor(() => expect(ApiClient.startPuzzleRun).toHaveBeenCalledTimes(1));
    await waitFor(() => expectBoard(SETUP));
  });

  it('reveals persisted hints in order, retains earlier hints, and resets hint level on the next turn', async () => {
    const first = { step: 1 as const, text: 'Inspect the central pawns.', visualizations: [] };
    const second = { step: 2 as const, text: 'Consider a pawn advance.', motif: 'center', piece: 'pawn', square: 'e7', targets: ['e5'], visualizations: [] };
    const third = { step: 3 as const, fen: SETUP, candidates: [{ number: 1, move: 'e7e5', san: 'e5', line: ['e7e5', 'g1f3'], reasons: [], fallback: false, visualizations: [] }], visualizations: [] };
    const hints = [first, second, third];
    for (let step = 1; step <= 3; step++) vi.mocked(ApiClient.revealPuzzleHint).mockResolvedValueOnce(run({ hint_step: step, revision: step, hints: hints.slice(0, step) }));
    const view = await mount();
    for (let step = 1; step <= 3; step++) {
      fireEvent.click(screen.getByRole('button', { name: 'Show hint' }));
      await waitFor(() => expect(ApiClient.revealPuzzleHint).toHaveBeenNthCalledWith(step, 'run-1', { expected_cursor: 0, expected_revision: step - 1, step }));
      await waitFor(() => expect(screen.getByText(first.text)).toBeInTheDocument());
      await waitFor(() => expect(board.props?.disabled).toBe(false));
    }
    expect(screen.getByText(second.text)).toBeInTheDocument();
    const hintButton = screen.queryByRole('button', { name: 'Show hint' });
    if (hintButton) expect(hintButton).toBeDisabled();
    view.unmount();
    await mount(run({ hint_step: 3, revision: 3, hints }));
    expect(screen.getByText(first.text)).toBeInTheDocument();
    expect(screen.getByText(second.text)).toBeInTheDocument();
    vi.mocked(ApiClient.submitPuzzleMove).mockResolvedValue(run({ fen: REPLY, cursor: 2, revision: 4, played_moves: ['e7e5', 'g1f3'] }));
    await move();
    expect(screen.queryByText(first.text)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show hint' })).toBeEnabled();
  });

  it('disables Next puzzle while requesting another run and does not double-start', async () => {
    await mount(run({ state: 'failed', wrong_attempts: 3 }));
    const pending = deferred<PuzzleStartRead>();
    vi.mocked(ApiClient.startPuzzleRun).mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Next puzzle' }));
    expect(screen.getByRole('button', { name: 'Next puzzle' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Next puzzle' }));
    expect(ApiClient.startPuzzleRun).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve({ status: 'ready', run: run({ run_id: 'run-2' }), message: '', daily, streak }));
    expectBoard(SETUP);
    expect(screen.getByText('3 wrong attempts remaining')).toBeInTheDocument();
  });

  it('abandons without reporting a solved puzzle or recording a fabricated success', async () => {
    vi.mocked(ApiClient.abandonPuzzleRun).mockResolvedValue(run({ state: 'abandoned', revision: 1 }));
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Abandon puzzle' }));
    await screen.findByText(/puzzle abandoned/i);
    expectBoard(SETUP, true);
    expect(ApiClient.abandonPuzzleRun).toHaveBeenCalledWith('run-1', { expected_cursor: 0, expected_revision: 0 });
    expect(screen.getByText(/abandoned/i)).toBeInTheDocument();
    expect(screen.queryByText(/puzzle solved|puzzle failed/i)).not.toBeInTheDocument();
    expect(ApiClient.submitPuzzleMove).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Next puzzle' })).toBeEnabled();
  });

  it('reconciles a 409 without replaying the mutation', async () => {
    const current = run({ fen: REPLY, cursor: 2, revision: 7, played_moves: ['e7e5', 'g1f3'] });
    vi.mocked(ApiClient.submitPuzzleMove).mockRejectedValueOnce(new PuzzleConflictError('Run changed in another tab', current));
    await mount();
    await move();
    expectBoard(REPLY);
    expect(ApiClient.submitPuzzleMove).toHaveBeenCalledTimes(1);
    vi.mocked(ApiClient.revealPuzzleHint).mockResolvedValue({ ...current, revision: 8, hint_step: 1, hints: [{ step: 1, text: 'Look at development.', visualizations: [] }] });
    fireEvent.click(screen.getByRole('button', { name: 'Show hint' }));
    await waitFor(() => expect(ApiClient.revealPuzzleHint).toHaveBeenCalledWith('run-1', { expected_cursor: 2, expected_revision: 7, step: 1 }));
  });

  it('blocks duplicate moves and other mutations while pending, then recovers without counting transport failure', async () => {
    const pending = deferred<PuzzleRunRead>();
    vi.mocked(ApiClient.submitPuzzleMove).mockReturnValueOnce(pending.promise);
    await mount();
    let submission!: void | Promise<void>;
    act(() => { submission = board.props!.onMove('e7e5'); });
    expectBoard(SETUP, true);
    expect(screen.getByRole('button', { name: 'Show hint' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Abandon puzzle' })).toBeDisabled();
    await act(async () => { await board.props!.onMove('a7a6'); });
    expect(ApiClient.submitPuzzleMove).toHaveBeenCalledTimes(1);
    await act(async () => { pending.reject(new Error('Connection interrupted')); await submission; });
    expectBoard(SETUP, true);
    expect(screen.getByText('3 wrong attempts remaining')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/Connection interrupted|retry|try again/i);
    vi.mocked(ApiClient.getPuzzleRun).mockResolvedValue(run());
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expectBoard(SETUP));
    expect(ApiClient.getPuzzleRun).toHaveBeenCalledWith('run-1');
    expect(ApiClient.submitPuzzleMove).toHaveBeenCalledTimes(1);
    vi.mocked(ApiClient.submitPuzzleMove).mockResolvedValue(run({ wrong_attempts: 1, revision: 1 }));
    await move('a7a6');
    expect(ApiClient.submitPuzzleMove).toHaveBeenLastCalledWith('run-1', { expected_cursor: 0, expected_revision: 0, move: 'a7a6' });
  });

  it('disables initial loading, exposes retry on start failure, and resumes server state on remount', async () => {
    const pending = deferred<PuzzleStartRead>();
    vi.mocked(ApiClient.getActivePuzzleRun).mockReturnValueOnce(pending.promise);
    const view = render(<PuzzlesScreen />);
    expect(screen.queryByTestId('puzzle-board')).not.toBeInTheDocument();
    for (const button of screen.queryAllByRole('button')) expect(button).toBeDisabled();
    await act(async () => pending.reject(new Error('Cannot start puzzle')));
    expect(await screen.findByRole('alert')).toHaveTextContent(/Cannot start puzzle|retry|try again/i);
    vi.mocked(ApiClient.getActivePuzzleRun).mockResolvedValue({ status: 'ready', run: run({ fen: REPLY, cursor: 2, revision: 5, wrong_attempts: 1, played_moves: ['e7e5', 'g1f3'] }), message: '', daily, streak });
    fireEvent.click(screen.getByRole('button', { name: /retry|try again/i }));
    await screen.findByTestId('puzzle-board');
    expectBoard(REPLY);
    view.unmount();
    render(<PuzzlesScreen />);
    await screen.findByTestId('puzzle-board');
    expectBoard(REPLY);
    expect(screen.getByText('2 wrong attempts remaining')).toBeInTheDocument();
    expect(ApiClient.submitPuzzleMove).not.toHaveBeenCalled();
  });

  it.each(['empty', 'exhausted'] as const)('shows server %s guidance without starting a board', async status => {
    vi.mocked(ApiClient.getActivePuzzleRun).mockResolvedValue({ status, run: null, message: `Server says ${status}: import another puzzle set.`, daily, streak });
    render(<PuzzlesScreen />);
    expect(await screen.findByText(`Server says ${status}: import another puzzle set.`)).toBeInTheDocument();
    if (status === 'empty') expect(screen.getByText(/scan64.*puzzles.*import/)).toBeInTheDocument();
    else expect(screen.getByText(/next daily set resets at UTC midnight/i)).toBeInTheDocument();
    expect(screen.queryByTestId('puzzle-board')).not.toBeInTheDocument();
    expect(ApiClient.submitPuzzleMove).not.toHaveBeenCalled();
  });
  it('shows selection limits and updates rating and progress from the terminal response', async () => {
    await mount();
    expect(screen.getByText(/1200 \(provisional\)/)).toBeInTheDocument();
    expect(screen.getByText(/0 of 10 completed.*4 overdue reviews/)).toBeInTheDocument();
    expect(screen.getByText(/Widened to ±400/)).toBeInTheDocument();
    expect(screen.getByText(/Related to recurring weaknesses/)).toBeInTheDocument();
    expect(screen.getByText(/hints, failure or abandonment lose rating/)).toBeInTheDocument();
    vi.mocked(ApiClient.submitPuzzleMove).mockResolvedValue(run({
      state: 'solved', revision: 1,
      daily: { ...daily, rating: 1216, rated_count: 1, completed: 1, remaining: 9 },
    }));
    await move();
    expect(screen.getByText(/1216 \(provisional\)/)).toBeInTheDocument();
    expect(screen.getByText(/1 of 10 completed.*9 remaining/)).toBeInTheDocument();
    expect(screen.queryByText(/Source line \(UCI\)/)).not.toBeInTheDocument();
  });
  it('resumes the server-bound mode and prevents switching before abandonment', async () => {
    await mount(run({ mode: 'theme', theme: 'fork', rated: false }));
    expect(screen.getByRole('radio', { name: 'Theme' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Daily' })).toBeDisabled();
    expect(screen.getByRole('combobox', { name: 'Theme' })).toHaveValue('fork');
    expect(screen.getByText('Unrated mode practice')).toBeInTheDocument();
    expect(ApiClient.getActivePuzzleRun).toHaveBeenCalledExactlyOnceWith();
    expect(ApiClient.startPuzzleRun).not.toHaveBeenCalled();
  });

  it('offers all modes without starting a rated Daily run when unclosed slots remain', async () => {
    vi.mocked(ApiClient.getActivePuzzleRun).mockResolvedValue({
      status: 'idle', run: null, message: 'Choose a practice mode, then Start.', daily, streak,
    });
    render(<PuzzlesScreen />);
    const themeRadio = await screen.findByRole('radio', { name: 'Theme' });
    await waitFor(() => expect(themeRadio).toBeEnabled());
    expect(ApiClient.startPuzzleRun).not.toHaveBeenCalled();
    expect(screen.getByRole('radio', { name: 'Solve streak' })).toBeEnabled();
    expect(screen.getByRole('radio', { name: 'Your mistakes' })).toBeEnabled();
    expect(screen.queryByText(/No more puzzles are available/)).not.toBeInTheDocument();
    fireEvent.click(themeRadio);
    vi.mocked(ApiClient.startPuzzleRun).mockResolvedValue({
      status: 'ready', run: run({ mode: 'theme', theme: 'fork', rated: false }), message: '', daily, streak,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Start theme' }));
    await screen.findByTestId('puzzle-board');
    expect(ApiClient.startPuzzleRun).toHaveBeenCalledExactlyOnceWith({ mode: 'theme', theme: 'fork' });
  });

  it('starts an explicitly selected metadata theme and renders mode exhaustion', async () => {
    await mount(run({ state: 'solved' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Theme' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Theme' }), { target: { value: 'pin' } });
    vi.mocked(ApiClient.startPuzzleRun).mockResolvedValueOnce({ status: 'exhausted', run: null, message: 'No unseen pin puzzles.', daily, streak });
    fireEvent.click(screen.getByRole('button', { name: 'Start theme' }));
    expect(await screen.findByText(/This practice mode is exhausted/)).toBeInTheDocument();
    expect(ApiClient.startPuzzleRun).toHaveBeenLastCalledWith({ mode: 'theme', theme: 'pin' });
    expect(screen.queryByTestId('puzzle-board')).not.toBeInTheDocument();
  });

  it('shows authoritative terminal streak progress and reset reason', async () => {
    await mount(run({ mode: 'streak', rated: false }));
    vi.mocked(ApiClient.abandonPuzzleRun).mockResolvedValueOnce(run({ mode: 'streak', rated: false, state: 'abandoned',
      streak: { current: 0, best: 4, reset_reason: 'abandoned' } }));
    fireEvent.click(screen.getByRole('button', { name: 'Abandon puzzle' }));
    expect(await screen.findByText('Reset reason: abandoned')).toBeInTheDocument();
    expect(screen.getByText('Current: 0 · Best: 4')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Your mistakes' })).toBeEnabled();
  });

  it('paginates sparse owned mistake pages and links game and ply without hidden lessons', async () => {
    const excluded = { no_review_schedule: 1, retired: 2, untrusted: 3, exhausted: 4 };
    vi.mocked(ApiClient.getPuzzleMistakes).mockResolvedValueOnce({ items: [], excluded, offset: 0, limit: 20, has_more: true })
      .mockResolvedValueOnce({ items: [{ lesson_id: 'lesson-1', game_id: 'game-1', ply: 12, attempts_remaining: 2 }],
        excluded, offset: 20, limit: 20, has_more: false });
    await mount(run({ state: 'solved' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Your mistakes' }));
    expect(await screen.findByText('No eligible mistakes available.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'More mistakes' }));
    expect(await screen.findByRole('link', { name: 'Game game-1, ply 12' })).toHaveAttribute('href', '/games/game-1/analysis?ply=12');
    expect(ApiClient.getPuzzleMistakes).toHaveBeenLastCalledWith(20);
    expect(screen.getByText(/2 attempts remaining/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'More mistakes' })).toBeDisabled();
  });
  it('opens an owned retry with its server session and lifetime lesson budget', async () => {
    vi.mocked(ApiClient.getPuzzleMistakes).mockResolvedValue({
      items: [{ lesson_id: reviewLesson.lesson_id, game_id: 'game-1', ply: 3, attempts_remaining: 2 }],
      excluded: { no_review_schedule: 0, retired: 0, untrusted: 0, exhausted: 0 },
      offset: 0, limit: 20, has_more: false,
    });
    vi.mocked(ApiClient.startPuzzleMistake).mockResolvedValue({
      moment: { lesson_id: reviewLesson.lesson_id, lesson: reviewLesson, ply: 3, attempts_used: 1, attempts_remaining: 2 },
      session_id: 'owned-study',
    });
    await mount(run({ state: 'solved' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Your mistakes' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Start mistake at ply 3' }));
    expect(await screen.findByTestId('critical-moment-review')).toHaveTextContent('1 lifetime attempts used; 2 remaining.');
    expect(ApiClient.startPuzzleMistake).toHaveBeenCalledExactlyOnceWith(reviewLesson.lesson_id);
    const panel = screen.getByRole('region', { name: 'Retry mistake at ply 3' });
    expect(panel).toHaveFocus();
    fireEvent.keyDown(panel, { key: 'Escape' });
    expect(screen.getByRole('button', { name: 'Start mistake at ply 3' })).toHaveFocus();
    expect(panel).not.toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Start mistake at ply 3' }));
    await waitFor(() => expect(panel).toBeVisible());
    expect(panel).toHaveFocus();
    expect(screen.getByText(/Hints are app-reported/)).toBeInTheDocument();
  });
});
