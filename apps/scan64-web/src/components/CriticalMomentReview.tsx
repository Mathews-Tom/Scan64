import { useState } from 'react';
import type { LessonSpec } from '../api/types';

interface Props {
  lesson: LessonSpec;
  requireIntent?: boolean;
  onComplete?: () => void;
}

export function CriticalMomentReview({ lesson, requireIntent, onComplete }: Props) {
  const [step, setStep] = useState<number>(1);
  const [intent, setIntent] = useState('');
  const [hintIndex, setHintIndex] = useState(-1);

  const handleNextStep = () => {
    if (step === 1) {
      setStep(2);
    } else if (step === 2) {
      if (requireIntent && !intent.trim()) return;
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

      <div className="step-content">
        {step >= 1 && <div data-testid="step-1-restore">Critical position restored.</div>}
        
        {step >= 2 && (
          <div data-testid="step-2-inspect">
            <p>Please inspect the position.</p>
            {step === 2 && (
              <textarea 
                data-testid="intent-input"
                placeholder={requireIntent ? "Required: What were you thinking?" : "Optional: What were you thinking?"}
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
          <button 
            onClick={handleNextStep} 
            data-testid="next-step-btn"
            disabled={step === 2 && requireIntent && !intent.trim()}
          >
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
