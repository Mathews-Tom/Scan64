import { useState } from 'react';
import { ApiClient } from '../api/client';
import { LessonSpec } from '../api/types';

export function PgnImportScreen() {
  const [pgn, setPgn] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [lessons, setLessons] = useState<LessonSpec[]>([]);

  const handleImport = async () => {
    if (!pgn.trim()) return;
    setLoading(true);
    setError(null);
    setLessons([]);
    
    try {
      const game = await ApiClient.createGame({ pgn });
      // In a real app we might poll a background job here.
      // For this minimal M12 flow, we assume the API triggers analysis
      // and we can fetch opportunities (or we just display the imported game).
      // Based on M12 acceptance: "importing a PGN with a known recurring weakness surfaces a linked exercise"
      const opportunities = await ApiClient.getLearningOpportunities(game.id);
      setLessons(opportunities);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error occurred');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="pgn-import" data-testid="pgn-import">
      <h2>Import Game</h2>
      <textarea
        value={pgn}
        onChange={(e) => setPgn(e.target.value)}
        placeholder="Paste PGN here..."
        rows={10}
        cols={50}
        data-testid="pgn-textarea"
      />
      <br />
      <button onClick={handleImport} disabled={loading || !pgn} data-testid="import-btn">
        {loading ? 'Importing...' : 'Import PGN'}
      </button>

      {error && <div className="error">{error}</div>}

      {lessons.length > 0 && (
        <div data-testid="lessons-list">
          <h3>Learning Opportunities Found</h3>
          <ul>
            {lessons.map(lesson => (
              <li key={lesson.lesson_id}>
                {lesson.diagnosis.primary} (confidence: {lesson.diagnosis.confidence})
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
