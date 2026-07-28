import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { vi, describe, beforeEach, afterEach, it, expect } from 'vitest';
import { DailyTrainingScreen } from './DailyTrainingScreen';
import { ApiClient } from '../api/client';
import type { LessonSpec } from '../api/types';

vi.mock('../api/client', () => ({
  ApiClient: {
    getTrainingSession: vi.fn(),
    recordLessonAttempt: vi.fn(),
  }
}));

vi.mock('./LessonBoard', () => ({
  LessonBoard: ({
    disabled,
    onMove,
  }: {
    disabled: boolean;
    onMove: (move: string) => void;
  }) => (
    <button
      data-testid="submit-daily-move"
      disabled={disabled}
      onClick={() => onMove('e2e4')}
    >
      Submit move
    </button>
  ),
}));

const mockLesson: LessonSpec = {
  schema_version: '1.0',
  lesson_id: 'lesson-1',
  source: {
    kind: 'position',
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
  interaction: {} as unknown as LessonSpec['interaction'],
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
    expect(screen.getByText('0 / 2')).toBeInTheDocument();

    // Click next
    fireEvent.click(screen.getByTestId('next-lesson-button'));

    // Now at index 1
    expect(screen.getByText('Find the next move.')).toBeInTheDocument();
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    
    // Click next again to finish
    fireEvent.click(screen.getByTestId('next-lesson-button'));

    expect(screen.getByTestId('session-complete')).toBeInTheDocument();
  });

  it('shows an explicit no-eligible-lessons state', async () => {
    vi.spyOn(ApiClient, 'getTrainingSession').mockResolvedValueOnce({
      session_id: 'session-empty',
      lessons: [],
    });

    render(<DailyTrainingScreen />);

    expect(await screen.findByTestId('no-eligible-lessons')).toHaveTextContent(
      'Analyse another game to generate a reviewable lesson.'
    );
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
      lessons: [limitedLesson],
    });
    vi.mocked(ApiClient.recordLessonAttempt).mockResolvedValueOnce({
      id: 'attempt-1',
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
});
