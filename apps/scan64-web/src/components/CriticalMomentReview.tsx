import { useRef, useState } from 'react';
import { ApiClient } from '../api/client';
import { LessonBoard } from './LessonBoard';
import type { LessonSpec } from '../api/types';

interface Props {
  lesson: LessonSpec;
  sessionId: string;
  onComplete?: () => void;
}

export function CriticalMomentReview({ lesson, sessionId, onComplete }: Props) {
  const [step, setStep] = useState<number>(1);
  const [intent, setIntent] = useState('');
  const [hintIndex, setHintIndex] = useState(-1);
  const [submittedMove, setSubmittedMove] = useState<string | null>(null);
  const [attemptCount, setAttemptCount] = useState(0);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const startedAt = useRef(performance.now());

  const recordAttempt = async (move: string) => {
    if (
      submitting
      || submittedMove !== null
      || attemptCount >= lesson.interaction.maximum_attempts
    ) return;
    setSubmitting(true);
    try {
      const result = await ApiClient.recordLessonAttempt({
        session_id: sessionId,
        lesson_id: lesson.lesson_id,
        source_kind: 'persisted_opportunity',
        submitted_move: move,
        elapsed_ms: Math.round(performance.now() - startedAt.current),
        hints_used: Math.max(hintIndex + 1, 0),
      });
      setAttemptCount(count => count + 1);
      if (result.success) {
        setSubmittedMove(move);
        setFeedback('Correct. Attempt recorded.');
      } else {
        const nextHintExists = hintIndex < lesson.hints.length - 1;
        setHintIndex(value => Math.min(value + 1, lesson.hints.length - 1));
        setStep(value => Math.max(value, 4));
        setFeedback(
          nextHintExists
            ? 'Not accepted. Attempt recorded; the next hint is revealed.'
            : 'Not accepted. Attempt recorded.',
        );
      }
    } catch (caught) {
      setFeedback(caught instanceof Error ? caught.message : 'Could not record critical-moment attempt.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleNextStep = () => {
    if (step === 1) {
      setStep(2);
    } else if (step === 2) {
      setStep(3);
    } else if (step === 3) {
      if (lesson.hints.length > 0) {
        setStep(4);
        setHintIndex(0);
      } else {
        setStep(6);
      }
    } else if (step >= 4 && step <= 5) {
      if (hintIndex < lesson.hints.length - 2) {
        setStep(5);
        setHintIndex(hintIndex + 1);
      } else {
        setStep(6);
        setHintIndex(lesson.hints.length - 1);
      }
    } else if (step === 6) {
      setStep(7);
    }
  };

  const currentHints = hintIndex >= 0 ? lesson.hints.slice(0, hintIndex + 1) : [];

  return (
    <div className="critical-moment-review" data-testid="critical-moment-review">
      <h2>Critical Moment Review</h2>
      <div className="objective">
        <strong>Objective:</strong> {lesson.objective.instruction}
      </div>

      {feedback && <p role="alert">{feedback}</p>}
      <LessonBoard
        lesson={lesson}
        disabled={
          submitting
          || submittedMove !== null
          || attemptCount >= lesson.interaction.maximum_attempts
        }
        onMove={move => void recordAttempt(move)}
      />
      {submittedMove && <p data-testid="critical-attempt-recorded">Attempt recorded.</p>}

      <div className="step-content">
        {step >= 1 && <div data-testid="step-1-restore">Critical position restored.</div>}
        
        {step >= 2 && (
          <div data-testid="step-2-inspect">
            <p>Please inspect the position.</p>
            {step === 2 && (
              <textarea 
                data-testid="intent-input"
                placeholder="What were you thinking?"
                value={intent}
                onChange={(e) => setIntent(e.target.value)}
              />
            )}
            {step > 2 && intent && (
              <div data-testid="intent-display">Your intent: {intent}</div>
            )}
          </div>
        )}

        {step >= 3 && (
          <div data-testid="step-3-request">
            <p>Can you identify the opponent's threats or candidate moves?</p>
          </div>
        )}

        {step >= 4 && currentHints.length > 0 && (
          <div className="hints-container" data-testid="hints-container">
            {currentHints.map((hint, idx) => (
              <div key={idx} className="hint" data-testid={`hint-${idx}`}>
                {hint.text}
                {hint.visualizations && hint.visualizations.length > 0 && (
                  <ul className="visualizations">
                    {hint.visualizations.map((vis, vIdx) => (
                      <li key={vIdx}>{vis.command}: {vis.description}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        )}

        {step >= 6 && (
          <div className="explanation" data-testid="explanation">
            <h3>Explanation</h3>
            <p>{lesson.explanation.text}</p>
          </div>
        )}

        {step === 7 && (
          <div data-testid="step-7-replay">
            <p>Please replay the corrected line on the board.</p>
          </div>
        )}
      </div>

      <div className="actions">
        {step < 3 && (
          <button onClick={handleNextStep} data-testid="next-step-btn">
            Continue
          </button>
        )}
        {step === 3 && (
          <button onClick={handleNextStep} data-testid="request-cue-btn">Request Cue</button>
        )}
        {step >= 4 && step <= 5 && (
          <button onClick={handleNextStep} data-testid="request-assist-btn">
            {hintIndex < lesson.hints.length - 2 ? 'Request Assistance' : 'Show Answer'}
          </button>
        )}
        {step === 6 && (
          <button onClick={handleNextStep} data-testid="replay-btn">Replay</button>
        )}
        {step === 7 && (
          <button onClick={onComplete} data-testid="complete-btn">Finish Review</button>
        )}
      </div>
    </div>
  );
}
