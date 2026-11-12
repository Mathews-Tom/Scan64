import { useCallback, useEffect, useRef, useState } from 'react';
import { Chess, type Move } from 'chess.js';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Key } from 'chessground/types';

import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';

type Promotion = 'q' | 'r' | 'b' | 'n';
interface CoachBoardProps {
  fen: string;
  pgn?: string;
  orientation?: 'white' | 'black';
  disabled?: boolean;
  resetAfterAttempt?: boolean;
  onMove: (uci: string) => void | Promise<void>;
  testId?: string;
}

function position(fen: string, pgn?: string): Chess {
  const chess = new Chess(fen);
  if (pgn) chess.loadPgn(pgn);
  return chess;
}

export function CoachBoard({ fen, pgn, orientation = 'white', disabled = false,
  resetAfterAttempt = false, onMove, testId = 'coach-board' }: CoachBoardProps) {
  const element = useRef<HTMLDivElement>(null);
  const board = useRef<Api | null>(null);
  const chess = useRef(position(fen, pgn));
  const latest = useRef({ fen, pgn, orientation, disabled, resetAfterAttempt, onMove });
  latest.current = { fen, pgn, orientation, disabled, resetAfterAttempt, onMove };
  const moving = useRef(false);
  const promotion = useRef<{ from: Key; to: Key } | null>(null);
  const [pendingPromotion, setPendingPromotion] = useState<{ from: Key; to: Key } | null>(null);
  const [moves, setMoves] = useState<Move[]>(() => chess.current.history({ verbose: true }));

  const draw = useCallback(() => {
    const game = chess.current;
    const history = game.history({ verbose: true });
    const last = history.at(-1);
    const blocked = latest.current.disabled || moving.current || promotion.current !== null;
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
      fen: game.fen(), orientation: latest.current.orientation, turnColor: color,
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
      if (!latest.current.resetAfterAttempt || !latest.current.disabled) restore();
      else draw();
    }
  }, [draw, restore]);

  useEffect(() => {
    restore();
    requestAnimationFrame(() => board.current?.redrawAll());
  }, [fen, pgn, restore]);

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
          if (latest.current.disabled || moving.current || promotion.current) return;
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
    requestAnimationFrame(() => board.current?.redrawAll());
    return () => { board.current?.destroy(); board.current = null; };
  }, [applyMove, draw]);

  return <div className="coach-board">
    <div ref={element} data-testid={testId} style={{ width: 'min(400px, 100%)', aspectRatio: '1' }} />
    {pendingPromotion !== null && <div role="group" aria-label="Choose promotion piece">
      {(['q', 'r', 'b', 'n'] as const).map(piece => <button key={piece} type="button" onClick={() => {
        const pending = promotion.current;
        if (!pending || latest.current.disabled) return;
        promotion.current = null;
        setPendingPromotion(null);
        void applyMove(pending.from, pending.to, piece);
      }}>Promote to {piece === 'q' ? 'queen' : piece === 'r' ? 'rook' : piece === 'b' ? 'bishop' : 'knight'}</button>)}
    </div>}
    <ol aria-label="Moves" data-testid="san-move-list" style={{ maxHeight: '12rem', overflowY: 'auto' }}>
      {moves.map((move, index) => <li key={index}>
        {move.before.split(' ')[5]}{move.color === 'w' ? '.' : '...'} {move.san}
      </li>)}
    </ol>
  </div>;
}
