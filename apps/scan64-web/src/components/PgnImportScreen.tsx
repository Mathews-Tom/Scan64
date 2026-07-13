import { useState, useRef, useEffect } from 'react';
import { ApiClient } from '../api/client';
import type { LessonSpec } from '../api/types';
import { CriticalMomentReview } from './CriticalMomentReview';

interface PgnImportScreenProps {
  onExploreAnalysis?: (gameId: string) => void;
}


export function PgnImportScreen({ onExploreAnalysis }: PgnImportScreenProps) {
  const [pgn, setPgn] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  const [lessons, setLessons] = useState<LessonSpec[]>([]);
  const [selectedLesson, setSelectedLesson] = useState<LessonSpec | null>(null);
  const [analyzedGameId, setAnalyzedGameId] = useState<string | null>(null);
  const handleImport = async () => {
    if (!pgn.trim()) return;
    
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    const { signal } = abortController;

    setLoading(true);
    setError(null);
    setStatusText('Creating game...');
    setLessons([]);
    
    try {
      const game = await ApiClient.createGame({ pgn });
      if (signal.aborted) return;

      setStatusText('Starting analysis job...');
      const job = await ApiClient.createAnalysisJob(game.id);
      if (signal.aborted) return;

      let currentJob = job;
      while (currentJob.status !== 'completed' && currentJob.status !== 'failed') {
        setStatusText(`Analyzing game (status: ${currentJob.status})...`);
        const { promise, resolve } = Promise.withResolvers<void>();
        const timeoutId = setTimeout(resolve, 1000);
        
        const abortHandler = () => {
          clearTimeout(timeoutId);
          resolve();
        };
        
        signal.addEventListener('abort', abortHandler, { once: true });
        await promise;
        signal.removeEventListener('abort', abortHandler);
        
        if (signal.aborted) return;

        currentJob = await ApiClient.getAnalysisJob(job.id);
        if (signal.aborted) return;
      }

      if (currentJob.status === 'failed') {
        throw new Error('Analysis job failed');
      }

      setStatusText('Fetching learning opportunities...');
      const opportunities = await ApiClient.getLearningOpportunities(game.id);
      if (signal.aborted) return;

      const verifiedLessons = opportunities.filter(op => op.verification?.status === 'verified' || !op.verification);
      setAnalyzedGameId(game.id);
      setLessons(verifiedLessons);
      setStatusText(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error occurred');
    } finally {
      if (!signal.aborted) {
        setLoading(false);
        setStatusText(null);
      }
    }
  };

  if (selectedLesson) {
    return (
      <div>
        <button onClick={() => setSelectedLesson(null)}>Back to Import</button>
        <CriticalMomentReview lesson={selectedLesson} />
      </div>
    );
  }

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
      <button onClick={handleImport} disabled={loading || !pgn.trim()} data-testid="import-btn">
        {loading ? 'Importing...' : 'Import PGN'}
      </button>
      
      {statusText && <div className="status" data-testid="import-status">{statusText}</div>}

      {error && <div className="error" data-testid="import-error">{error}</div>}


      {analyzedGameId && onExploreAnalysis && (
        <button
          data-testid="explore-analysis-btn"
          onClick={() => onExploreAnalysis(analyzedGameId)}
        >
          Explore analysis board
        </button>
      )}
      {lessons.length > 0 && (
        <div data-testid="lessons-list">
          <h3>Learning Opportunities Found</h3>
          <ul>
            {lessons.map(lesson => (
              <li key={lesson.lesson_id}>
                {lesson.diagnosis.primary} (confidence: {lesson.diagnosis.confidence})
                <button 
                  data-testid={`review-btn-${lesson.lesson_id}`}
                  onClick={() => setSelectedLesson(lesson)}
                >
                  Review
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
