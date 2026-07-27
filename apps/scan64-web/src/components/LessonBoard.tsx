import { useEffect, useRef, useState } from 'react';
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
  const onMoveRef = useRef(onMove);
  const [ready, setReady] = useState(false);

  onMoveRef.current = onMove;

  useEffect(() => {
    chess.current = new Chess(lesson.source.fen);
    board.current?.set({
      fen: chess.current.fen(),
      turnColor: turnColor(chess.current),
      movable: {
        color: disabled ? undefined : turnColor(chess.current),
        dests: disabled ? undefined : getDests(chess.current),
      },
    });
    requestAnimationFrame(() => board.current?.redrawAll());
  }, [disabled, lesson.lesson_id, lesson.source.fen]);

  useEffect(() => {
    if (boardElement.current === null || board.current !== null) return;
    board.current = Chessground(boardElement.current, {
      fen: chess.current.fen(),
      turnColor: turnColor(chess.current),
      movable: {
        color: disabled ? undefined : turnColor(chess.current),
        dests: disabled ? undefined : getDests(chess.current),
        events: {
          after: (from, to) => {
            if (disabled) return;
            try {
              chess.current.move({ from, to, promotion: 'q' });
            } catch {
              return;
            }
            board.current?.set({
              fen: chess.current.fen(),
              turnColor: turnColor(chess.current),
              movable: { color: undefined },
            });
            onMoveRef.current(`${from}${to}`);
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
  }, [disabled]);

  return <div ref={boardElement} data-testid="lesson-board" aria-busy={!ready} style={{ width: '400px', height: '400px' }} />;
}
