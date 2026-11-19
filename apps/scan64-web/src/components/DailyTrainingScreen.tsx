import React, { useEffect, useRef, useState } from 'react';

import { ApiClient, LessonAttemptLimitError } from '../api/client';
import type { LessonSpec, SafeOrBlunderChoice, TrainingMode } from '../api/types';
import { CoachBoard } from './CoachBoard';
import { Button, Panel } from './Primitives';
import './WorkingScreens.css';

export const DailyTrainingScreen: React.FC = () => {
  const [mode, setMode] = useState<TrainingMode>('review');
  const [moveChoices, setMoveChoices] = useState<Record<string, SafeOrBlunderChoice[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionLessons, setSessionLessons] = useState<LessonSpec[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [hintIndex, setHintIndex] = useState(-1);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [attemptsUsed, setAttemptsUsed] = useState<Record<string, number>>({});
  const activeAttempt = useRef<{ sessionId: string; lessonId: string } | null>(null);
  const startedAt = useRef(0);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (currentIndex > 0) heading.current?.focus();
  }, [currentIndex]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const session = await ApiClient.getTrainingSession(mode);
        if (cancelled) return;
        if (mode === 'safe_or_blunder' && session.lessons.some(
          lesson => session.move_choices[lesson.lesson_id]?.length !== 2,
        )) throw new Error('The server did not supply two drill choices');
        if (session.lessons.some(
          lesson => typeof session.attempts_used[lesson.lesson_id] !== 'number',
        )) throw new Error('The server did not supply lesson attempt counts');
        setSessionId(session.session_id);
        setSessionLessons(session.lessons);
        setMoveChoices(session.move_choices);
        setAttemptsUsed(session.attempts_used);
        startedAt.current = performance.now();
      } catch (caught) {
        if (cancelled) return;
        console.error('Failed to load training session:', caught);
        setError('Failed to load training session');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      activeAttempt.current = null;
    };
  }, [mode]);

  const selectMode = (next: TrainingMode) => {
    if (submitting || activeAttempt.current !== null || next === mode) return;
    activeAttempt.current = null;
    setMode(next);
    setLoading(true);
    setError(null);
    setSessionId(null);
    setSessionLessons([]);
    setMoveChoices({});
    setCurrentIndex(0);
    setHintIndex(-1);
    setFeedback(null);
    setAttemptsUsed({});
    setSubmitting(false);
  };
  const renderScreen = (content: React.ReactNode) => <div className="daily-training-screen working-screen">
    <div role="group" aria-label="Training mode" className="working-controls">
      <Button aria-pressed={mode === 'review'} disabled={submitting || mode === 'review'}
        onClick={() => selectMode('review')}>Find a move</Button>
      <Button aria-pressed={mode === 'safe_or_blunder'} disabled={submitting || mode === 'safe_or_blunder'}
        onClick={() => selectMode('safe_or_blunder')}>Safe or blunder</Button>
    </div>
    {content}
  </div>;

  if (loading) return renderScreen(<div role="status" data-testid="loading-indicator">Loading your daily training...</div>);
  if (error) return renderScreen(<div role="alert" data-testid="error-message">{error}</div>);
  if (sessionLessons.length === 0) return renderScreen(<div data-testid="no-eligible-lessons">
    <h2>{mode === 'safe_or_blunder' ? 'No safe-or-blunder drills available' : 'No training lessons available'}</h2>
    <p>Analyse another game to generate a reviewable lesson.</p>
  </div>);
  if (currentIndex >= sessionLessons.length) return renderScreen(<div data-testid="session-complete"><h2 ref={heading} tabIndex={-1}>Training Complete!</h2></div>);

  const lesson = sessionLessons[currentIndex];
  const hint = hintIndex >= 0 ? lesson.hints[hintIndex] : undefined;
  const attemptCount = attemptsUsed[lesson.lesson_id];
  const finished = feedback?.startsWith('Correct') === true || attemptCount >= lesson.interaction.maximum_attempts;
  const submitMove = async (move: string) => {
    if (
      sessionId === null
      || submitting
      || activeAttempt.current !== null
      || finished
    ) return;
    const attempt = { sessionId, lessonId: lesson.lesson_id };
    activeAttempt.current = attempt;
    setSubmitting(true);
    try {
      const sourceKind = lesson.verification.engine === 'transfer_catalog'
        ? 'transfer_measurement'
        : 'persisted_opportunity';
      const result = await ApiClient.recordLessonAttempt({
        session_id: sessionId,
        lesson_id: lesson.lesson_id,
        source_kind: sourceKind,
        submitted_move: move,
        elapsed_ms: Math.round(performance.now() - startedAt.current),
        hints_used: Math.max(hintIndex + 1, 0),
      });
      if (activeAttempt.current !== attempt) return;
      setAttemptsUsed(counts => ({ ...counts, [lesson.lesson_id]: result.attempts_used }));
      if (result.success) {
        setFeedback('Correct. Attempt recorded.');
      } else {
        const nextHintExists = hintIndex < lesson.hints.length - 1;
        setHintIndex(value => Math.min(value + 1, lesson.hints.length - 1));
        setFeedback(
          nextHintExists
            ? 'Not accepted. Attempt recorded; the next hint is revealed.'
            : 'Not accepted. Attempt recorded.',
        );
      }
    } catch (caught) {
      if (activeAttempt.current !== attempt) return;
      if (caught instanceof LessonAttemptLimitError) {
        setAttemptsUsed(counts => ({ ...counts, [lesson.lesson_id]: caught.attemptsUsed }));
        setFeedback(`All ${caught.attemptsUsed} attempts used. Review the explanation before continuing.`);
        return;
      }
      console.error('Failed to record lesson attempt:', caught);
      setFeedback('Could not record this attempt.');
    } finally {
      if (activeAttempt.current === attempt) {
        activeAttempt.current = null;
        setSubmitting(false);
      }
    }
  };

  const nextLesson = () => {
    if (submitting || activeAttempt.current !== null) return;
    setCurrentIndex(index => index + 1);
    setHintIndex(-1);
    setFeedback(null);
    startedAt.current = performance.now();
  };

  return renderScreen(<>
    <h2 ref={heading} tabIndex={-1}>{mode === 'safe_or_blunder' ? 'Safe or blunder' : 'Daily Training'}</h2>
    <div className="board-workspace">
      <div className="board-workspace__board">
        <CoachBoard key={lesson.lesson_id} fen={lesson.source.fen}
          orientation={lesson.source.fen.split(' ')[1] === 'b' ? 'black' : 'white'}
          resetAfterAttempt testId="lesson-board" disabled={submitting || finished || mode === 'safe_or_blunder'}
          visualizations={finished ? lesson.explanation?.visualizations : hint?.visualizations}
          onMove={submitMove} />
      </div>
      <Panel className="board-workspace__side working-controls" aria-label="Training controls">
        <p role="status" aria-label="Lesson progress">Lesson {currentIndex + 1} of {sessionLessons.length}</p>
        <div className="lesson-instruction" data-testid="lesson-instruction">
          {mode === 'safe_or_blunder' ? 'Choose the sound move. One option is the move you played.' : lesson.objective.instruction}
        </div>
        {mode === 'safe_or_blunder' && <div role="group" aria-label="Move choices" className="working-controls">
          {moveChoices[lesson.lesson_id].map(choice => <Button key={choice.uci}
            disabled={submitting || finished} onClick={() => void submitMove(choice.uci)}>
            {choice.san}
          </Button>)}
        </div>}
        <div role="status" aria-atomic="true">{hint !== undefined && <p data-testid="lesson-hint">Hint: {hint.text}</p>}</div>
        <Button data-testid="request-hint-button"
          disabled={submitting || finished || hintIndex >= lesson.hints.length - 1}
          onClick={() => setHintIndex(value => Math.min(value + 1, lesson.hints.length - 1))}>
          Next hint
        </Button>
        <div role="status" aria-atomic="true">{feedback !== null && <p data-testid="lesson-feedback">{feedback}</p>}</div>
        {finished && <section data-testid="lesson-explanation"><h3>Explanation</h3><p>{lesson.explanation?.text}</p></section>}
        <Button variant="primary" data-testid="next-lesson-button" disabled={submitting}
          onClick={nextLesson}>{currentIndex === sessionLessons.length - 1 ? 'Finish Session' : 'Next Lesson'}</Button>
      </Panel>
    </div>
  </>);
};
