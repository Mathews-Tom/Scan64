import { act, render, screen, waitFor, fireEvent } from '@testing-library/react';
import { vi, describe, beforeEach, afterEach, it, expect } from 'vitest';
import { DailyTrainingScreen } from './DailyTrainingScreen';
import { ApiClient, LessonAttemptLimitError } from '../api/client';
import type * as ApiModule from '../api/client';
import type { LessonAttemptRead, LessonSpec, TrainingSessionRead } from '../api/types';

vi.mock('../api/client', async importOriginal => ({
  ...(await importOriginal<typeof ApiModule>()),
  ApiClient: {
    getTrainingSession: vi.fn(),
    recordLessonAttempt: vi.fn(),
  }
}));

vi.mock('./CoachBoard', () => ({
  CoachBoard: ({
    disabled,
    onMove,
    visualizations,
    orientation,
  }: {
    disabled: boolean;
    onMove: (move: string) => void;
    visualizations?: unknown[];
    orientation?: 'white' | 'black';
  }) => (
    <button
      data-testid="submit-daily-move"
      data-visualizations={JSON.stringify(visualizations ?? [])}
      data-orientation={orientation ?? 'white'}
      disabled={disabled}
      onClick={() => onMove('e2e4')}
    >
      Submit move
    </button>
  ),
}));

const mockLesson: LessonSpec = {
  schema_version: '0.2.0',
  lesson_id: 'lesson-1',
  source: {
    kind: 'player_game',
    fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
  },
  diagnosis: {
    primary: 'tactics.fork',
    secondary: [],
    confidence: 0.9,
    evidence_refs: []
  },
  objective: {
    type: 'find_best_move',
    instruction: 'Find the winning tactic.'
  },
  interaction: { input: 'move', maximum_attempts: 3, accepted_moves: [] },
  hints: [],
  explanation: {} as unknown as LessonSpec['explanation'],
  verification: {} as unknown as LessonSpec['verification'],
  mastery: {} as unknown as LessonSpec['mastery']
};

describe('DailyTrainingScreen', () => {
  beforeEach(() => {
    const mockSession = [
      mockLesson,
      {
        ...mockLesson,
        lesson_id: 'lesson-2',
        objective: { type: 'find_best_move', instruction: 'Find the next move.' }
      }
    ];
    vi.spyOn(ApiClient, 'getTrainingSession').mockResolvedValue({
      session_id: 'session-1',
      move_choices: {},
      attempts_used: { 'lesson-1': 0, 'lesson-2': 0 },
      lessons: mockSession,
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading indicator initially', () => {
    render(<DailyTrainingScreen />);
    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();
  });

  it('renders lesson and advances progress', async () => {
    render(<DailyTrainingScreen />);

    // Wait for the lesson to load
    await waitFor(() => {
      expect(screen.getByTestId('lesson-instruction')).toBeInTheDocument();
    });

    // We start at index 0, total 2
    expect(screen.getByText('Find the winning tactic.')).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Lesson progress' })).toHaveTextContent('Lesson 1 of 2');

    // Click next
    fireEvent.click(screen.getByTestId('next-lesson-button'));

    // Now at index 1
    expect(screen.getByText('Find the next move.')).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Lesson progress' })).toHaveTextContent('Lesson 2 of 2');
    expect(screen.getByRole('heading', { name: 'Daily Training' })).toHaveFocus();
    
    // Click next again to finish
    fireEvent.click(screen.getByTestId('next-lesson-button'));
    expect(screen.getByRole('heading', { name: 'Training Complete!' })).toHaveFocus();

    expect(screen.getByTestId('session-complete')).toBeInTheDocument();
  });

  it('orients each lesson to its solver, including Black', async () => {
    vi.spyOn(ApiClient, 'getTrainingSession').mockResolvedValueOnce({
      session_id: 'orientation-session',
      move_choices: {},
      attempts_used: { 'lesson-1': 0, 'white-lesson': 0 },
      lessons: [
        { ...mockLesson, source: { ...mockLesson.source, fen: mockLesson.source.fen.replace(' w ', ' b ') } },
        { ...mockLesson, lesson_id: 'white-lesson' },
      ],
    });
    render(<DailyTrainingScreen />);
    expect(await screen.findByTestId('submit-daily-move')).toHaveAttribute('data-orientation', 'black');
    fireEvent.click(screen.getByTestId('next-lesson-button'));
    expect(screen.getByTestId('submit-daily-move')).toHaveAttribute('data-orientation', 'white');
  });

  it('shows an explicit no-eligible-lessons state', async () => {
    vi.spyOn(ApiClient, 'getTrainingSession').mockResolvedValueOnce({
      session_id: 'session-empty',
      move_choices: {},
      attempts_used: {},
      lessons: [],
    });

    render(<DailyTrainingScreen />);

    expect(await screen.findByTestId('no-eligible-lessons')).toHaveTextContent(
      'Analyse another game to generate a reviewable lesson.'
    );
  });

  it('records a catalog transfer lesson as a transfer measurement', async () => {
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValueOnce({
      session_id: 'transfer-session',
      move_choices: {},
      attempts_used: { 'aab0f1be-b070-4c0c-bfe2-49d8cdb68bef': 0 },
      lessons: [{
        ...mockLesson,
        lesson_id: 'aab0f1be-b070-4c0c-bfe2-49d8cdb68bef',
        verification: {
          ...mockLesson.verification,
          engine: 'transfer_catalog',
        },
      }],
    });
    vi.mocked(ApiClient.recordLessonAttempt).mockResolvedValueOnce({
      id: 'transfer-attempt',
      attempts_used: 1,
      success: true,
      grading_status: 'verified',
      profile_update_result: 'not_applicable',
    });

    render(<DailyTrainingScreen />);

    fireEvent.click(await screen.findByTestId('submit-daily-move'));

    await waitFor(() => {
      expect(ApiClient.recordLessonAttempt).toHaveBeenCalledWith(
        expect.objectContaining({ source_kind: 'transfer_measurement' }),
      );
    });
  });
  it('reveals hints only after a rejected attempt and records their use', async () => {
    const hintedLesson: LessonSpec = {
      ...mockLesson,
      interaction: {
        input: 'click',
        maximum_attempts: 3,
        accepted_moves: [],
      },
      hints: [{ level: 1, kind: 'prompt', text: 'Control the centre.' }],
    };
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValueOnce({
      session_id: 'session-hinted',
      move_choices: {},
      attempts_used: { 'lesson-1': 0 },
      lessons: [hintedLesson],
    });
    vi.mocked(ApiClient.recordLessonAttempt)
      .mockResolvedValueOnce({
        id: 'attempt-1',
        attempts_used: 1,
        success: false,
        grading_status: 'verified',
        profile_update_result: 'applied',
      })
      .mockResolvedValueOnce({
        id: 'attempt-2',
        attempts_used: 2,
        success: false,
        grading_status: 'verified',
        profile_update_result: 'applied',
      });

    render(<DailyTrainingScreen />);

    expect(await screen.findByTestId('lesson-instruction')).toBeInTheDocument();
    expect(screen.queryByTestId('lesson-hint')).not.toBeInTheDocument();
    const submitMove = screen.getByTestId('submit-daily-move');
    fireEvent.click(submitMove);
    await waitFor(() => expect(screen.getByTestId('lesson-hint')).toHaveTextContent('Control the centre.'));
    expect(ApiClient.recordLessonAttempt).toHaveBeenLastCalledWith(
      expect.objectContaining({ hints_used: 0 }),
    );

    fireEvent.click(submitMove);
    await waitFor(() => expect(ApiClient.recordLessonAttempt).toHaveBeenCalledTimes(2));
    expect(ApiClient.recordLessonAttempt).toHaveBeenLastCalledWith(
      expect.objectContaining({ hints_used: 1 }),
    );
    expect(screen.getByTestId('lesson-feedback')).toHaveTextContent('Not accepted. Attempt recorded.');
  });

  it('disables a lesson after its maximum recorded attempts', async () => {
    const limitedLesson: LessonSpec = {
      ...mockLesson,
      interaction: {
        input: 'click',
        maximum_attempts: 1,
        accepted_moves: [],
      },
    };
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValueOnce({
      session_id: 'session-limited',
      move_choices: {},
      attempts_used: { 'lesson-1': 0 },
      lessons: [limitedLesson],
    });
    vi.mocked(ApiClient.recordLessonAttempt).mockResolvedValueOnce({
      id: 'attempt-1',
      attempts_used: 1,
      success: false,
      grading_status: 'verified',
      profile_update_result: 'applied',
    });
    render(<DailyTrainingScreen />);

    const submitMove = await screen.findByTestId('submit-daily-move');
    fireEvent.click(submitMove);

    await waitFor(() => {
      expect(ApiClient.recordLessonAttempt).toHaveBeenCalledTimes(1);
    });
    expect(submitMove).toBeDisabled();
    expect(screen.getByTestId('lesson-feedback')).toHaveTextContent(
      'Not accepted. Attempt recorded.',
    );
    fireEvent.click(submitMove);
    expect(ApiClient.recordLessonAttempt).toHaveBeenCalledTimes(1);
  });

  it('shows Correct for a non-best accepted move and sends it for server grading', async () => {
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValueOnce({
      session_id: 'session-sound',
      move_choices: {},
      attempts_used: { 'lesson-1': 0 },
      lessons: [{
        ...mockLesson,
        interaction: {
          input: 'move',
          maximum_attempts: 3,
          accepted_moves: [
            { san: 'd4', lan: 'd2d4', reason: 'Best engine evaluation' },
            { san: 'e4', lan: 'e2e4', reason: 'Within soundness tolerance' },
          ],
        },
      }],
    });
    vi.mocked(ApiClient.recordLessonAttempt).mockResolvedValueOnce({
      id: 'attempt-sound',
      attempts_used: 1,
      success: true,
      grading_status: 'verified',
      profile_update_result: 'applied',
    });

    render(<DailyTrainingScreen />);
    const submitMove = await screen.findByTestId('submit-daily-move');
    fireEvent.click(submitMove);

    expect(await screen.findByTestId('lesson-feedback')).toHaveTextContent('Correct. Attempt recorded.');
    expect(ApiClient.recordLessonAttempt).toHaveBeenCalledWith(expect.objectContaining({
      session_id: 'session-sound',
      source_kind: 'persisted_opportunity',
      submitted_move: 'e2e4',
    }));
    expect(submitMove).toBeDisabled();
  });

  it.each([true, false])('draws all three hints and the final explanation (last success: %s)', async (success) => {
    const visualizations = [1, 2, 3].map(level => [{
      command: level === 3 ? 'draw_arrow' : 'highlight_square',
      description: `Step ${level}`, square: 'e2', origin: 'e2', target: 'e4',
    }]);
    const lesson: LessonSpec = {
      ...mockLesson,
      interaction: { input: 'move', maximum_attempts: 3, accepted_moves: [] },
      hints: visualizations.map((drawings, index) => ({
        level: index + 1, kind: 'hint', text: `Hint ${index + 1}`, visualizations: drawings,
      })),
      explanation: { text: 'Final explanation', visualizations: visualizations[2] },
    };
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValueOnce({
      session_id: 'ladder-session', lessons: [lesson],
      move_choices: {},
      attempts_used: { 'lesson-1': 0 },
    });
    const result = { id: 'attempt', attempts_used: 1, success: false, grading_status: 'verified', profile_update_result: 'applied' };
    vi.mocked(ApiClient.recordLessonAttempt)
      .mockResolvedValueOnce(result).mockResolvedValueOnce({ ...result, attempts_used: 2 })
      .mockResolvedValueOnce({ ...result, attempts_used: 3, success });
    render(<DailyTrainingScreen />);
    const submit = await screen.findByTestId('submit-daily-move');
    for (let index = 0; index < 2; index += 1) {
      fireEvent.click(submit);
      await waitFor(() => expect(screen.getByTestId('lesson-hint')).toHaveTextContent(`Hint ${index + 1}`));
      expect(JSON.parse(submit.getAttribute('data-visualizations')!)).toEqual(visualizations[index]);
    }
    fireEvent.click(screen.getByTestId('request-hint-button'));
    expect(screen.getByTestId('lesson-hint')).toHaveTextContent('Hint 3');
    expect(ApiClient.recordLessonAttempt).toHaveBeenCalledTimes(2);
    expect(JSON.parse(submit.getAttribute('data-visualizations')!)).toEqual(visualizations[2]);
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    await screen.findByTestId('lesson-explanation');
    expect(ApiClient.recordLessonAttempt).toHaveBeenLastCalledWith(expect.objectContaining({ hints_used: 3 }));
    expect(submit).toBeDisabled();
    expect(screen.getByTestId('lesson-explanation')).toHaveTextContent('Final explanation');
    expect(JSON.parse(submit.getAttribute('data-visualizations')!)).toEqual(visualizations[2]);
  });

  it('lets a player request the entire ladder before attempting a move', async () => {
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValueOnce({
      move_choices: {},
      attempts_used: { 'lesson-1': 0 },
      session_id: 'manual-ladder', lessons: [{
        ...mockLesson,
        interaction: { input: 'move', maximum_attempts: 3, accepted_moves: [] },
        hints: [1, 2, 3].map(level => ({ level, kind: 'hint', text: `Manual ${level}` })),
      }],
    });
    render(<DailyTrainingScreen />);
    const hint = await screen.findByTestId('request-hint-button');
    for (let level = 1; level <= 3; level += 1) fireEvent.click(hint);
    expect(screen.getByTestId('lesson-hint')).toHaveTextContent('Manual 3');
    expect(hint).toBeDisabled();
    expect(ApiClient.recordLessonAttempt).not.toHaveBeenCalled();
    expect(screen.getByTestId('submit-daily-move')).not.toBeDisabled();
  });
  it('grades either drill choice on the server and locks a solved drill', async () => {
    const lesson: LessonSpec = {
      ...mockLesson,
      interaction: { input: 'move', maximum_attempts: 3, accepted_moves: [{ san: 'e4' }] },
    };
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValue({
      session_id: 'choice-session',
      lessons: [lesson],
      attempts_used: { [lesson.lesson_id]: 0 },
      move_choices: {
        [lesson.lesson_id]: [{ uci: 'd2d4', san: 'd4' }, { uci: 'e2e4', san: 'e4' }],
      },
    });
    vi.mocked(ApiClient.recordLessonAttempt)
      .mockResolvedValueOnce({
        id: 'wrong-choice', attempts_used: 1, success: false,
        grading_status: 'verified', profile_update_result: 'applied',
      })
      .mockResolvedValueOnce({
        id: 'sound-choice', attempts_used: 2, success: true,
        grading_status: 'verified', profile_update_result: 'applied',
      });
    render(<DailyTrainingScreen />);
    await screen.findByRole('heading', { name: 'Daily Training' });
    fireEvent.click(screen.getByRole('button', { name: 'Safe or blunder' }));
    await screen.findByRole('heading', { name: 'Safe or blunder' });
    expect(screen.getByTestId('submit-daily-move')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'd4' }));
    expect(await screen.findByTestId('lesson-feedback')).toHaveTextContent('Not accepted');
    fireEvent.click(screen.getByRole('button', { name: 'e4' }));
    await waitFor(() => expect(screen.getByTestId('lesson-feedback')).toHaveTextContent('Correct'));
    expect(screen.getByRole('button', { name: 'd4' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'e4' })).toBeDisabled();
  });

  it('does not replace the chosen drill mode with a late previous session', async () => {
    const first = Promise.withResolvers<TrainingSessionRead>();
    vi.mocked(ApiClient.getTrainingSession)
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce({ session_id: 'empty-drill', lessons: [], move_choices: {}, attempts_used: {} });
    render(<DailyTrainingScreen />);
    fireEvent.click(screen.getByRole('button', { name: 'Safe or blunder' }));
    await screen.findByRole('heading', { name: 'No safe-or-blunder drills available' });
    await act(async () => {
      first.resolve({ session_id: 'late-review', lessons: [mockLesson], move_choices: {}, attempts_used: { 'lesson-1': 0 } });
    });
    expect(screen.getByRole('heading', { name: 'No safe-or-blunder drills available' })).toBeInTheDocument();
    expect(screen.queryByTestId('lesson-board')).not.toBeInTheDocument();
  });

  it('does not make a lesson playable when the server omits its usage count', async () => {
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValueOnce({
      session_id: 'missing-usage', lessons: [mockLesson], move_choices: {}, attempts_used: {},
    });
    render(<DailyTrainingScreen />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Failed to load training session');
    expect(screen.queryByTestId('submit-daily-move')).not.toBeInTheDocument();
    expect(ApiClient.recordLessonAttempt).not.toHaveBeenCalled();
  });

  it.each(['review', 'safe_or_blunder'] as const)(
    'finishes the last wrong attempt after two prior attempts and preserves exhaustion across modes (%s)',
    async mode => {
      const lesson: LessonSpec = {
        ...mockLesson,
        explanation: { text: 'The central pawn move is sound.', visualizations: [] },
      };
      const session: TrainingSessionRead = {
        session_id: 'last-attempt-session',
        lessons: [lesson],
        attempts_used: { [lesson.lesson_id]: 2 },
        move_choices: {
          [lesson.lesson_id]: [{ uci: 'd2d4', san: 'd4' }, { uci: 'e2e4', san: 'e4' }],
        },
      };
      vi.mocked(ApiClient.getTrainingSession).mockResolvedValue(session);
      vi.mocked(ApiClient.recordLessonAttempt).mockResolvedValueOnce({
        id: 'last-wrong-attempt', attempts_used: 3, success: false,
        grading_status: 'verified', profile_update_result: 'applied',
      });
      render(<DailyTrainingScreen />);
      await screen.findByRole('heading', { name: 'Daily Training' });
      if (mode === 'safe_or_blunder') {
        fireEvent.click(screen.getByRole('button', { name: 'Safe or blunder' }));
        await screen.findByRole('heading', { name: 'Safe or blunder' });
      }
      const submit = mode === 'review'
        ? screen.getByTestId('submit-daily-move')
        : screen.getByRole('button', { name: 'd4' });
      expect(submit).toBeEnabled();
      fireEvent.click(submit);

      expect(await screen.findByTestId('lesson-explanation')).toHaveTextContent('The central pawn move is sound.');
      expect(screen.getByTestId('lesson-feedback')).toHaveTextContent('Not accepted. Attempt recorded.');
      expect(submit).toBeDisabled();
      if (mode === 'safe_or_blunder') expect(screen.getByRole('button', { name: 'e4' })).toBeDisabled();
      fireEvent.click(submit);
      expect(ApiClient.recordLessonAttempt).toHaveBeenCalledTimes(1);

      vi.mocked(ApiClient.getTrainingSession).mockResolvedValue({
        ...session, session_id: 'continued-session', attempts_used: { [lesson.lesson_id]: 3 },
      });
      fireEvent.click(screen.getByRole('button', { name: mode === 'review' ? 'Safe or blunder' : 'Find a move' }));
      await screen.findByRole('heading', { name: mode === 'review' ? 'Safe or blunder' : 'Daily Training' });
      expect(screen.getByTestId('lesson-explanation')).toHaveTextContent('The central pawn move is sound.');
      expect(screen.queryByTestId('lesson-feedback')).not.toBeInTheDocument();
      expect(screen.getByTestId('submit-daily-move')).toBeDisabled();
      if (mode === 'review') {
        expect(screen.getByRole('button', { name: 'd4' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'e4' })).toBeDisabled();
      }
      expect(ApiClient.recordLessonAttempt).toHaveBeenCalledTimes(1);
    },
  );

  it('preserves the next lesson’s existing exhausted budget when advancing', async () => {
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValueOnce({
      session_id: 'per-lesson-usage',
      lessons: [
        mockLesson,
        {
          ...mockLesson, lesson_id: 'already-used',
          objective: { type: 'find_best_move', instruction: 'Review the already-used lesson.' },
          explanation: { text: 'This lesson already used its three attempts.', visualizations: [] },
        },
      ],
      attempts_used: { [mockLesson.lesson_id]: 0, 'already-used': 3 },
      move_choices: {},
    });
    render(<DailyTrainingScreen />);
    expect(await screen.findByTestId('submit-daily-move')).toBeEnabled();
    fireEvent.click(screen.getByTestId('next-lesson-button'));

    expect(screen.getByTestId('lesson-instruction')).toHaveTextContent('Review the already-used lesson.');
    expect(screen.getByTestId('submit-daily-move')).toBeDisabled();
    expect(screen.getByTestId('lesson-explanation')).toHaveTextContent('This lesson already used its three attempts.');
    expect(ApiClient.recordLessonAttempt).not.toHaveBeenCalled();
  });

  it('uses the successful POST count when other attempts consumed the remaining budget', async () => {
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValueOnce({
      session_id: 'concurrent-usage', lessons: [mockLesson],
      attempts_used: { [mockLesson.lesson_id]: 0 }, move_choices: {},
    });
    vi.mocked(ApiClient.recordLessonAttempt).mockResolvedValueOnce({
      id: 'third-server-attempt', attempts_used: 3, success: false,
      grading_status: 'verified', profile_update_result: 'applied',
    });
    render(<DailyTrainingScreen />);
    fireEvent.click(await screen.findByTestId('submit-daily-move'));

    await screen.findByTestId('lesson-explanation');
    expect(screen.getByTestId('submit-daily-move')).toBeDisabled();
    expect(screen.getByTestId('lesson-feedback')).toHaveTextContent('Not accepted. Attempt recorded.');
    fireEvent.click(screen.getByTestId('submit-daily-move'));
    expect(ApiClient.recordLessonAttempt).toHaveBeenCalledTimes(1);
  });

  it.each(['review', 'safe_or_blunder'] as const)(
    'reveals the explanation and locks choices after an authoritative server limit conflict (%s)',
    async mode => {
      const lesson: LessonSpec = {
        ...mockLesson,
        explanation: { text: 'The server has exhausted this lesson.', visualizations: [] },
      };
      vi.mocked(ApiClient.getTrainingSession).mockResolvedValue({
        session_id: 'limit-conflict-session', lessons: [lesson],
        attempts_used: { [lesson.lesson_id]: 2 },
        move_choices: {
          [lesson.lesson_id]: [{ uci: 'd2d4', san: 'd4' }, { uci: 'e2e4', san: 'e4' }],
        },
      });
      vi.mocked(ApiClient.recordLessonAttempt).mockRejectedValueOnce(new LessonAttemptLimitError(4));
      render(<DailyTrainingScreen />);
      await screen.findByRole('heading', { name: 'Daily Training' });
      if (mode === 'safe_or_blunder') {
        fireEvent.click(screen.getByRole('button', { name: 'Safe or blunder' }));
        await screen.findByRole('heading', { name: 'Safe or blunder' });
      }
      const submit = mode === 'review'
        ? screen.getByTestId('submit-daily-move')
        : screen.getByRole('button', { name: 'd4' });
      fireEvent.click(submit);

      expect(await screen.findByTestId('lesson-explanation')).toHaveTextContent('The server has exhausted this lesson.');
      expect(screen.getByTestId('lesson-feedback')).toHaveTextContent(
        'All 4 attempts used. Review the explanation before continuing.',
      );
      expect(submit).toBeDisabled();
      expect(screen.getByTestId('request-hint-button')).toBeDisabled();
      expect(screen.getByTestId('next-lesson-button')).toBeEnabled();
      if (mode === 'safe_or_blunder') expect(screen.getByRole('button', { name: 'e4' })).toBeDisabled();
      fireEvent.click(submit);
      expect(ApiClient.recordLessonAttempt).toHaveBeenCalledTimes(1);
    },
  );

  it.each([
    { mode: 'review', outcome: 'success' },
    { mode: 'safe_or_blunder', outcome: 'success' },
    { mode: 'safe_or_blunder', outcome: 'failure' },
    { mode: 'safe_or_blunder', outcome: 'error' },
  ] as const)(
    'prevents advancing during a pending $mode submission and keeps its $outcome out of the next lesson',
    async ({ mode, outcome }) => {
      const pending = Promise.withResolvers<LessonAttemptRead>();
      const firstLesson: LessonSpec = {
        ...mockLesson,
        hints: [{ level: 1, kind: 'hint', text: 'Only the first lesson hint.' }],
        explanation: { text: 'Only the first lesson explanation.', visualizations: [] },
      };
      const secondLesson: LessonSpec = {
        ...mockLesson, lesson_id: 'second-lesson',
        objective: { type: 'find_best_move', instruction: 'Second lesson remains independent.' },
      };
      vi.mocked(ApiClient.getTrainingSession).mockResolvedValue({
        session_id: 'pending-session', lessons: [firstLesson, secondLesson],
        attempts_used: { [firstLesson.lesson_id]: 0, [secondLesson.lesson_id]: 2 },
        move_choices: {
          [firstLesson.lesson_id]: [{ uci: 'd2d4', san: 'd4' }, { uci: 'e2e4', san: 'e4' }],
          [secondLesson.lesson_id]: [{ uci: 'b1c3', san: 'Nc3' }, { uci: 'g1f3', san: 'Nf3' }],
        },
      });
      vi.mocked(ApiClient.recordLessonAttempt).mockReturnValueOnce(pending.promise);
      render(<DailyTrainingScreen />);
      await screen.findByRole('heading', { name: 'Daily Training' });
      if (mode === 'safe_or_blunder') {
        fireEvent.click(screen.getByRole('button', { name: 'Safe or blunder' }));
        await screen.findByRole('heading', { name: 'Safe or blunder' });
      }
      const submit = mode === 'review'
        ? screen.getByTestId('submit-daily-move')
        : screen.getByRole('button', { name: 'd4' });
      const next = screen.getByTestId('next-lesson-button');
      act(() => {
        fireEvent.click(submit);
        fireEvent.click(next);
      });

      expect(screen.getByRole('status', { name: 'Lesson progress' })).toHaveTextContent('Lesson 1 of 2');
      expect(next).toBeDisabled();
      expect(submit).toBeDisabled();
      fireEvent.click(next);
      expect(screen.getByRole('status', { name: 'Lesson progress' })).toHaveTextContent('Lesson 1 of 2');
      expect(ApiClient.recordLessonAttempt).toHaveBeenCalledTimes(1);

      await act(async () => {
        if (outcome === 'error') pending.reject(new Error('Attempt transport failed'));
        else pending.resolve({
          id: 'first-lesson-attempt', attempts_used: 3, success: outcome === 'success',
          grading_status: 'verified', profile_update_result: 'applied',
        });
      });
      expect(screen.getByTestId('lesson-feedback')).toHaveTextContent(
        outcome === 'success' ? 'Correct' : outcome === 'failure' ? 'Not accepted' : 'Could not record this attempt.',
      );
      expect(next).toBeEnabled();
      fireEvent.click(next);

      expect(screen.getByRole('status', { name: 'Lesson progress' })).toHaveTextContent('Lesson 2 of 2');
      expect(screen.queryByTestId('lesson-feedback')).not.toBeInTheDocument();
      expect(screen.queryByTestId('lesson-hint')).not.toBeInTheDocument();
      expect(screen.queryByTestId('lesson-explanation')).not.toBeInTheDocument();
      if (mode === 'review') {
        expect(screen.getByTestId('lesson-instruction')).toHaveTextContent('Second lesson remains independent.');
        expect(screen.getByTestId('submit-daily-move')).toBeEnabled();
      } else {
        expect(screen.getByRole('button', { name: 'Nc3' })).toBeEnabled();
        expect(screen.getByRole('button', { name: 'Nf3' })).toBeEnabled();
      }
      expect(ApiClient.recordLessonAttempt).toHaveBeenCalledTimes(1);
    },
  );
});
