import { useCallback, useEffect, useRef, useState } from 'react';
import { Chess, type Move } from 'chess.js';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Key } from 'chessground/types';
import type { Visualization } from '../api/types';
import { BoardVisualizations } from './BoardVisualizations';
import { linePosition } from './linePosition';

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
  resetAfterAttempt?: boolean;
  onMove: (uci: string) => void | Promise<void>;
  testId?: string;
  visualizations?: Visualization[];
}

function position(fen: string, pgn?: string): Chess {
  const chess = new Chess(fen);
  if (pgn) chess.loadPgn(pgn);
  return chess;
}

export function CoachBoard({ fen, pgn, orientation = 'white', disabled = false,
  resetAfterAttempt = false, onMove, testId = 'coach-board', visualizations = EMPTY_VISUALIZATIONS }: CoachBoardProps) {
  const element = useRef<HTMLDivElement>(null);
  const board = useRef<Api | null>(null);
  const [initialPosition] = useState(() => position(fen, pgn));
  const chess = useRef(initialPosition);
  const latest = useRef({ fen, pgn, orientation, disabled, resetAfterAttempt, onMove });
  latest.current = { fen, pgn, orientation, disabled, resetAfterAttempt, onMove };
  const moving = useRef(false);
  const promotion = useRef<{ from: Key; to: Key } | null>(null);
  const [pendingPromotion, setPendingPromotion] = useState<{ from: Key; to: Key } | null>(null);
  const [moves, setMoves] = useState<Move[]>(() => chess.current.history({ verbose: true }));
  const [ready, setReady] = useState(false);
  const [animation, setAnimation] = useState<{ fen: string; step: number } | null>(null);
  const animationFen = animation?.fen;
  const animationRef = useRef<string | undefined>(undefined);
  animationRef.current = animationFen;

  const draw = useCallback(() => {
    const game = chess.current;
    const history = game.history({ verbose: true });
    const last = history.at(-1);
    const blocked = latest.current.disabled || moving.current || promotion.current !== null || animationRef.current !== undefined;
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
    chess.current = position(latest.current.fen, latest.current.pgn);
    promotion.current = null;
    setPendingPromotion(null);
    draw();
  }, [draw]);

  const applyMove = useCallback(async (from: Key, to: Key, piece?: Promotion) => {
    let played: Move;
    try {
      played = chess.current.move({ from, to, promotion: piece });
    } catch {
      draw();
      return;
    }
    moving.current = true;
    draw();
    try {
      await latest.current.onMove(`${played.from}${played.to}${played.promotion ?? ''}`);
    } finally {
      moving.current = false;
      restore();
    }
  }, [draw, restore]);

  useEffect(() => {
    restore();
    requestAnimationFrame(() => board.current?.redrawAll());
  }, [fen, pgn, restore]);
  useEffect(() => { setAnimation(null); }, [fen, pgn, visualizations]);
  useEffect(() => { draw(); }, [animationFen, draw]);

  useEffect(() => {
    if (resetAfterAttempt && !disabled && !moving.current) restore();
    else draw();
    requestAnimationFrame(() => board.current?.redrawAll());
  }, [disabled, orientation, resetAfterAttempt, draw, restore]);

  useEffect(() => {
    if (!element.current) return;
    board.current = Chessground(element.current, {
      fen: chess.current.fen(), orientation: latest.current.orientation,
      movable: {
        color: latest.current.disabled ? undefined : chess.current.turn() === 'w' ? 'white' : 'black',
        free: false,
        events: { after: (from, to) => {
          if (latest.current.disabled || moving.current || promotion.current || animationRef.current !== undefined) return;
          const promotes = chess.current.moves({ verbose: true }).some(
            move => move.from === from && move.to === to && move.promotion !== undefined,
          );
          if (promotes) {
            promotion.current = { from, to };
            setPendingPromotion({ from, to });
            draw();
          } else {
            void applyMove(from, to);
          }
        } },
      },
    });
    draw();
    setReady(true);
    requestAnimationFrame(() => board.current?.redrawAll());
    return () => { board.current?.destroy(); board.current = null; };
  }, [applyMove, draw]);

  return <div className="coach-board">
    <div style={{ position: 'relative', width: 'min(400px, 100%)', aspectRatio: '1' }}>
      <div ref={element} data-testid={testId}
        aria-busy={!ready || disabled || moving.current || pendingPromotion !== null}
        style={{ width: '100%', height: '100%' }} />
      <BoardVisualizations visualizations={visualizations} orientation={orientation} fen={animationFen ?? chess.current.fen()} />
    </div>
    {visualizations.filter(visualization => visualization.command === 'animate_line'
      || !['draw_arrow', 'highlight_square', 'highlight_piece', 'draw_defence_map'].includes(visualization.command)).map((visualization, index) => <div key={index}>
      <p>{visualization.description}</p>
      {visualization.command === 'animate_line' && visualization.moves?.length ? <>
        <button type="button" onClick={() => {
          const step = Math.min((animation?.step ?? 0) + 1, visualization.moves!.length);
          try { setAnimation({ fen: linePosition(position(fen, pgn).fen(), visualization.moves!, step), step }); }
          catch { setAnimation(null); }
        }}>Next line move</button>
        <button type="button" onClick={() => {
          try { setAnimation({ fen: linePosition(position(fen, pgn).fen(), visualization.moves!, visualization.moves!.length), step: visualization.moves!.length }); }
          catch { setAnimation(null); }
        }}>Show full line</button>
        <button type="button" onClick={() => setAnimation(null)}>Return to original position</button>
      </> : null}
    </div>)}
    {pendingPromotion !== null && <div role="group" aria-label="Choose promotion piece">
      {(['q', 'r', 'b', 'n'] as const).map(piece => <button key={piece} type="button" onClick={() => {
        const pending = promotion.current;
        if (!pending || latest.current.disabled) return;
        promotion.current = null;
        setPendingPromotion(null);
        void applyMove(pending.from, pending.to, piece);
      }}>Promote to {piece === 'q' ? 'queen' : piece === 'r' ? 'rook' : piece === 'b' ? 'bishop' : 'knight'}</button>)}
    </div>}
    <ol aria-label="Moves" data-testid={`${testId}-san-move-list`}
      style={{ listStyle: 'none', padding: 0, maxHeight: '12rem', overflowY: 'auto' }}>
      {moves.map((move, index) => <li key={index}>
        {move.before.split(' ')[5]}{move.color === 'w' ? '.' : '...'} {move.san}
      </li>)}
    </ol>
  </div>;
}
