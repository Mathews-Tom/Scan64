import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CriticalMomentReview } from './CriticalMomentReview';
import { ApiClient } from '../api/client';
import { reviewLesson } from './review.test-fixtures';

vi.mock('./CoachBoard', () => ({ CoachBoard: ({ disabled, onMove }: { disabled?: boolean; onMove: (move: string) => void }) =>
  <button type="button" data-testid="retry-move" disabled={disabled} onClick={() => onMove('g1f3')}>Submit retry move</button>,
}));
afterEach(() => { vi.restoreAllMocks(); });

describe('critical-moment lifetime attempts', () => {
  it('resumes the idea after two spent attempts and spends only the last server-authorized attempt', async () => {
    const record = vi.spyOn(ApiClient, 'recordLessonAttempt').mockResolvedValue({ id: 'a', success: false, grading_status: 'verified', profile_update_result: 'applied' });
    const onRecorded = vi.fn();
    render(<CriticalMomentReview lesson={reviewLesson} opportunityId="opportunity-1" sessionId="study-1"
      initialAttemptsUsed={2} initialAttemptsRemaining={1} onAttemptRecorded={onRecorded} />);
    expect(screen.getByTestId('hint-1')).toHaveTextContent('Consider your knight.');
    fireEvent.click(screen.getByTestId('retry-move'));
    await waitFor(() => expect(screen.getByTestId('retry-move')).toBeDisabled());
    expect(record).toHaveBeenCalledWith(expect.objectContaining({ hints_used: 2, submitted_move: 'g1f3', source_kind: 'persisted_opportunity' }));
    expect(onRecorded).toHaveBeenCalledWith(3);
    expect(screen.getByTestId('explanation')).toHaveTextContent('Accepted moves: Nf3');
    fireEvent.click(screen.getByTestId('retry-move'));
    expect(record).toHaveBeenCalledTimes(1);
  });

  it('uses the existing hint ladder and records assistance on a resumed correct answer', async () => {
    const record = vi.spyOn(ApiClient, 'recordLessonAttempt').mockResolvedValue({ id: 'a', success: true, grading_status: 'verified', profile_update_result: 'applied' });
    render(<CriticalMomentReview lesson={reviewLesson} opportunityId="opportunity-1" sessionId="study-1"
      initialAttemptsUsed={1} initialAttemptsRemaining={2} />);
    expect(screen.getByTestId('hint-0')).toHaveTextContent('Which piece needs attention?');
    fireEvent.click(screen.getByTestId('request-next-hint'));
    expect(screen.getByTestId('hint-1')).toHaveTextContent('Consider your knight.');
    fireEvent.click(screen.getByTestId('retry-move'));
    await screen.findByTestId('critical-attempt-recorded');
    expect(record).toHaveBeenCalledWith(expect.objectContaining({ session_id: 'study-1', lesson_id: 'opportunity-1', hints_used: 2 }));
    expect(screen.getByTestId('retry-move')).toBeDisabled();
  });

  it('honours remaining attempts even when lower than the lesson maximum', async () => {
    const record = vi.spyOn(ApiClient, 'recordLessonAttempt').mockResolvedValue({ id: 'a', success: false, grading_status: 'verified', profile_update_result: 'applied' });
    render(<CriticalMomentReview lesson={reviewLesson} opportunityId="opportunity-1" sessionId="study-1"
      initialAttemptsUsed={1} initialAttemptsRemaining={1} />);
    fireEvent.click(screen.getByTestId('retry-move'));
    await waitFor(() => expect(screen.getByTestId('explanation')).toBeInTheDocument());
    expect(screen.getByTestId('retry-move')).toBeDisabled();
    expect(record).toHaveBeenCalledTimes(1);
  });

  it('disables already exhausted retry interaction and shows explanation without spending attempts', () => {
    const record = vi.spyOn(ApiClient, 'recordLessonAttempt');
    render(<CriticalMomentReview lesson={reviewLesson} opportunityId="opportunity-1" sessionId="study-1"
      initialAttemptsUsed={3} initialAttemptsRemaining={0} />);
    expect(screen.getByTestId('retry-move')).toBeDisabled();
    expect(screen.getByTestId('request-next-hint')).toBeDisabled();
    expect(screen.getByTestId('explanation')).toHaveTextContent('Accepted moves: Nf3');
    expect(record).not.toHaveBeenCalled();
  });
});
