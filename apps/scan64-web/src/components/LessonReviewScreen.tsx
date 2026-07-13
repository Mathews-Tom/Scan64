import { useState } from 'react';
import { LessonSpec } from '../api/types';

interface Props {
  lesson: LessonSpec;
  onComplete?: () => void;
}

export function LessonReviewScreen({ lesson, onComplete }: Props) {
  const [hintIndex, setHintIndex] = useState(-1);

  const showNextHint = () => {
    if (hintIndex < lesson.hints.length - 1) {
      setHintIndex(hintIndex + 1);
    }
  };

  const currentHints = lesson.hints.slice(0, hintIndex + 1);

  return (
    <div className="lesson-review" data-testid="lesson-review">
      <h2>Critical Moment Review</h2>
      <div className="objective">
        <strong>Objective:</strong> {lesson.objective.instruction}
      </div>

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

      {hintIndex < lesson.hints.length - 1 && (
        <button onClick={showNextHint} data-testid="next-hint-btn">
          Show Hint
        </button>
      )}

      {hintIndex === lesson.hints.length - 1 && (
        <div className="explanation" data-testid="explanation">
          <h3>Explanation</h3>
          <p>{lesson.explanation.text}</p>
          <button onClick={onComplete} data-testid="complete-btn">Finish Review</button>
        </div>
      )}
    </div>
  );
}
