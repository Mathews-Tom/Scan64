import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { CriticalMomentReview } from './CriticalMomentReview';
import { ApiClient } from '../api/client';
import type { LessonSpec } from '../api/types';

vi.mock('./LessonBoard', () => ({
  LessonBoard: ({
    disabled,
    onMove,
  }: {
    disabled?: boolean;
    onMove: (move: string) => void;
  }) => (
    <button
      data-testid="submit-lesson-move"
      disabled={disabled}
      onClick={() => onMove('e2e4')}
    >
      Submit move
    </button>
  ),
}));

const mockLesson: LessonSpec = {
  schema_version: '0.1.0',
  lesson_id: 'les_1',
  source: { kind: 'pgn', fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1' },
  diagnosis: { primary: 'test', secondary: [], confidence: 1, evidence_refs: [] },
  objective: { type: 'test', instruction: 'Find the best move' },
  interaction: { input: 'move', maximum_attempts: 1, accepted_moves: [] },
  hints: [
    { level: 1, kind: 'prompt', text: 'Hint 1' },
    { level: 2, kind: 'prompt', text: 'Hint 2', visualizations: [{ command: 'highlight', description: 'square' }] }
  ],
  explanation: { text: 'The explanation text' },
  verification: { status: 'ok', engine: 'e', engine_binary_digest: 'd', nodes: 1, multipv: 1, verified_at: 'now' },
  mastery: { skill_key: 'key', delta: 0.1 }
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe('CriticalMomentReview', () => {
  it('flows through the 7-step sequence', () => {
    const onComplete = vi.fn();
    render(
      <CriticalMomentReview
        lesson={mockLesson}
        opportunityId="opportunity-1"
        sessionId="study-1"
        onComplete={onComplete}
      />,
    );
    
    // Step 1: Restore
    expect(screen.getByTestId('step-1-restore')).toBeTruthy();
    fireEvent.click(screen.getByTestId('next-step-btn'));

    // Step 2: Inspect
    expect(screen.getByTestId('step-2-inspect')).toBeTruthy();
    fireEvent.change(screen.getByTestId('intent-input'), { target: { value: 'I wanted to attack.' } });
    fireEvent.click(screen.getByTestId('next-step-btn'));

    // Step 3: Request
    expect(screen.getByTestId('intent-display').textContent).toBe('Your intent: I wanted to attack.');
    expect(screen.getByTestId('step-3-request')).toBeTruthy();
    fireEvent.click(screen.getByTestId('request-cue-btn'));

    // Step 4: Cue (Hint 1)
    expect(screen.getByTestId('hint-0').textContent).toContain('Hint 1');
    expect(screen.queryByTestId('hint-1')).toBeNull();
    fireEvent.click(screen.getByTestId('request-assist-btn')); // Show Answer

    // Step 5 -> skips because we only have 2 hints. Wait, length is 2. hintIndex=0. length - 2 = 0. So 0 < 0 is false.
    // So it should go to step 6 (Answer) showing hint 2.
    expect(screen.getByTestId('hint-1').textContent).toContain('Hint 2');
    expect(screen.getByTestId('explanation').textContent).toContain('The explanation text');

    // Step 6 -> 7
    fireEvent.click(screen.getByTestId('replay-btn'));
    expect(screen.getByTestId('step-7-replay')).toBeTruthy();

    // Step 7 -> Complete
    fireEvent.click(screen.getByTestId('complete-btn'));
    expect(onComplete).toHaveBeenCalled();
  });
  it('records a rejected game lesson against the supplied study context', async () => {
    const recordAttempt = vi.spyOn(ApiClient, 'recordLessonAttempt').mockResolvedValue({
      id: 'attempt-1',
      success: false,
      grading_status: 'verified',
      profile_update_result: 'applied',
    });
    render(
      <CriticalMomentReview
        lesson={mockLesson}
        opportunityId="opportunity-1"
        sessionId="game-study-1"
      />,
    );

    fireEvent.click(screen.getByTestId('submit-lesson-move'));

    await waitFor(() => {
      expect(recordAttempt).toHaveBeenCalledWith({
        session_id: 'game-study-1',
        lesson_id: 'opportunity-1',
        source_kind: 'persisted_opportunity',
        submitted_move: 'e2e4',
        elapsed_ms: expect.any(Number),
        hints_used: 0,
      });
    });
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Not accepted. Attempt recorded; the next hint is revealed.',
    );
    expect(screen.getByTestId('hint-0')).toHaveTextContent('Hint 1');
    const submitMove = screen.getByTestId('submit-lesson-move');
    expect(submitMove).toBeDisabled();
    fireEvent.click(submitMove);
    expect(recordAttempt).toHaveBeenCalledTimes(1);
  });
});
