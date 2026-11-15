import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Chess } from 'chess.js';
import { PuzzlesScreen } from './PuzzlesScreen';
import { ApiClient, PuzzleConflictError } from '../api/client';
import type { PuzzleDailyRead, PuzzleMoveExplanationRead, PuzzleRunRead, PuzzleStartRead, Visualization } from '../api/types';
import type * as ClientModule from '../api/client';
import { VisualizationDescriptions } from './BoardVisualizations';

import { reviewLesson } from './review.test-fixtures';
const board = vi.hoisted(() => ({ props: null as null | {
  fen: string; orientation: string; disabled: boolean; resetAfterAttempt: boolean;
  pgn?: string; selectedPly?: number;
  visualizations?: Visualization[];
  onMove: (move: string) => void | Promise<void>;
} }));
vi.mock('./CoachBoard', () => ({ CoachBoard: (props: NonNullable<typeof board.props> & { testId: string }) => {
  board.props = props;
  const game = new Chess(props.fen);
  if (props.pgn) game.loadPgn(props.pgn);
  const history = game.history({ verbose: true });
  const fen = props.selectedPly === undefined ? game.fen()
    : history[props.selectedPly - 1]?.after ?? history[0]?.before ?? props.fen;
  return <div data-testid={props.testId} data-fen={fen} data-orientation={props.orientation} data-disabled={String(props.disabled)}>
    <VisualizationDescriptions fen={fen} visualizations={props.visualizations ?? []} />
  </div>;
} }));
vi.mock('../api/client', async importOriginal => {
  const actual = await importOriginal<typeof ClientModule>();
  return { ...actual, ApiClient: { ...actual.ApiClient,
    startPuzzleRun: vi.fn(), getPuzzleRun: vi.fn(), submitPuzzleMove: vi.fn(),
    getActivePuzzleRun: vi.fn(),
    revealPuzzleHint: vi.fn(), abandonPuzzleRun: vi.fn(),
    getPuzzleModes: vi.fn(), getPuzzleMistakes: vi.fn(), startPuzzleMistake: vi.fn(),
    getPuzzleReview: vi.fn(), explainPuzzleMove: vi.fn(),
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
function afterMove(fen: string, move: string): string {
  const game = new Chess(fen);
  game.move(move);
  return game.fen();
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
  it('shows operator import instructions for an empty browser catalog', async () => {
    vi.mocked(ApiClient.getActivePuzzleRun).mockResolvedValue({
      status: 'empty', run: null, message: '', daily, streak,
    });
    render(<PuzzlesScreen />);
    expect(await screen.findByText(/uv run scan64-cli puzzles import/)).toBeVisible();
    expect(screen.queryByText(/Scan64 window/)).toBeNull();
    expect(screen.queryByRole('region', { name: 'Puzzle workspace' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Choose your practice' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start daily' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check for puzzles' })).toBeEnabled();
  });
  it('prevents checking an empty library with no eligible theme selected', async () => {
    vi.mocked(ApiClient.getPuzzleModes).mockResolvedValue({ themes: [], streak });
    vi.mocked(ApiClient.getActivePuzzleRun).mockResolvedValue({ status: 'empty', run: null, message: '', daily, streak });
    render(<PuzzlesScreen />);
    await screen.findByText('No imported puzzles are available.');
    fireEvent.click(screen.getByRole('radio', { name: 'Theme' }));
    expect(screen.getByRole('button', { name: 'Check for puzzles' })).toBeDisabled();
  });
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
    expect(screen.getByText('3 wrong attempts remaining')).toBeInTheDocument();
    const workspace = screen.getByRole('region', { name: 'Puzzle workspace' });
    expect(within(workspace).getByTestId('puzzle-board')).toBeVisible();
    expect(within(workspace).getByRole('region', { name: 'Current puzzle' })).toContainElement(screen.getByRole('button', { name: 'Show hint' }));
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
    expectBoard(afterMove(REPLY, 'a7a6'), true);
    expect(screen.getByRole('button', { name: 'Retry unscored' })).toBeEnabled();
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
    expect(screen.getByRole('heading', { name: 'Puzzle solved' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Next puzzle' })).toBeEnabled();
  });

  it('holds mistaken moves until retry and exhausts the three-attempt scored budget', async () => {
    for (let wrong = 1; wrong <= 3; wrong++) {
      vi.mocked(ApiClient.submitPuzzleMove).mockResolvedValueOnce(run({ wrong_attempts: wrong, revision: wrong, state: wrong === 3 ? 'failed' : 'active' }));
    }
    await mount();
    for (let wrong = 1; wrong <= 3; wrong++) {
      await move('a7a6');
      expect(ApiClient.submitPuzzleMove).toHaveBeenNthCalledWith(wrong, 'run-1', { expected_cursor: 0, expected_revision: wrong - 1, move: 'a7a6' });
      expectBoard(afterMove(SETUP, 'a7a6'), true);
      expect(screen.getByRole('region', { name: 'Move feedback' })).toHaveTextContent('a6');
      if (wrong < 3) {
        expect(screen.getByText(`${3 - wrong} wrong attempts remaining`)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expectBoard(SETUP);
      }
    }
    expect(screen.getByRole('button', { name: 'Retry unscored' })).toBeEnabled();
    vi.mocked(ApiClient.getPuzzleReview).mockResolvedValue({
      run_id: 'run-1', initial_fen: SETUP, solution: ['e7e5', 'g1f3', 'b8c6'],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expectBoard(SETUP));
    expect(ApiClient.submitPuzzleMove).toHaveBeenCalledTimes(3);
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

  it('keeps hint-three source annotations free of engine-quality claims', async () => {
    const visualizations = [{ command: 'draw_arrow', description: 'Source continuation: e5.', origin: 'e7', target: 'e5' }];
    await mount(run({ hint_step: 3, hints: [{
      step: 3, fen: SETUP,
      candidates: [{ number: 1, move: 'e7e5', san: 'e5', line: ['e7e5', 'g1f3'], reasons: [], fallback: false, visualizations }],
      visualizations,
    }] }));
    const annotations = screen.getByRole('list', { name: 'Board annotations' });
    expect(annotations).toHaveTextContent('Source continuation: e5.');
    expect(annotations).not.toHaveTextContent(/best|engine.quality/i);
    fireEvent.click(screen.getByText('Rating and practice rules'));
    expect(screen.getByText(/not an engine judgement of move quality/)).toBeVisible();
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
    await waitFor(() => expect(screen.getByRole('status', { name: 'Puzzle progress' })).toHaveTextContent(/puzzle abandoned/i));
    expectBoard(SETUP, true);
    expect(ApiClient.abandonPuzzleRun).toHaveBeenCalledWith('run-1', { expected_cursor: 0, expected_revision: 0 });
    expect(screen.getByRole('status', { name: 'Puzzle progress' })).not.toHaveTextContent(/puzzle solved|puzzle failed/i);
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
    expect(screen.queryByRole('region', { name: 'Puzzle workspace' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start daily' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check for puzzles' })).toBeEnabled();
    expect(ApiClient.submitPuzzleMove).not.toHaveBeenCalled();
  });
  it('shows selection limits and updates rating and progress from the terminal response', async () => {
    await mount();
    expect(screen.getByText(/1200 \(provisional\)/)).toBeInTheDocument();
    expect(screen.getByText(/0 of 10 completed.*4 overdue reviews/)).toBeInTheDocument();
    expect(screen.getByText(/Widened to ±400/)).not.toBeVisible();
    fireEvent.click(screen.getByText('Source and selection details'));
    expect(screen.getByText(/Widened to ±400/)).toBeVisible();
    expect(screen.getByText(/Related to recurring weaknesses/)).toBeVisible();
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
    expect(screen.queryByRole('region', { name: 'Puzzle workspace' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Choose your practice' })).not.toBeInTheDocument();
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

  it('retries the failed theme start instead of refreshing an older completed daily run', async () => {
    await mount(run({ state: 'solved' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Theme' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Theme' }), { target: { value: 'pin' } });
    vi.mocked(ApiClient.startPuzzleRun)
      .mockRejectedValueOnce(new Error('Theme request interrupted'))
      .mockResolvedValueOnce({ status: 'ready', run: run({ mode: 'theme', theme: 'pin', rated: false }), message: '', daily, streak });
    fireEvent.click(screen.getByRole('button', { name: 'Start theme' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Theme request interrupted');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByTestId('puzzle-board');
    expect(screen.getByRole('radio', { name: 'Theme' })).toBeChecked();
    expect(ApiClient.startPuzzleRun).toHaveBeenCalledTimes(2);
    expect(ApiClient.startPuzzleRun).toHaveBeenLastCalledWith({ mode: 'theme', theme: 'pin' });
    expect(ApiClient.getPuzzleRun).not.toHaveBeenCalled();
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
      .mockResolvedValueOnce({ items: [{ lesson_id: 'lesson-1', game_id: 'game-1', ply: 12, attempts_remaining: 2, diagnosis: 'threat_processing.missed_capture' }],
        excluded, offset: 20, limit: 20, has_more: false });
    await mount(run({ state: 'solved' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Your mistakes' }));
    expect(await screen.findByText('No eligible mistakes available.')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Puzzle workspace' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'More mistakes' }));
    expect(ApiClient.getPuzzleMistakes).toHaveBeenLastCalledWith(20);
    expect(await screen.findByText(/2 attempts remaining/)).toBeInTheDocument();
    expect(screen.getAllByRole('link').map(link => link.getAttribute('href'))).toContain('/games/game-1/analysis?ply=12');
    expect(screen.getByRole('button', { name: 'More mistakes' })).toBeDisabled();
  });
  it('opens an owned retry with its server session and lifetime lesson budget', async () => {
    vi.mocked(ApiClient.getPuzzleMistakes).mockResolvedValue({
      items: [{ lesson_id: reviewLesson.lesson_id, game_id: 'game-1', ply: 3, attempts_remaining: 2, diagnosis: 'threat_processing.missed_capture' }],
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
    await waitFor(() => expect(panel).toHaveFocus());
    fireEvent.keyDown(panel, { key: 'Escape' });
    expect(screen.getByRole('button', { name: 'Start mistake at ply 3' })).toHaveFocus();
    expect(panel).not.toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Start mistake at ply 3' }));
    await waitFor(() => expect(panel).toBeVisible());
    await waitFor(() => expect(panel).toHaveFocus());
    expect(screen.getByText(/Hints are app-reported/)).toBeInTheDocument();
  });
});

describe('pattern practice routes', () => {
  afterEach(() => window.history.replaceState(null, '', '/puzzles'));

  it('returns keyboard focus to Your mistakes after closing a pattern-linked retry', async () => {
    window.history.replaceState(null, '', `/puzzles?lesson=${reviewLesson.lesson_id}&game=game-1`);
    vi.mocked(ApiClient.getActivePuzzleRun).mockResolvedValue({ status: 'ready', run: null, message: '', daily, streak });
    vi.mocked(ApiClient.startPuzzleMistake).mockResolvedValue({
      moment: { lesson_id: reviewLesson.lesson_id, lesson: reviewLesson, ply: 3, attempts_used: 0, attempts_remaining: 3 },
      session_id: 'owned-study',
    });
    render(<PuzzlesScreen />);
    const retry = await screen.findByRole('region', { name: 'Retry mistake at ply 3' });
    await waitFor(() => expect(retry).toHaveFocus());
    fireEvent.keyDown(retry, { key: 'Escape' });
    expect(screen.getByRole('heading', { name: 'Your mistakes' })).toHaveFocus();
  });

  it('labels related theme practice after a retry admission race and clears it for unrelated runs', async () => {
    window.history.replaceState(null, '', '/puzzles?lesson=spent&game=game-1&fallbackTheme=fork');
    vi.mocked(ApiClient.getActivePuzzleRun).mockResolvedValue({ status: 'ready', run: null, message: '', daily, streak });
    vi.mocked(ApiClient.startPuzzleMistake).mockRejectedValue(new Error('Retry budget exhausted'));
    vi.mocked(ApiClient.startPuzzleRun)
      .mockResolvedValueOnce({ status: 'ready', run: run({ mode: 'theme', theme: 'fork', rated: false, state: 'solved' }), message: '', daily, streak })
      .mockResolvedValueOnce({ status: 'ready', run: run({ mode: 'daily', theme: null }), message: '', daily, streak });
    render(<PuzzlesScreen />);
    expect(await screen.findByText('Related unrated theme practice, not diagnosis mastery.')).toBeInTheDocument();
    expect(ApiClient.startPuzzleRun).toHaveBeenCalledWith({ mode: 'theme', theme: 'fork' });
    fireEvent.click(screen.getByRole('radio', { name: 'Daily' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start daily' }));
    await waitFor(() => expect(screen.queryByText('Related unrated theme practice, not diagnosis mastery.')).not.toBeInTheDocument());
  });

  it('offers the unrelated Daily link when the related theme request fails', async () => {
    window.history.replaceState(null, '', '/puzzles?theme=fork');
    vi.mocked(ApiClient.getActivePuzzleRun).mockResolvedValue({ status: 'ready', run: null, message: '', daily, streak });
    vi.mocked(ApiClient.startPuzzleRun).mockRejectedValue(new Error('Practice unavailable'));
    render(<PuzzlesScreen />);
    expect(await screen.findByRole('link', { name: 'Daily puzzles — may be unrelated' })).toHaveAttribute('href', '/puzzles');
  });

  it('starts the mapped unrated theme through the existing run API', async () => {
    window.history.replaceState(null, '', '/puzzles?theme=fork');
    vi.mocked(ApiClient.getActivePuzzleRun).mockResolvedValue({ status: 'ready', run: null, message: '', daily, streak });
    vi.mocked(ApiClient.startPuzzleRun).mockResolvedValue({ status: 'ready', run: run({ mode: 'theme', theme: 'fork', rated: false }), message: '', daily, streak });
    render(<PuzzlesScreen />);
    await waitFor(() => expect(ApiClient.startPuzzleRun).toHaveBeenCalledWith({ mode: 'theme', theme: 'fork' }));
    expect(await screen.findByText('Related unrated theme practice, not diagnosis mastery.')).toBeInTheDocument();
  });

  it('never replaces an active run with pattern practice', async () => {
    window.history.replaceState(null, '', '/puzzles?theme=fork');
    await mount();
    expect(ApiClient.startPuzzleRun).not.toHaveBeenCalled();
    expect(screen.getByText('Finish or abandon the active puzzle before switching modes.')).toBeInTheDocument();
  });

  it('offers plain Daily without starting it when mapped theme supply is unavailable', async () => {
    window.history.replaceState(null, '', '/puzzles?theme=fork');
    vi.mocked(ApiClient.getActivePuzzleRun).mockResolvedValue({ status: 'ready', run: null, message: '', daily, streak });
    vi.mocked(ApiClient.startPuzzleRun).mockResolvedValue({ status: 'ready', run: null, message: 'No related puzzles available.', daily, streak });
    render(<PuzzlesScreen />);
    expect(await screen.findByRole('link', { name: 'Daily puzzles — may be unrelated' })).toHaveAttribute('href', '/puzzles');
    expect(ApiClient.startPuzzleRun).toHaveBeenCalledTimes(1);
    expect(ApiClient.startPuzzleRun).toHaveBeenCalledWith({ mode: 'theme', theme: 'fork' });
  });
});

describe('interactive puzzle review', () => {
  it('allows unlimited terminal retries without submitting another graded move', async () => {
    await mount(run({ state: 'failed', wrong_attempts: 3, revision: 3 }));
    vi.mocked(ApiClient.getPuzzleReview).mockResolvedValue({
      run_id: 'run-1', initial_fen: SETUP, solution: ['e7e5', 'g1f3', 'b8c6'],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Retry unscored' }));
    await waitFor(() => expectBoard(SETUP));
    for (let retry = 0; retry < 4; retry++) {
      await move('a7a6');
      expectBoard(afterMove(SETUP, 'a7a6'), true);
      fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
      expectBoard(SETUP);
    }
    await move('e7e5');
    expectBoard(REPLY);
    await move('b8c6');
    expectBoard(FINISH, true);
    expect(screen.getByRole('heading', { name: 'Unscored practice complete' })).toBeVisible();
    expect(ApiClient.submitPuzzleMove).not.toHaveBeenCalled();
    expect(ApiClient.startPuzzleRun).not.toHaveBeenCalled();
  });

  it('discloses assistance and replays the legal reply without calling a good alternative bad', async () => {
    await mount();
    vi.mocked(ApiClient.submitPuzzleMove).mockResolvedValue(run({ wrong_attempts: 1, revision: 1 }));
    await move('a7a6');
    const attempted = afterMove(SETUP, 'a7a6');
    expect(screen.getByText(/counts as assistance/)).toBeVisible();
    vi.mocked(ApiClient.explainPuzzleMove).mockResolvedValue({
      run: run({ wrong_attempts: 1, revision: 2, hint_step: 3 }), move: 'a7a6',
      fen_before: SETUP, fen_after: attempted, verdict: 'alternative',
      summary: 'The search does not establish a mistake.', reply_line: ['g1f3'],
      visualizations: [{ command: 'draw_arrow', origin: 'g1', target: 'f3', description: 'Opponent reply: Nf3.' }],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Explain this move' }));
    expect(await screen.findByRole('heading', { name: 'Playable alternative' })).toBeVisible();
    expectBoard(attempted, true);
    expect(screen.getByRole('list', { name: 'Board annotations' })).toHaveTextContent('Opponent reply');
    fireEvent.click(screen.getByRole('button', { name: 'Next move' }));
    expectBoard(afterMove(attempted, 'g1f3'), true);
    expect(screen.queryByRole('list', { name: 'Board annotations' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expectBoard(SETUP);
    vi.mocked(ApiClient.submitPuzzleMove).mockResolvedValue(run({ fen: REPLY, cursor: 2, revision: 3, played_moves: ['e7e5', 'g1f3'] }));
    await move();
    expect(ApiClient.submitPuzzleMove).toHaveBeenLastCalledWith('run-1', { expected_cursor: 0, expected_revision: 2, move: 'e7e5' });
  });

  it('discards an explanation that finishes after the user starts the next puzzle', async () => {
    const ended = run({ state: 'failed', wrong_attempts: 3, revision: 3 });
    await mount(ended);
    vi.mocked(ApiClient.getPuzzleReview).mockResolvedValue({
      run_id: 'run-1', initial_fen: SETUP, solution: ['e7e5', 'g1f3', 'b8c6'],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Retry unscored' }));
    await waitFor(() => expectBoard(SETUP));
    await move('a7a6');
    const response = deferred<PuzzleMoveExplanationRead>();
    vi.mocked(ApiClient.explainPuzzleMove).mockReturnValue(response.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Explain this move' }));
    vi.mocked(ApiClient.startPuzzleRun).mockResolvedValue({ status: 'ready', run: run({ run_id: 'run-2' }), message: '', daily, streak });
    fireEvent.click(screen.getByRole('button', { name: 'Next puzzle' }));
    await waitFor(() => expectBoard(SETUP));
    await act(async () => response.resolve({
      run: ended, move: 'a7a6', fen_before: SETUP, fen_after: afterMove(SETUP, 'a7a6'),
      verdict: 'alternative', summary: 'Old explanation.', reply_line: ['g1f3'], visualizations: [],
    }));
    expect(screen.queryByText('Old explanation.')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Move feedback' })).not.toBeInTheDocument();
    expectBoard(SETUP);
  });
});
