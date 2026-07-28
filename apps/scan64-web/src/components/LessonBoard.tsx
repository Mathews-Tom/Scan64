import { useCallback, useEffect, useRef, useState } from 'react';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Key } from 'chessground/types';
import { Chess } from 'chess.js';

import type { LessonSpec } from '../api/types';

import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';

function getDests(chess: Chess): Map<Key, Key[]> {
  const dests = new Map<Key, Key[]>();
  for (const move of chess.moves({ verbose: true })) {
    const from = move.from as Key;
    const destinations = dests.get(from) ?? [];
    destinations.push(move.to as Key);
    dests.set(from, destinations);
  }
  return dests;
}

function turnColor(chess: Chess): 'white' | 'black' {
  return chess.turn() === 'w' ? 'white' : 'black';
}

interface LessonBoardProps {
  lesson: LessonSpec;
  disabled?: boolean;
  onMove: (move: string) => void;
}

export function LessonBoard({ lesson, disabled = false, onMove }: LessonBoardProps) {
  const boardElement = useRef<HTMLDivElement>(null);
  const board = useRef<Api | null>(null);
  const chess = useRef(new Chess(lesson.source.fen));
  const disabledRef = useRef(disabled);
  const wasDisabled = useRef(disabled);
  const pendingPromotionRef = useRef<{ from: Key; to: Key } | null>(null);
  const onMoveRef = useRef(onMove);
  const [ready, setReady] = useState(false);
  const [pendingPromotion, setPendingPromotion] = useState<{ from: Key; to: Key } | null>(null);

  onMoveRef.current = onMove;

  const updateMovable = useCallback(() => {
    const isDisabled = disabledRef.current || pendingPromotionRef.current !== null;
    board.current?.set({
      movable: {
        color: isDisabled ? undefined : turnColor(chess.current),
        dests: isDisabled ? undefined : getDests(chess.current),
      },
    });
  }, []);

  const resetBoard = useCallback(() => {
    chess.current = new Chess(lesson.source.fen);
    pendingPromotionRef.current = null;
    setPendingPromotion(null);
    board.current?.set({
      fen: chess.current.fen(),
      turnColor: turnColor(chess.current),
    });
    updateMovable();
    requestAnimationFrame(() => board.current?.redrawAll());
  }, [lesson.source.fen, updateMovable]);

  const applyMove = useCallback((from: Key, to: Key, promotion?: 'q' | 'r' | 'b' | 'n') => {
    try {
      const played = chess.current.move({ from, to, promotion });
      board.current?.set({
        fen: chess.current.fen(),
        turnColor: turnColor(chess.current),
        movable: { color: undefined },
      });
      onMoveRef.current(`${played.from}${played.to}${played.promotion ?? ''}`);
    } catch {
      board.current?.set({
        fen: chess.current.fen(),
        turnColor: turnColor(chess.current),
      });
      updateMovable();
    }
  }, [updateMovable]);

  useEffect(() => {
    resetBoard();
  }, [lesson.lesson_id, resetBoard]);

  useEffect(() => {
    const wasTemporarilyDisabled = wasDisabled.current;
    disabledRef.current = disabled;
    wasDisabled.current = disabled;
    if (wasTemporarilyDisabled && !disabled) {
      resetBoard();
      return;
    }
    updateMovable();
  }, [disabled, resetBoard, updateMovable]);

  useEffect(() => {
    if (boardElement.current === null || board.current !== null) return;
    board.current = Chessground(boardElement.current, {
      fen: chess.current.fen(),
      turnColor: turnColor(chess.current),
      movable: {
        color: disabledRef.current ? undefined : turnColor(chess.current),
        dests: disabledRef.current ? undefined : getDests(chess.current),
        free: false,
        events: {
          after: (from, to) => {
            if (disabledRef.current || pendingPromotionRef.current !== null) return;
            const promotions = chess.current.moves({ verbose: true }).filter(
              move => move.from === from && move.to === to && move.promotion !== undefined,
            );
            if (promotions.length > 0) {
              const pending = { from, to };
              pendingPromotionRef.current = pending;
              setPendingPromotion(pending);
              updateMovable();
              return;
            }
            applyMove(from, to);
          },
        },
      },
    });
    setReady(true);
    requestAnimationFrame(() => board.current?.redrawAll());
    return () => {
      board.current?.destroy?.();
      board.current = null;
    };
  }, [applyMove, updateMovable]);

  const choosePromotion = (promotion: 'q' | 'r' | 'b' | 'n') => {
    const pending = pendingPromotionRef.current;
    if (pending === null) return;
    pendingPromotionRef.current = null;
    setPendingPromotion(null);
    applyMove(pending.from, pending.to, promotion);
  };

  return (
    <>
      <div ref={boardElement} data-testid="lesson-board" aria-busy={!ready} style={{ width: '400px', height: '400px' }} />
      {pendingPromotion !== null && (
        <div role="group" aria-label="Choose promotion piece">
          {(['q', 'r', 'b', 'n'] as const).map(piece => (
            <button key={piece} type="button" onClick={() => choosePromotion(piece)}>
              Promote to {piece === 'q' ? 'queen' : piece === 'r' ? 'rook' : piece === 'b' ? 'bishop' : 'knight'}
            </button>
          ))}
        </div>
      )}
    </>
  );
}
