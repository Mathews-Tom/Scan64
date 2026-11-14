import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiClient, PuzzleConflictError } from '../api/client';
import type { PuzzleMode, PuzzleModesRead, PuzzleMistakesRead, PuzzleMistakeStartRead, PuzzleRunCreate, PuzzleRunRead, PuzzleStartRead } from '../api/types';
import { CoachBoard } from './CoachBoard';
import { CriticalMomentReview } from './CriticalMomentReview';
import './PuzzlesScreen.css';

const IMPORT_COMMAND = 'uv run scan64-cli puzzles import /path/to/lichess_db_puzzle.csv.zst --json';

export function PuzzlesScreen() {
  const [distribution, setDistribution] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void ApiClient.getAppInfo().then(info => {
      if (!cancelled) setDistribution(info.distribution);
    }).catch(() => { if (!cancelled) setDistribution('unavailable'); });
    return () => { cancelled = true; };
  }, []);
  const [practiceRequest] = useState(() => new URLSearchParams(window.location.search));
  const practiceStarted = useRef(false);
  const [patternUnavailable, setPatternUnavailable] = useState(false);
  const [relatedTheme, setRelatedTheme] = useState<string | null>(null);
  const [run, setRun] = useState<PuzzleRunRead | null>(null);
  const [catalog, setCatalog] = useState<PuzzleStartRead | null>(null);
  const [pending, setPending] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState('Loading puzzle…');
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [mode, setMode] = useState<PuzzleMode | 'mistakes'>('daily');
  const [theme, setTheme] = useState('');
  const [modes, setModes] = useState<PuzzleModesRead | null>(null);
  const [modesError, setModesError] = useState<string | null>(null);
  const [mistakes, setMistakes] = useState<PuzzleMistakesRead | null>(null);
  const [mistake, setMistake] = useState<(PuzzleMistakeStartRead & { game_id: string }) | null>(null);
  const [openedMistakes, setOpenedMistakes] = useState<(PuzzleMistakeStartRead & { game_id: string })[]>([]);
  const retryPanel = useRef<HTMLDivElement>(null);
  const retryButton = useRef<HTMLButtonElement | null>(null);
  const mistakeHeading = useRef<HTMLHeadingElement>(null);
  const puzzleHeading = useRef<HTMLHeadingElement>(null);
  const previousRunState = useRef<PuzzleRunRead['state'] | undefined>(undefined);
  const busy = useRef(false);
  const generation = useRef(0);

  const start = useCallback(async (body?: PuzzleRunCreate, related = false) => {
    if (busy.current) return;
    busy.current = true;
    const request = generation.current;
    setPending(true); setError(null);
    if (body) {
      setRelatedTheme(related && body.mode === 'theme' ? body.theme ?? null : null);
      setPatternUnavailable(false);
    }
    try {
      const result = body ? await ApiClient.startPuzzleRun(body) : await ApiClient.getActivePuzzleRun();
      if (request !== generation.current) return;
      setCatalog(result); setRun(result.run); setNeedsRefresh(false);
      if (!result.run && related) setPatternUnavailable(true);
      if (result.run) {
        setMode(result.run.mode);
        if (result.run.theme) setTheme(result.run.theme);
      }
      setNotice(result.message);
    } catch (failure) {
      if (request === generation.current) {
        setNotice('');
        setError(failure instanceof Error ? failure.message : 'Could not load puzzle.');
        if (related) setPatternUnavailable(true);
      }
    } finally {
      if (request === generation.current) { busy.current = false; setPending(false); }
    }
  }, []);

  useEffect(() => {
    void start();
    void ApiClient.getPuzzleModes().then(result => {
      setModes(result);
      setTheme(current => current || result.themes[0] || '');
    }).catch(failure => setModesError(failure instanceof Error ? failure.message : 'Could not load practice modes.'));
    return () => { generation.current += 1; busy.current = false; };
  }, [start]);

  const mutate = async (action: 'move' | 'hint' | 'abandon', move?: string) => {
    if (!run || run.state !== 'active' || busy.current || needsRefresh) return;
    busy.current = true;
    const request = generation.current;
    setPending(true); setError(null);
    const expected = { expected_cursor: run.cursor, expected_revision: run.revision };
    try {
      const next = action === 'move'
        ? await ApiClient.submitPuzzleMove(run.run_id, { ...expected, move: move! })
        : action === 'hint'
          ? await ApiClient.revealPuzzleHint(run.run_id, { ...expected, step: Math.min(3, run.hint_step + 1) as 1 | 2 | 3 })
          : await ApiClient.abandonPuzzleRun(run.run_id, expected);
      if (request !== generation.current) return;
      setRun(next);
      const committed = next.played_moves.slice(run.played_moves.length);
      const moveAnnouncements = committed.map((uci, index) => {
        const white = (run.fen.split(' ')[1] === 'w') !== (index % 2 === 1);
        const promoted = uci[4] ? `, promoting to ${{ q: 'queen', r: 'rook', b: 'bishop', n: 'knight' }[uci[4] as 'q' | 'r' | 'b' | 'n']}` : '';
        return `${white ? 'White' : 'Black'} played ${uci.slice(0, 2)} to ${uci.slice(2, 4)}${promoted}.`;
      }).join(' ');
      setNotice(action === 'move' && next.wrong_attempts > run.wrong_attempts
        ? next.state === 'active' ? 'That move is not the source continuation. Try again.' : 'That move is not the source continuation.'
        : action === 'move' ? `${moveAnnouncements}${next.state === 'active' ? ' Source move accepted. Continue the puzzle.' : ''}` : '');
    } catch (failure) {
      if (request !== generation.current) return;
      if (failure instanceof PuzzleConflictError) {
        setRun(failure.run);
        setNotice(`${failure.message} Latest puzzle state loaded; your action was not replayed.`);
      } else {
        setError(failure instanceof Error ? failure.message : 'Puzzle request failed.');
        setNeedsRefresh(true);
      }
    } finally {
      if (request === generation.current) { busy.current = false; setPending(false); }
    }
  };

  const refresh = async () => {
    if (!run || busy.current) return;
    busy.current = true;
    const request = generation.current;
    setPending(true); setError(null);
    try {
      const next = await ApiClient.getPuzzleRun(run.run_id);
      if (request !== generation.current) return;
      setRun(next); setNeedsRefresh(false);
      setNotice('Latest puzzle state loaded. Choose your next action.');
    } catch (failure) {
      if (request === generation.current) setError(failure instanceof Error ? failure.message : 'Could not refresh puzzle.');
    } finally {
      if (request === generation.current) { busy.current = false; setPending(false); }
    }
  };

  const loadMistakes = async (offset = 0) => {
    if (busy.current) return;
    busy.current = true; setPending(true); setError(null);
    const request = generation.current;
    try {
      const result = await ApiClient.getPuzzleMistakes(offset);
      if (request === generation.current) setMistakes(result);
    } catch (failure) {
      if (request === generation.current) setError(failure instanceof Error ? failure.message : 'Could not load your mistakes.');
    } finally {
      if (request === generation.current) { busy.current = false; setPending(false); }
    }
  };

  const startMistake = useCallback(async (item: { lesson_id: string; game_id: string }) => {
    if (busy.current || run?.state === 'active') return;
    busy.current = true; setPending(true); setError(null);
    const request = generation.current;
    try {
      const result = await ApiClient.startPuzzleMistake(item.lesson_id);
      if (request === generation.current) {
        const opened = { ...result, game_id: item.game_id };
        setMistake(opened);
        setOpenedMistakes(current => current.some(entry => entry.moment.lesson_id === item.lesson_id) ? current : [...current, opened]);
      }
    } catch (failure) {
      if (request === generation.current) {
        setError(failure instanceof Error ? failure.message : 'Could not start this mistake.');
        if (practiceRequest.get('lesson') === item.lesson_id) {
          try {
            const available = await ApiClient.getPuzzleModes();
            const fallback = practiceRequest.getAll('fallbackTheme').find(theme => available.themes.includes(theme));
            if (request !== generation.current) return;
            if (fallback) {
              busy.current = false;
              await start({ mode: 'theme', theme: fallback }, true);
            } else setPatternUnavailable(true);
          } catch { if (request === generation.current) setPatternUnavailable(true); }
        }
      }
    } finally {
      if (request === generation.current) { busy.current = false; setPending(false); }
    }
  }, [run?.state, practiceRequest, start]);

  useEffect(() => {
    if (mistake) retryPanel.current?.focus();
    else (retryButton.current?.isConnected ? retryButton.current : mistakeHeading.current)?.focus();
  }, [mistake]);

  const chooseMode = (next: PuzzleMode | 'mistakes') => {
    if (busy.current || run?.state === 'active') return;
    setMode(next); setMistake(null); setError(null); setNotice('');
    setPatternUnavailable(false);
    if (next === 'mistakes') void loadMistakes();
  };

  useEffect(() => {
    if (pending || practiceStarted.current || !catalog) return;
    const lessonId = practiceRequest.get('lesson');
    const gameId = practiceRequest.get('game');
    const relatedTheme = practiceRequest.get('theme');
    if (!lessonId && !relatedTheme) return;
    if (run?.state === 'active') return;
    practiceStarted.current = true;
    if (lessonId && gameId) {
      setMode('mistakes');
      void startMistake({ lesson_id: lessonId, game_id: gameId });
    } else if (relatedTheme) {
      setMode('theme'); setTheme(relatedTheme);
      void start({ mode: 'theme', theme: relatedTheme }, true);
    }
  }, [pending, catalog, practiceRequest, run?.state, start, startMistake]);

  const startSelected = () => mode !== 'mistakes'
    ? start({ mode, ...(mode === 'theme' ? { theme } : {}) }) : loadMistakes(mistakes?.offset ?? 0);

  useEffect(() => {
    if (previousRunState.current === 'active' && run?.state !== 'active') puzzleHeading.current?.focus();
    previousRunState.current = run?.state;
  }, [run?.state]);
  // Undo the parity of already played plies to keep the initial solver side on screen.
  const blackToMove = run?.fen.split(' ')[1] === 'b';
  const solverBlack = run ? blackToMove !== (run.played_moves.length % 2 === 1) : false;
  const active = run?.state === 'active';
  const latestHint = run?.hints.at(-1);
  const hintSlots = [1, 2, 3].map(step => run?.hints.find(hint => hint.step === step));
  const daily = run?.daily ?? catalog?.daily;
  const streak = run?.streak ?? catalog?.streak ?? modes?.streak;
  return <section className="puzzles-screen" aria-labelledby="puzzles-title" aria-busy={pending}>
    <h1 id="puzzles-title">Puzzles</h1>
    <p>Practise the complete imported source continuation. The opponent’s setup move is already played.</p>
    <p className="muted">Lichess CC0 source-verified practice: the source line is legal, not an engine judgement of move quality. Puzzle rating is separate from chess strength and does not award theme mastery.</p>
    <fieldset className="puzzles-modes" disabled={pending || active}>
      <legend>Practice mode</legend>
      {([['daily', 'Daily'], ['theme', 'Theme'], ['mistakes', 'Your mistakes'], ['streak', 'Solve streak']] as const).map(([value, label]) =>
        <label key={value}><input type="radio" name="puzzle-mode" checked={mode === value} onChange={() => chooseMode(value)} /> {label}</label>)}
      {mode === 'theme' ? <label>Theme
        <select value={theme} onChange={event => setTheme(event.target.value)} disabled={!modes?.themes.length}>
          {!modes?.themes.length ? <option value="">No themes available</option> : null}
          {modes?.themes.map(value => <option key={value} value={value}>{value}</option>)}
        </select>
      </label> : null}
      {mode !== 'mistakes' ? <button disabled={mode === 'theme' && !theme} onClick={() => void startSelected()}>Start {mode === 'streak' ? 'solve streak' : mode}</button> : null}
    </fieldset>
    {run?.mode === 'theme' && run.theme === relatedTheme ? <p>Related unrated theme practice, not diagnosis mastery.</p> : null}
    {patternUnavailable ? <p role="status">Related practice is unavailable. <a href="/puzzles">Daily puzzles — may be unrelated</a>. Today’s set may contain no related puzzle.</p> : null}
    {active ? <p>Finish or abandon the active puzzle before switching modes.</p> : null}
    {modesError ? <p role="alert">{modesError}</p> : null}
    <p>Theme, Your mistakes and Solve streak are unrated. Mode-used puzzles permanently cease to be rated first exposures or weak/exploration candidates; future Daily supply may widen or be shorter.</p>
    {streak ? <section aria-label="Solve streak progress" className="study-panel">
      <h2>Solve streak</h2><p>Current: {streak.current} · Best: {streak.best}</p>
      {streak.reset_reason ? <p>Reset reason: {streak.reset_reason}</p> : null}
      <p>Only finished Solve streak puzzles count. Unhinted solves add one; hints, failure or abandonment reset the streak. Within-puzzle mistakes are allowed. Daily and Theme do not change it.</p>
    </section> : null}
    {mode === 'mistakes' && !active ? <section className="study-panel" aria-label="Your mistakes">
      <h2 ref={mistakeHeading} tabIndex={-1}>Your mistakes</h2>
      <p>Hints are app-reported. Existing lesson hints and assisted-credit rules apply; lifetime attempt budgets do not reset.</p>
      {mistakes ? <>
        <p>Excluded on this page: no review schedule {mistakes.excluded.no_review_schedule}; retired {mistakes.excluded.retired}; untrusted {mistakes.excluded.untrusted}; exhausted {mistakes.excluded.exhausted}.</p>
        {!mistakes.items.length ? <p>No eligible mistakes available.</p> : <ul>{mistakes.items.map(item => <li key={item.lesson_id}>
          <a href={`/games/${encodeURIComponent(item.game_id)}/analysis?ply=${item.ply}`}>Game {item.game_id}, ply {item.ply}</a>
          <span> · {item.attempts_remaining} attempts remaining </span>
          <button disabled={pending || item.attempts_remaining === 0}
            aria-expanded={mistake?.moment.lesson_id === item.lesson_id}
            aria-controls={openedMistakes.some(entry => entry.moment.lesson_id === item.lesson_id) ? `mistake-${item.lesson_id}` : undefined}
            onClick={event => { retryButton.current = event.currentTarget; void startMistake(item); }}>Start mistake at ply {item.ply}</button>
        </li>)}</ul>}
        <div className="puzzles-actions">
          <button disabled={pending || mistakes.offset === 0} onClick={() => void loadMistakes(Math.max(0, mistakes.offset - mistakes.limit))}>Previous mistakes</button>
          <button disabled={pending || !mistakes.has_more} onClick={() => void loadMistakes(mistakes.offset + mistakes.limit)}>More mistakes</button>
        </div>
      </> : null}
    </section> : null}
    {openedMistakes.map(entry => <div key={entry.moment.lesson_id}
      id={`mistake-${entry.moment.lesson_id}`}
      ref={mistake?.moment.lesson_id === entry.moment.lesson_id ? retryPanel : undefined}
      role="region" aria-label={`Retry mistake at ply ${entry.moment.ply}`} tabIndex={-1}
      hidden={mode !== 'mistakes' || mistake?.moment.lesson_id !== entry.moment.lesson_id}
      onKeyDown={event => { if (event.key === 'Escape' && !event.defaultPrevented) setMistake(null); }}>
      <button onClick={() => setMistake(null)}>Close retry</button>
      <CriticalMomentReview lesson={entry.moment.lesson}
        opportunityId={entry.moment.lesson_id} sessionId={entry.session_id}
        initialAttemptsUsed={entry.moment.attempts_used} initialAttemptsRemaining={entry.moment.attempts_remaining}
        onAttemptRecorded={used => setMistakes(current => current ? { ...current, items: current.items.map(item =>
          item.lesson_id === entry.moment.lesson_id ? { ...item, attempts_remaining: Math.max(0, entry.moment.attempts_used + entry.moment.attempts_remaining - used) } : item) } : null)}
        onComplete={() => setMistake(null)} />
    </div>)}
    {daily ? <section className="study-panel" aria-label="Daily puzzle practice">
      <h2>Daily puzzle practice · {daily.utc_day} UTC</h2>
      <p>Scan64 puzzle rating: {daily.rating}{daily.provisional ? ' (provisional)' : ''} · {daily.rated_count} rated puzzles</p>
      <p>{daily.completed} of {daily.total} completed · {daily.remaining} remaining · {daily.overdue_count} overdue reviews</p>
      <p>Selected mix: {daily.actual_mix.due} due reviews, {daily.actual_mix.weak} related to weaknesses, {daily.actual_mix.exploration} exploration.</p>
      <p>{daily.message}</p>
      <p>Up to 10 puzzles per UTC day. Refreshing or choosing Next does not regenerate or skip the set. An active puzzle resumes first, even after UTC midnight.</p>
    </section> : null}
    <p>On a rated first exposure, an independent solve wins rating; hints, failure or abandonment lose rating. Mistakes within the three-attempt limit are allowed. Reviews and repeats are unrated.</p>
    <div role="status" aria-label="Puzzle progress" aria-live="polite">
      {pending ? <p>Loading puzzle state…</p> : null}
      {notice ? <p>{notice}</p> : null}
      {run ? <>
        <p>{Math.max(0, run.maximum_wrong_attempts - run.wrong_attempts)} wrong attempts remaining</p>
        {run.state === 'solved' ? <p>Puzzle solved. You completed the source continuation.</p> : null}
        {run.state === 'failed' ? <p>Puzzle failed. All wrong attempts have been used.</p> : null}
        {run.state === 'abandoned' ? <p>Puzzle abandoned. No content success or failure attempt was recorded; a rated first exposure counts as a rating loss.</p> : null}
      </> : null}
    </div>
    {error ? <div role="alert"><p>{error} Please retry loading the server state before continuing.</p>
      <button disabled={pending} onClick={() => void (mode === 'mistakes' ? loadMistakes(mistakes?.offset ?? 0) : run ? refresh() : start())}>Retry</button>
    </div> : null}
    {!active && mode !== 'mistakes' && !run && catalog && (catalog.status === 'empty' || catalog.status === 'exhausted') ? <div className="study-panel">
      <p>{catalog.status === 'empty' ? 'No imported puzzles are available.' : mode === 'daily' ? 'No more puzzles are available in today’s set. The next daily set resets at UTC midnight.' : 'This practice mode is exhausted. No unseen eligible puzzles remain.'}</p>
      {catalog.status === 'empty' ? <>
        <PuzzleImportInstructions distribution={distribution} />
        <p>Nothing downloads automatically.</p>
      </> : null}
      <button disabled={pending || (mode === 'theme' && !theme)} onClick={() => void startSelected()}>Check for puzzles</button>
    </div> : null}
    {run && (active || mode === run.mode) ? <div className="puzzles-layout">
      <CoachBoard key={run.run_id} fen={run.fen} orientation={solverBlack ? 'black' : 'white'}
        disabled={pending || !active || needsRefresh} resetAfterAttempt testId="puzzle-board"
        onMove={(move) => mutate('move', move)} visualizations={latestHint?.visualizations} />
      <div className="study-panel">
        <h2 ref={puzzleHeading} tabIndex={-1}>Source puzzle</h2>
        <p>Puzzle: {run.puzzle_id}</p>
        <p>Selection: {run.selection_reason === 'due' ? 'Due review' : run.selection_reason === 'weak' ? 'Related to recurring weaknesses (theme heuristic, not measured mastery)' : run.selection_reason === 'exploration' ? 'Exploration' : 'Resumed earlier practice'}</p>
        <p>Mode: {run.mode}{run.theme ? ` · ${run.theme}` : ''}</p>
        {run.widening ? <p>Selection range: {run.widening === '200' ? 'Within ±200 of your puzzle rating' : run.widening === '400' ? 'Widened to ±400 of your puzzle rating' : run.widening === 'catalog' ? 'Widened to the available catalog' : 'Scheduled due review'}</p> : null}
        <p>{run.rated ? 'Rated first exposure' : run.mode !== 'daily' ? 'Unrated mode practice' : 'Unrated review, repeat or earlier run'}</p>
        <p>Setup move (UCI): {run.setup}</p>
        <p>Played continuation (UCI): {run.played_moves.length ? run.played_moves.join(' ') : 'No solver moves yet.'}</p>
        <section aria-label="Puzzle hints">
          <button disabled={pending || !active || needsRefresh || run.hint_step >= 3} onClick={() => void mutate('hint')}>Show hint</button>
          <p>Hint {run.hint_step} of 3. The third hint can reveal the source line.</p>
          {hintSlots.map((hint, index) => <div key={index} role="status" aria-atomic="true">
            {hint ? <><h3>Hint {hint.step}</h3>
            {hint.step !== 3 ? <p>{hint.text}</p> : <ol>{hint.candidates.map(candidate => <li key={candidate.move}>
              <strong>Source continuation: {candidate.san}</strong>
              <p>Source line (UCI): {candidate.line.join(' ')}</p>
              {candidate.reasons.length ? <ul>{candidate.reasons.map((reason, index) => <li key={index}><strong>{reason.tier === 'principle' ? 'Principle' : 'Tactical'}:</strong> {reason.description}</li>)}</ul> : <p>Source continuation; no engine-quality claim.</p>}
            </li>)}</ol>}</> : null}
          </div>)}
        </section>
        <div className="puzzles-actions">
          {active ? <button disabled={pending || needsRefresh} onClick={() => void mutate('abandon')}>Abandon puzzle</button>
            : <button disabled={pending} onClick={() => void startSelected()}>Next puzzle</button>}
        </div>
      </div>
    </div> : null}
  </section>;
}

export function PuzzleImportInstructions({ distribution }: { distribution: string | null }) {
  if (distribution === null) return <p>Loading import instructions…</p>;
  if (distribution === 'unavailable') return <p>Application information unavailable. Reload to see import instructions.</p>;
  return distribution === 'desktop'
    ? <p>Use Import puzzles… in the Scan64 window.</p>
    : <><p>Ask the operator to import puzzles into the application database:</p><code>{IMPORT_COMMAND}</code></>;
}
