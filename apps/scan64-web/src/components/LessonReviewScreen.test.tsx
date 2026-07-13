import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LessonReviewScreen } from './LessonReviewScreen';
import type { LessonSpec } from '../api/types';

const mockLesson: LessonSpec = {
  schema_version: '0.1.0',
  lesson_id: 'les_1',
  source: { kind: 'pgn', fen: 'fen' },
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

describe('LessonReviewScreen', () => {
  it('renders objective and first hint button', () => {
    render(<LessonReviewScreen lesson={mockLesson} />);
    expect(screen.getByText((_, element) => {
      return element?.textContent === 'Objective: Find the best move';
    })).toBeTruthy();
    expect(screen.queryByTestId('hint-0')).toBeNull();
    expect(screen.getByTestId('next-hint-btn')).toBeTruthy();
  });

  it('progressively shows hints', () => {
    render(<LessonReviewScreen lesson={mockLesson} />);
    
    // Show Hint 1
    fireEvent.click(screen.getByTestId('next-hint-btn'));
    expect(screen.getByTestId('hint-0').textContent).toContain('Hint 1');
    expect(screen.queryByTestId('hint-1')).toBeNull();
    
    // Show Hint 2
    fireEvent.click(screen.getByTestId('next-hint-btn'));
    expect(screen.getByTestId('hint-1').textContent).toContain('Hint 2');
    expect(screen.getByTestId('hint-1').textContent).toContain('highlight: square');
    
    // No more hints, button should be gone
    expect(screen.queryByTestId('next-hint-btn')).toBeNull();
  });

  it('shows explanation after all hints', () => {
    render(<LessonReviewScreen lesson={mockLesson} />);
    fireEvent.click(screen.getByTestId('next-hint-btn'));
    fireEvent.click(screen.getByTestId('next-hint-btn'));
    
    expect(screen.getByTestId('explanation').textContent).toContain('The explanation text');
  });
});
