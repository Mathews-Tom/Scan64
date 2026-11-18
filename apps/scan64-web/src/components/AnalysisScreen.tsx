import { useEffect, useMemo, useRef, useState } from 'react';
import { Chess } from 'chess.js';
import { ApiClient, ApiRequestError, ensurePlayerAuthorization, getOrCreatePlayerId } from '../api/client';
import { DEFAULT_BOT_LEVEL } from '../api/opponent';
import type { PlaySessionRead, PositionAnalysisLine } from '../api/types';
import { OwnedGameReview } from './OwnedGameReview';
import { CoachBoard } from './CoachBoard';
import { analysisEntries, analysisMoveLabel, analysisReplay, commitAnalysisMoves, createAnalysisTree, exportAnalysisPgn, type AnalysisTree } from './analysisTree';
import { Button, Field, Panel } from './Primitives';
import './WorkingScreens.css';

interface AnalysisScreenProps {
  gameId?: string;
  initialPly?: number;
  onPlayFromHere?: (session: PlaySessionRead, fen: string) => void;
}

async function startPlayFromPosition(fen: string): Promise<PlaySessionRead> {
  const playerId = await ensurePlayerAuthorization(getOrCreatePlayerId());
  const game = await ApiClient.createGame({
    pgn: `[FEN "${fen}"]\n[SetUp "1"]\n\n`,
    player_id: playerId,
    owner_color: fen.split(' ')[1] === 'b' ? 'black' : 'white',
  });
  return ApiClient.createPlaySession({
    player_id: playerId,
    game_id: game.id,
    opponent_config: { provider: 'stockfish', bot_level: DEFAULT_BOT_LEVEL },
  });
}

export function AnalysisScreen({ gameId, initialPly, onPlayFromHere }: AnalysisScreenProps) {
  return gameId
    ? <OwnedGameReview key={gameId} gameId={gameId} initialPly={initialPly} onPlayFromHere={onPlayFromHere} startPlayFromPosition={startPlayFromPosition} />
    : <FreeAnalysisBoard onPlayFromHere={onPlayFromHere} />;
}

type Evaluation = { state: 'loading' } | { state: 'ready'; lines: PositionAnalysisLine[]; revision: number }
  | { state: 'error'; message: string; busy: boolean };

function evaluationLabel(line: PositionAnalysisLine): string {
  if (line.mate !== null) return `${line.mate < 0 ? '−' : '+'}M${Math.abs(line.mate)}`;
  if (line.score_cp === null) return 'Unscored';
  const score = line.score_cp / 100;
  return `${score >= 0 ? '+' : ''}${score.toFixed(2)}`;
}

function FreeAnalysisBoard({ onPlayFromHere }: Pick<AnalysisScreenProps, 'onPlayFromHere'>) {
  const fenField = useRef<HTMLInputElement>(null);
  const revision = useRef(0);
  const loadGeneration = useRef(0);
  const acceptedLoadRevision = useRef<number | null>(null);
  const [study, setStudy] = useState(() => ({ tree: createAnalysisTree(), selected: 0, revision: 0 }));
  const { tree, selected } = study;
  const fen = tree.nodes[selected].fen;
  const replay = useMemo(() => analysisReplay(tree, selected), [tree, selected]);
  const entries = useMemo(() => analysisEntries(tree), [tree]);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fenInput, setFenInput] = useState(fen);
  const [evaluation, setEvaluation] = useState<Evaluation>({ state: 'loading' });

  // Invalidate in the interaction itself, not just effect cleanup: A → B → A
  // must not allow the first A response to become current again.
  const selectPosition = (nextTree: AnalysisTree, next: number) => {
    revision.current += 1;
    loadGeneration.current += 1;
    setEvaluation({ state: 'loading' });
    setStudy({ tree: nextTree, selected: next, revision: revision.current });
    setFenInput(nextTree.nodes[next].fen);
    setError(null);
    setStatus('');
  };

  useEffect(() => () => { loadGeneration.current += 1; }, []);

  useEffect(() => {
    // Loading a FEN already obtained its evaluation; do not search twice.
    if (acceptedLoadRevision.current === study.revision) {
      acceptedLoadRevision.current = null;
      return;
    }
    let active = true;
    const requestedRevision = study.revision;
    const current = () => active && revision.current === requestedRevision;
    void (async () => {
      try {
        const playerId = await ensurePlayerAuthorization(getOrCreatePlayerId());
        if (!current()) return;
        const result = await ApiClient.analyzePosition({ player_id: playerId, fen });
        if (!current()) return;
        if (new Chess(result.fen).fen() !== fen) throw new Error('Engine returned a different position. Retry analysis.');
        setEvaluation({ state: 'ready', lines: result.lines, revision: requestedRevision });
      } catch (caught) {
        if (current()) setEvaluation({
          state: 'error',
          message: caught instanceof Error ? caught.message : 'Could not analyse this position.',
          busy: caught instanceof ApiRequestError && caught.status === 503,
        });
      }
    })();
    return () => { active = false; };
  }, [fen, study.revision]);

  const handleMoves = (moves: string[]) => {
    const next = commitAnalysisMoves(tree, selected, moves);
    selectPosition(next.tree, next.selected);
  };

  const handleLoadFen = async () => {
    const generation = ++loadGeneration.current;
    let nextTree: AnalysisTree;
    try {
      nextTree = createAnalysisTree(fenInput);
    } catch {
      setError('Invalid FEN');
      setStatus('');
      fenField.current?.focus();
      return;
    }
    const candidateFen = nextTree.nodes[0].fen;
    setError(null);
    setStatus('Validating FEN. The existing tree remains available.');
    const accept = (result: { state: 'ready'; lines: PositionAnalysisLine[] } | { state: 'error'; message: string; busy: boolean }) => {
      selectPosition(nextTree, 0);
      acceptedLoadRevision.current = revision.current;
      setEvaluation(result.state === 'ready' ? { ...result, revision: revision.current } : result);
      setStatus('Position loaded. Started a new variation tree.');
    };
    try {
      const playerId = await ensurePlayerAuthorization(getOrCreatePlayerId());
      if (loadGeneration.current !== generation) return;
      const result = await ApiClient.analyzePosition({ player_id: playerId, fen: candidateFen });
      if (loadGeneration.current !== generation) return;
      if (new Chess(result.fen).fen() !== candidateFen) throw new Error('Engine returned a different position. Try loading the FEN again.');
      accept({ state: 'ready', lines: result.lines });
    } catch (caught) {
      if (loadGeneration.current !== generation) return;
      // The endpoint validates the position before any pool/search 503.
      if (caught instanceof ApiRequestError && caught.status === 503) {
        accept({ state: 'error', message: caught.message, busy: true });
      } else {
        setStatus('');
        setError(caught instanceof ApiRequestError && caught.status === 422
          ? `Invalid FEN. ${caught.message}`
          : caught instanceof Error ? caught.message : 'Could not validate this FEN.');
        fenField.current?.focus();
      }
    }
  };

  const playFromHere = async () => {
    if (!onPlayFromHere) return;
    loadGeneration.current += 1;
    setLoading(true);
    setError(null);
    try {
      onPlayFromHere(await startPlayFromPosition(fen), fen);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to start game');
    } finally { setLoading(false); }
  };

  return <div className="analysis-screen working-screen">
    <h2>Analysis Board</h2>
    {error && <p role="alert">{error}</p>}
    <p role="status">{loading ? 'Loading...' : status}</p>
    <div className="board-workspace">
      <div className="board-workspace__board">
        <CoachBoard flippable editableReplay fen={tree.nodes[0].fen} pgn={replay.pgn}
          selectedPly={replay.selectedPly} onSelectedPlyChange={ply => selectPosition(tree, replay.path[ply])}
          onMove={uci => handleMoves([uci])} disabled={loading} testId="free-analysis-board" />
      </div>
      <Panel className="board-workspace__side working-controls" aria-label="Analysis controls">
        <Button variant="primary" onClick={playFromHere} disabled={loading || !onPlayFromHere} data-testid="play-from-here">{loading ? 'Starting...' : 'Play from here'}</Button>
        <section className="working-controls__section" aria-label="Variation tree">
          <h3>Variations</h3>
          <p>Return to any move to try another line. Existing continuations stay saved in this tree.</p>
          <div className="working-actions" role="group" aria-label="Replay navigation">
            <Button disabled={selected === 0} onClick={() => selectPosition(tree, 0)}>First</Button>
            <Button disabled={selected === 0} onClick={() => selectPosition(tree, tree.nodes[selected].parent!)}>Previous</Button>
            <Button disabled={!tree.nodes[selected].children.length} onClick={() => selectPosition(tree, tree.nodes[selected].children[0])}>Next</Button>
            <Button disabled={!tree.nodes[selected].children.length} onClick={() => selectPosition(tree, replay.path[replay.path.length - 1])}>Last</Button>
          </div>
          <ol className="analysis-variations" aria-label="Retained variations">
            {entries.map(({ id, depth }) => {
              const parent = tree.nodes[id].parent;
              const startsVariation = parent !== null && tree.nodes[parent].children[0] !== id;
              return <li key={id} style={{ paddingInlineStart: `${Math.min(depth, 4) * 0.75}rem` }}>
                {startsVariation && <span className="analysis-variation-origin">After {analysisMoveLabel(tree, parent!)}:</span>}
                <Button aria-current={selected === id ? 'step' : undefined} onClick={() => selectPosition(tree, id)}>
                  {depth > 0 ? 'Variation: ' : ''}{analysisMoveLabel(tree, id)}
                </Button>
              </li>;
            })}
          </ol>
        </section>
        <section className="working-controls__section">
          <h3>FEN setup</h3>
          <p>Loading a valid FEN starts a new tree and replaces all current variations.</p>
          <Field ref={fenField} label="FEN" id="analysis-fen" type="text" value={fenInput} onChange={event => setFenInput(event.target.value)} placeholder="Paste FEN here" />
          <Button onClick={handleLoadFen} disabled={loading}>Load FEN</Button>
        </section>
        <section className="working-controls__section">
          <h3>Export</h3>
          <div className="working-actions">
            <Button onClick={() => {
              const url = URL.createObjectURL(new Blob([exportAnalysisPgn(tree)], { type: 'text/plain' }));
              const link = document.createElement('a');
              link.href = url;
              link.download = 'analysis.pgn';
              link.click();
              URL.revokeObjectURL(url);
            }}>Download PGN</Button>
            <Button onClick={() => {
              void navigator.clipboard.writeText(fen)
                .then(() => setStatus('FEN copied.'))
                .catch(() => setError('Could not copy FEN.'));
            }}>Copy FEN</Button>
          </div>
        </section>
        <section className="working-controls__section" data-testid="multipv-lines" aria-label="Engine evaluation" aria-busy={evaluation.state === 'loading'}>
          <h3>Engine evaluation</h3>
          <p>White perspective: positive favours White; negative favours Black. M means mate.</p>
          {evaluation.state === 'loading' && <p role="status">Analysing current position… You can keep moving.</p>}
          {evaluation.state === 'error' && <>
            <p role="alert">{evaluation.busy ? 'Engine busy or unavailable. ' : ''}{evaluation.message}</p>
            <Button onClick={() => selectPosition(tree, selected)}>Retry</Button>
          </>}
          {evaluation.state === 'ready' && (evaluation.lines.length ? <ol className="analysis-engine-lines">
            {evaluation.lines.map((line, index) => <li key={index}>
              <strong aria-label={`Line ${index + 1} evaluation ${evaluationLabel(line)}`}>{evaluationLabel(line)}</strong>
              <div className="working-actions" aria-label={`Engine line ${index + 1}`}>
                {line.moves.map((move, moveIndex) => <Button key={moveIndex}
                  aria-label={`Play line ${index + 1} through ${moveIndex + 1}: ${move.san}`}
                  onClick={() => {
                    if (evaluation.revision !== revision.current) return;
                    try { handleMoves(line.moves.slice(0, moveIndex + 1).map(item => item.uci)); }
                    catch { setError('The engine line is not legal in this position. Retry analysis.'); }
                  }}>{move.san}</Button>)}
              </div>
            </li>)}
          </ol> : <p>No legal continuation: this position has ended.</p>)}
        </section>
      </Panel>
    </div>
  </div>;
}

