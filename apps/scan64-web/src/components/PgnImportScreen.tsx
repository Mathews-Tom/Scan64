import { useState, useRef, useEffect } from 'react';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId, getStoredPlayerId } from '../api/client';
import type { LessonSpec, OnlineGameImportResponse, OnlineGameSource } from '../api/types';
import { CriticalMomentReview } from './CriticalMomentReview';
import { diagnosisName } from './diagnosisName';
import { Button, Panel } from './Primitives';

interface PgnImportScreenProps {
  onExploreAnalysis?: (gameId: string) => void;
  onOpenGame?: (gameId: string) => void;
}


export function PgnImportScreen({ onExploreAnalysis, onOpenGame }: PgnImportScreenProps) {
  const [source, setSource] = useState<OnlineGameSource>('lichess');
  const [username, setUsername] = useState('');
  const [maxGames, setMaxGames] = useState('10');
  const [onlinePending, setOnlinePending] = useState(false);
  const onlineInFlight = useRef(false);
  const [onlineError, setOnlineError] = useState<string | null>(null);
  const [onlineResult, setOnlineResult] = useState<OnlineGameImportResponse | null>(null);
  const handleOnlineImport = async () => {
    if (onlineInFlight.current) return;
    const sourceName = source === 'lichess' ? 'Lichess' : 'Chess.com';
    setOnlineError(null);
    setOnlineResult(null);
    if (!/^[A-Za-z0-9_-]{2,30}$/.test(username)) {
      setOnlineError(`${sourceName}: Enter a username of 2–30 letters, digits, underscores or hyphens, not a URL.`);
      return;
    }
    const count = Number(maxGames);
    if (!/^\d+$/.test(maxGames) || !Number.isInteger(count) || count < 1 || count > 50) {
      setOnlineError('Enter a whole number of games from 1 to 50.');
      return;
    }
    onlineInFlight.current = true;
    setOnlinePending(true);
    try {
      await ensurePlayerAuthorization(getOrCreatePlayerId());
      setOnlineResult(await ApiClient.importOnlineGames({ source, username, max_games: count }));
    } catch (error: unknown) {
      setOnlineError(`${sourceName}: ${error instanceof Error ? error.message : 'Import failed. Please try again.'}`);
    } finally {
      onlineInFlight.current = false;
      setOnlinePending(false);
    }
  };
  const [pgn, setPgn] = useState('');
  const [ownerColor, setOwnerColor] = useState<'white' | 'black'>('white');
  const [displayName, setDisplayName] = useState<string | null>(null);
  const sideChosenManually = useRef(false);

  useEffect(() => {
    const playerId = getStoredPlayerId();
    if (playerId === null) return;
    void ApiClient.getPlayerProfile(playerId).then(profile => setDisplayName(profile.display_name)).catch(() => {});
  }, []);

  useEffect(() => {
    if (sideChosenManually.current) return;
    const playerId = getStoredPlayerId();
    const names = playerId === null ? [] : [playerId];
    if (displayName && displayName !== 'Anonymous') names.push(displayName);
    const matches = (['white', 'black'] as const).filter(color => {
      const header = color === 'white' ? 'White' : 'Black';
      const name = pgn.match(new RegExp(`\\[${header}\\s+"([^"]*)"\\]`))?.[1];
      return name !== undefined && names.includes(name);
    });
    setOwnerColor(matches.length === 1 ? matches[0] : 'white');
  }, [pgn, displayName]);
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
  const [lessonSessionId, setLessonSessionId] = useState<string | null>(null);
  const [analyzedGameId, setAnalyzedGameId] = useState<string | null>(null);
  const reviewContainer = useRef<HTMLDivElement>(null);
  const reviewOpener = useRef<string | null>(null);
  const reviewButtons = useRef(new Map<string, HTMLButtonElement>());
  useEffect(() => {
    if (selectedLesson) reviewContainer.current?.focus();
    else if (reviewOpener.current) reviewButtons.current.get(reviewOpener.current)?.focus();
  }, [selectedLesson]);
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
      let playerId = getOrCreatePlayerId();
      playerId = await ensurePlayerAuthorization(playerId);
      const game = await ApiClient.createGame({ pgn, player_id: playerId, owner_color: ownerColor });
      if (signal.aborted) return;

      setStatusText('Starting analysis job...');
      const job = await ApiClient.createAnalysisJob(game.id);
      if (signal.aborted) return;

      const currentJob = job.status === 'completed' || job.status === 'failed' ? job
        : await ApiClient.watchAnalysisJob(job.id, next => {
          if (!signal.aborted) setStatusText(`Analyzing game (status: ${next.status})...`);
        }, signal);
      if (signal.aborted) return;

      if (currentJob.status === 'failed') {
        throw new Error('Analysis job failed');
      }

      setStatusText('Fetching learning opportunities...');
      const learningSession = await ApiClient.getLearningOpportunities(game.id);
      if (signal.aborted) return;

      const verifiedLessons = learningSession.lessons.filter(
        opportunity => opportunity.verification?.status === 'verified' || !opportunity.verification,
      );
      setLessonSessionId(learningSession.session_id);
      setAnalyzedGameId(game.id);
      setLessons(verifiedLessons);
      setStatusText(null);
    } catch (e: unknown) {
      if (!signal.aborted) setError(e instanceof Error ? e.message : 'Unknown error occurred');
    } finally {
      if (!signal.aborted) {
        setLoading(false);
        setStatusText(null);
      }
    }
  };

  if (selectedLesson !== null && lessonSessionId !== null) {
    return (
      <div className="lesson-study working-screen" ref={reviewContainer} tabIndex={-1} role="region" aria-label="Imported game lesson" onKeyDown={event => {
        if (event.key === 'Escape' && !event.defaultPrevented) setSelectedLesson(null);
      }}>
        <button onClick={() => setSelectedLesson(null)}>Back to Import</button>
        <CriticalMomentReview
          lesson={selectedLesson}
          opportunityId={selectedLesson.lesson_id}
          sessionId={lessonSessionId}
          onComplete={() => setSelectedLesson(null)}
        />
      </div>
    );
  }

  return (
    <section className="pgn-import working-screen" data-testid="pgn-import">
      <h1>Import game</h1>
      <Panel className="import-controls" aria-label="Import public online games">
        <h2>Import public online games</h2>
        <p>Fetch recent standard games only when you submit. Your side is matched to the username; analysis is queued automatically.</p>
        <form noValidate onSubmit={event => { event.preventDefault(); void handleOnlineImport(); }}>
          <fieldset disabled={onlinePending}>
            <legend>Online source</legend>
            <label htmlFor="online-source">Source</label>
            <select id="online-source" value={source} onChange={event => setSource(event.target.value as OnlineGameSource)}>
              <option value="lichess">Lichess</option>
              <option value="chesscom">Chess.com</option>
            </select>
            <label htmlFor="online-username">Username</label>
            <input id="online-username" value={username} onChange={event => setUsername(event.target.value)} autoCapitalize="none" autoCorrect="off" spellCheck={false} aria-describedby="online-privacy" />
            <label htmlFor="online-count">Number of games</label>
            <input id="online-count" type="number" min={1} max={50} step={1} value={maxGames} onChange={event => setMaxGames(event.target.value)} />
            <p id="online-privacy">On submit, Scan64 sends this username and requested count to lichess.org or api.chess.com. Public games only; no OAuth, private games or background sync.</p>
            <Button type="submit" disabled={onlinePending}>{onlinePending ? 'Importing online games…' : 'Import online games'}</Button>
          </fieldset>
        </form>
        {onlinePending && <p role="status">Fetching public games and queuing analysis…</p>}
        {onlineError && <p className="error" role="alert">{onlineError}</p>}
        {onlineResult && (
          <>
            <div role="status">
              <p>{onlineResult.imported_count} new · {onlineResult.duplicate_count} duplicate · {onlineResult.skipped_count} skipped</p>
              <p>{onlineResult.message}</p>
            </div>
            <ul>
              {onlineResult.games.map(game => (
                <li key={game.id}>
                  {game.white} vs {game.black} · {game.result} · Your side: {game.owner_color}
                  {onOpenGame && <Button variant="secondary" onClick={() => onOpenGame(game.id)} aria-label={`Review ${game.white} vs ${game.black}`}>Review</Button>}
                </li>
              ))}
            </ul>
          </>
        )}
      </Panel>
      <Panel className="import-controls" aria-label="Import your game">
      <label>Your side
        <select aria-label="Your side" value={ownerColor} onChange={e => { sideChosenManually.current = true; setOwnerColor(e.target.value as 'white' | 'black'); }}>
          <option value="white">White</option>
          <option value="black">Black</option>
        </select>
      </label>
      <label htmlFor="import-pgn">PGN game text</label>
      <textarea
        id="import-pgn"
        value={pgn}
        onChange={(e) => setPgn(e.target.value)}
        placeholder="Paste PGN here..."
        rows={10}
        cols={50}
        data-testid="pgn-textarea"
      />
      <Button variant={analyzedGameId ? 'secondary' : 'primary'} onClick={handleImport} disabled={loading || !pgn.trim()} data-testid="import-btn">
        {loading ? 'Importing...' : 'Import PGN'}
      </Button>
      
      {statusText && <div className="status" role="status" data-testid="import-status">{statusText}</div>}

      {error && <div className="error" role="alert" data-testid="import-error">{error}</div>}


      {analyzedGameId && onExploreAnalysis && (
        <Button variant="primary"
          data-testid="explore-analysis-btn"
          onClick={() => onExploreAnalysis(analyzedGameId)}
        >
          Explore analysis board
        </Button>
      )}
      </Panel>
      {lessons.length > 0 && (
        <Panel data-testid="lessons-list">
          <h2>Learning opportunities found</h2>
          <ul>
            {lessons.map(lesson => (
              <li key={lesson.lesson_id}>
                {lesson.diagnosis ? (
                  <>
                    {lesson.diagnosis.primary === 'threat_processing.allowed_mate'
                      ? diagnosisName(lesson.diagnosis.primary)
                      : lesson.diagnosis.primary}
                    {` (confidence: ${lesson.diagnosis.confidence})`}
                  </>
                ) : lesson.objective.instruction}
                <button 
                  ref={button => { if (button) reviewButtons.current.set(lesson.lesson_id, button); else reviewButtons.current.delete(lesson.lesson_id); }}
                  data-testid={`review-btn-${lesson.lesson_id}`}
                  onClick={() => { reviewOpener.current = lesson.lesson_id; setSelectedLesson(lesson); }}
                >
                  Review
                </button>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </section>
  );
}
