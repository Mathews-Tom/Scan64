import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Chess, type Move } from 'chess.js';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Key } from 'chessground/types';
import type { Visualization } from '../api/types';
import { BoardVisualizations, VisualizationDescriptions } from './BoardVisualizations';
import { linePosition } from './linePosition';
import { resolveMove } from './moveEntry';

import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';

type Promotion = 'q' | 'r' | 'b' | 'n';
const EMPTY_VISUALIZATIONS: Visualization[] = [];
interface CoachBoardProps {
  fen: string;
  pgn?: string;
  orientation?: 'white' | 'black';
  disabled?: boolean;
  preview?: boolean;
  resetAfterAttempt?: boolean;
  onMove?: (uci: string) => void | Promise<void>;
  selectedPly?: number;
  onSelectedPlyChange?: (ply: number) => void;
  testId?: string;
  visualizations?: Visualization[];
}

function position(fen: string, pgn?: string): Chess {
  const chess = new Chess(fen);
  if (pgn) chess.loadPgn(pgn);
  return chess;
}

export function CoachBoard({ fen, pgn, orientation = 'white', disabled = false, preview = false,
  resetAfterAttempt = false, onMove, selectedPly, onSelectedPlyChange,
  testId = 'coach-board', visualizations = EMPTY_VISUALIZATIONS }: CoachBoardProps) {
  const element = useRef<HTMLDivElement>(null);
  const board = useRef<Api | null>(null);
  const entry = useRef<HTMLInputElement>(null);
  const promotionButton = useRef<HTMLButtonElement>(null);
  const promotionOrigin = useRef<HTMLElement | null>(null);
  const entryId = useId();
  const [moveText, setMoveText] = useState('');
  const [entryError, setEntryError] = useState('');
  const controlled = selectedPly !== undefined;
  const replay = useMemo(() => {
    if (!controlled) return null;
    const game = position(fen, pgn);
    const moves = game.history({ verbose: true });
    return { moves, initialFen: moves[0]?.before ?? game.fen() };
  }, [fen, pgn, controlled]);
  const [initialPosition] = useState(() => position(fen, pgn));
  const chess = useRef(initialPosition);
  const latest = useRef({ fen, pgn, orientation, disabled, preview, resetAfterAttempt, onMove, selectedPly, replay });
  latest.current = { fen, pgn, orientation, disabled, preview, resetAfterAttempt, onMove, selectedPly, replay };
  const moving = useRef(false);
  const promotion = useRef<{ from: Key; to: Key } | null>(null);
  const [pendingPromotion, setPendingPromotion] = useState<{ from: Key; to: Key } | null>(null);
  const [moves, setMoves] = useState<Move[]>(() => chess.current.history({ verbose: true }));
  const committedHistory = useRef(moves);
  const [announcement, setAnnouncement] = useState('');
  const submittedMove = useRef(false);
  const [ready, setReady] = useState(false);
  const [animation, setAnimation] = useState<{ fen: string; step: number } | null>(null);
  const animationFen = animation?.fen;
  const animationRef = useRef<string | undefined>(undefined);
  animationRef.current = animationFen;

  const draw = useCallback(() => {
    const game = chess.current;
    const replay = latest.current.replay;
    const history = replay?.moves ?? game.history({ verbose: true });
    const last = replay ? history[(latest.current.selectedPly ?? 0) - 1] : history.at(-1);
    const blocked = latest.current.disabled || latest.current.preview || latest.current.onMove === undefined || replay !== null
      || moving.current || promotion.current !== null || animationRef.current !== undefined;
    const dests = new Map<Key, Key[]>();
    if (!blocked) {
      for (const move of game.moves({ verbose: true })) {
        const from = move.from as Key;
        const destinations = dests.get(from) ?? [];
        if (!destinations.includes(move.to as Key)) destinations.push(move.to as Key);
        dests.set(from, destinations);
      }
    }
    const color = game.turn() === 'w' ? 'white' : 'black';
    board.current?.set({
      fen: animationRef.current ?? game.fen(), orientation: latest.current.orientation, turnColor: color,
      lastMove: last ? [last.from as Key, last.to as Key] : [],
      movable: { color: blocked ? undefined : color, dests: blocked ? undefined : dests },
    });
    setMoves(history);
  }, []);

  const restore = useCallback(() => {
    const { replay, selectedPly } = latest.current;
    chess.current = replay
      ? new Chess(replay.moves[Math.max(0, Math.min(selectedPly ?? 0, replay.moves.length)) - 1]?.after ?? replay.initialFen)
      : position(latest.current.fen, latest.current.pgn);
    promotion.current = null;
    setPendingPromotion(null);
    draw();
  }, [draw]);

  const applyMove = useCallback(async (from: Key, to: Key, piece?: Promotion) => {
    if (latest.current.disabled || latest.current.preview || !latest.current.onMove || latest.current.replay !== null
      || moving.current || promotion.current || animationRef.current !== undefined) return;
    let played: Move;
    try {
      played = chess.current.move({ from, to, promotion: piece });
    } catch {
      setEntryError('That move is not legal in the current position.');
      draw();
      return;
    }
    moving.current = true;
    setEntryError('');
    setAnnouncement('');
    submittedMove.current = true;
    draw();
    try {
      await latest.current.onMove?.(`${played.from}${played.to}${played.promotion ?? ''}`);
      setMoveText('');
    } catch {
      setEntryError('The move could not be committed. Check the game status and try again.');
    } finally {
      moving.current = false;
      restore();
    }
  }, [draw, restore]);

  useEffect(() => {
    restore();
    requestAnimationFrame(() => board.current?.redrawAll());
  }, [fen, pgn, selectedPly, restore]);
  useEffect(() => {
    if (controlled || moving.current) return;
    if (preview || animationFen) { setAnnouncement(''); return; }
    const history = chess.current.history({ verbose: true });
    const previous = committedHistory.current;
    const prefixMatches = previous.every((move, index) => history[index]?.after === move.after);
    const committed = prefixMatches ? history.slice(previous.length) : [];
    if (committed.length) {
      const announced = submittedMove.current ? committed : committed.slice(-1);
      setAnnouncement(`${!submittedMove.current && committed.length > 1 ? 'Position loaded. Last move: ' : ''}${announced.map(
        move => `${move.color === 'w' ? 'White' : 'Black'} played ${move.san}.`,
      ).join(' ')}`);
    } else if (previous.length && !prefixMatches) setAnnouncement('Position changed.');
    submittedMove.current = false;
    committedHistory.current = history;
  }, [moves, controlled, preview, animationFen]);
  useEffect(() => { setAnimation(null); }, [fen, pgn, selectedPly, visualizations]);
  useEffect(() => { draw(); }, [animationFen, draw]);
  useEffect(() => {
    if (pendingPromotion) promotionButton.current?.focus();
  }, [pendingPromotion]);

  useEffect(() => {
    if (resetAfterAttempt && !disabled && !moving.current) restore();
    else draw();
    requestAnimationFrame(() => board.current?.redrawAll());
  }, [disabled, orientation, resetAfterAttempt, draw, restore]);

  const requestMove = useCallback((from: Key, to: Key, piece?: Promotion) => {
    if (latest.current.disabled || latest.current.preview || !latest.current.onMove || latest.current.replay !== null
      || moving.current || promotion.current || animationRef.current !== undefined) return;
    const promotes = !piece && chess.current.moves({ verbose: true }).some(
      move => move.from === from && move.to === to && move.promotion !== undefined,
    );
    if (promotes) {
      promotionOrigin.current = entry.current;
      promotion.current = { from, to };
      setPendingPromotion({ from, to });
      setEntryError('');
      draw();
    } else void applyMove(from, to, piece);
  }, [applyMove, draw]);

  const cancelPromotion = () => {
    promotion.current = null;
    setPendingPromotion(null);
    draw();
    (promotionOrigin.current ?? entry.current)?.focus();
  };

  useEffect(() => {
    if (!element.current) return;
    const motion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const updateMotion = () => board.current?.set({ animation: { enabled: !motion?.matches } });
    board.current = Chessground(element.current, {
      fen: chess.current.fen(), orientation: latest.current.orientation,
      animation: { enabled: !motion?.matches },
      movable: {
        color: latest.current.disabled ? undefined : chess.current.turn() === 'w' ? 'white' : 'black',
        free: false,
        events: { after: (from, to) => requestMove(from, to) },
      },
    });
    motion?.addEventListener('change', updateMotion);
    draw();
    setReady(true);
    requestAnimationFrame(() => board.current?.redrawAll());
    return () => {
      motion?.removeEventListener('change', updateMotion);
      board.current?.destroy(); board.current = null;
    };
  }, [requestMove, draw]);

  const displayed = useMemo(() => animationFen ? new Chess(animationFen) : null, [animationFen]) ?? chess.current;
  const blocked = disabled || preview || !onMove || controlled || moving.current || pendingPromotion !== null || animationFen !== undefined;
  const side = displayed.turn() === 'w' ? 'White' : 'Black';
  const result = displayed.isCheckmate() ? `Checkmate. ${side === 'White' ? 'Black' : 'White'} wins.`
    : displayed.isStalemate() ? 'Draw by stalemate.' : displayed.isDraw() ? 'Draw.'
      : `${side} to move.${displayed.isCheck() ? ' Check.' : ''}`;
  const names = { p: 'pawn', n: 'knight', b: 'bishop', r: 'rook', q: 'queen', k: 'king' } as const;

  return <div className="coach-board">
    <div style={{ position: 'relative', width: 'min(400px, 100%)', aspectRatio: '1' }}>
      <div ref={element} data-testid={testId}
        aria-hidden="true"
        aria-busy={!ready || disabled || moving.current || pendingPromotion !== null}
        style={{ width: '100%', height: '100%' }} />
      <BoardVisualizations visualizations={visualizations} orientation={orientation} fen={animationFen ?? chess.current.fen()} />
    </div>
    <VisualizationDescriptions visualizations={visualizations} fen={animationFen ?? chess.current.fen()} />
    <p role="status" aria-atomic="true">{moving.current ? 'Submitting move.' : <>
      {animationFen || preview ? 'Line preview. ' : controlled ? `Review position ${selectedPly}. ` : ''}
      {!controlled && !animationFen && !preview && announcement ? `${announcement} ` : ''}{result}
    </>}</p>
    <details>
      <summary>Read board position</summary>
      <p>Coordinates identify each occupied square. Board viewed from {orientation === 'white' ? 'White' : 'Black'}.</p>
      <ul aria-label="Pieces by square">
        {displayed.board().flat().filter(piece => piece !== null).map(piece => <li key={piece.square}>
          {piece.square}: {piece.color === 'w' ? 'White' : 'Black'} {names[piece.type]}
        </li>)}
      </ul>
    </details>
    <details>
      <summary>Legal moves and notation help</summary>
      <p>UCI uses source and destination squares, with q, r, b or n for promotion. English SAN is case-sensitive and includes disambiguation and check signs. Castling is O-O or O-O-O.</p>
      <p>{displayed.moves().join(', ') || 'No legal moves.'}</p>
    </details>
    {onMove && !controlled && <form aria-label="Enter chess move" onSubmit={event => {
      event.preventDefault();
      if (blocked) return;
      const resolved = resolveMove(chess.current, moveText);
      if (resolved.kind === 'error') {
        setEntryError(resolved.message);
        entry.current?.focus();
      } else if (resolved.kind === 'promotion') requestMove(resolved.from, resolved.to);
      else requestMove(resolved.move.from as Key, resolved.move.to as Key, resolved.move.promotion as Promotion | undefined);
    }}>
      <label htmlFor={entryId}>Move (UCI or SAN)</label>
      <input id={entryId} ref={entry} value={moveText} readOnly={blocked}
        autoComplete="off" autoCapitalize="none" spellCheck={false}
        aria-describedby={`${entryId}-help`} aria-invalid={entryError ? true : undefined}
        onChange={event => { setMoveText(event.target.value); setEntryError(''); }} />
      <p id={`${entryId}-help`}>For example e2e4 or Nf3. Tap two squares or enter a move; dragging is optional.</p>
      <button type="submit" disabled={blocked}>Make move</button>
    </form>}
    {entryError && <p role="alert">{entryError}</p>}
    {visualizations.filter(visualization => visualization.command === 'animate_line'
      || !['draw_arrow', 'highlight_square', 'highlight_piece', 'draw_defence_map'].includes(visualization.command)).map((visualization, index) => <div key={index}>
      <p className="board-visualization-description">{visualization.description}</p>
      {visualization.command === 'animate_line' && visualization.moves?.length ? <>
        <button type="button" onClick={() => {
          const step = Math.min((animation?.step ?? 0) + 1, visualization.moves!.length);
          try { setAnimation({ fen: linePosition(controlled ? chess.current.fen() : position(fen, pgn).fen(), visualization.moves!, step), step }); }
          catch { setAnimation(null); }
        }}>Next line move</button>
        <button type="button" onClick={() => {
          try { setAnimation({ fen: linePosition(controlled ? chess.current.fen() : position(fen, pgn).fen(), visualization.moves!, visualization.moves!.length), step: visualization.moves!.length }); }
          catch { setAnimation(null); }
        }}>Show full line</button>
        <button type="button" onClick={() => setAnimation(null)}>Return to original position</button>
      </> : null}
    </div>)}
    {pendingPromotion !== null && <div role="group" aria-label="Choose promotion piece"
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); cancelPromotion(); } }}>
      {(['q', 'r', 'b', 'n'] as const).map(piece => <button key={piece} ref={piece === 'q' ? promotionButton : undefined} type="button" onClick={() => {
        const pending = promotion.current;
        if (!pending || latest.current.disabled) return;
        promotion.current = null;
        setPendingPromotion(null);
        void applyMove(pending.from, pending.to, piece);
        (promotionOrigin.current ?? entry.current)?.focus();
      }}>Promote to {piece === 'q' ? 'queen' : piece === 'r' ? 'rook' : piece === 'b' ? 'bishop' : 'knight'}</button>)}
      <button type="button" onClick={cancelPromotion}>Cancel promotion</button>
    </div>}
    <ol aria-label="Moves" data-testid={`${testId}-san-move-list`}
      style={{ listStyle: 'none', padding: 0, maxHeight: '12rem', overflowY: 'auto' }}>
      {controlled && <li><button type="button" aria-current={selectedPly === 0 ? 'step' : undefined}
        disabled={onSelectedPlyChange === undefined} onClick={() => onSelectedPlyChange?.(0)}>Initial position</button></li>}
      {moves.map((move, index) => <li key={index}>
        {controlled ? <button type="button" aria-current={selectedPly === index + 1 ? 'step' : undefined}
          disabled={onSelectedPlyChange === undefined} onClick={() => onSelectedPlyChange?.(index + 1)}>
          {move.before.split(' ')[5]}{move.color === 'w' ? '.' : '...'} {move.san}
        </button> : <>{move.before.split(' ')[5]}{move.color === 'w' ? '.' : '...'} {move.san}</>}
      </li>)}
    </ol>
  </div>;
}
