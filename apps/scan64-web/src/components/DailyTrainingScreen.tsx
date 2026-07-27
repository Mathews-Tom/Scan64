import React, { useEffect, useRef, useState } from 'react';

import { ApiClient } from '../api/client';
import type { LessonSpec } from '../api/types';
import { LessonBoard } from './LessonBoard';

export const DailyTrainingScreen: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionLessons, setSessionLessons] = useState<LessonSpec[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [hintsUsed, setHintsUsed] = useState(0);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const startedAt = useRef(0);

  useEffect(() => {
    void (async () => {
      try {
        const session = await ApiClient.getTrainingSession();
        setSessionId(session.session_id);
        setSessionLessons(session.lessons);
        startedAt.current = performance.now();
      } catch (caught) {
        console.error('Failed to load training session:', caught);
        setError('Failed to load training session');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <div data-testid="loading-indicator">Loading your daily training...</div>;
  if (error) return <div data-testid="error-message">{error}</div>;
  if (sessionLessons.length === 0) return <div data-testid="no-eligible-lessons"><h2>No training lessons available</h2><p>Analyse another game to generate a reviewable lesson.</p></div>;
  if (currentIndex >= sessionLessons.length) return <div data-testid="session-complete"><h2>Training Complete!</h2></div>;

  const lesson = sessionLessons[currentIndex];
  const hint = lesson.hints[hintsUsed];
  const submitMove = async (move: string) => {
    if (sessionId === null || submitting) return;
    setSubmitting(true);
    try {
      const result = await ApiClient.recordLessonAttempt({
        session_id: sessionId,
        lesson_id: lesson.lesson_id,
        source_kind: 'persisted_opportunity',
        submitted_move: move,
        elapsed_ms: Math.round(performance.now() - startedAt.current),
        hints_used: hintsUsed,
      });
      if (result.success) {
        setFeedback('Correct. Attempt recorded.');
      } else {
        setHintsUsed(value => Math.min(value + 1, lesson.hints.length));
        setFeedback('Not accepted. Attempt recorded; the next hint is available.');
      }
    } catch (caught) {
      console.error('Failed to record lesson attempt:', caught);
      setFeedback('Could not record this attempt.');
    } finally {
      setSubmitting(false);
    }
  };

  const nextLesson = () => {
    setCurrentIndex(index => index + 1);
    setHintsUsed(0);
    setFeedback(null);
    startedAt.current = performance.now();
  };

  return <div className="daily-training-screen"><h2>Daily Training</h2><p>{currentIndex} / {sessionLessons.length}</p><LessonBoard lesson={lesson} disabled={submitting || feedback?.startsWith('Correct') === true} onMove={submitMove} /><div className="lesson-instruction" data-testid="lesson-instruction">{lesson.objective.instruction}</div>{hint !== undefined && <p data-testid="lesson-hint">Hint: {hint.text}</p>}{feedback !== null && <p data-testid="lesson-feedback">{feedback}</p>}<button data-testid="next-lesson-button" onClick={nextLesson}>{currentIndex === sessionLessons.length - 1 ? 'Finish Session' : 'Next Lesson'}</button></div>;
};
