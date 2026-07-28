import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Key } from 'chessground/types';

import type { LessonSpec } from '../api/types';
import { LessonBoard } from './LessonBoard';

type MoveHandler = (from: Key, to: Key) => void;

const chessgroundMock = vi.hoisted(() => ({
  after: undefined as MoveHandler | undefined,
  free: undefined as boolean | undefined,
  destroy: vi.fn(),
  redrawAll: vi.fn(),
  set: vi.fn(),
}));

vi.mock('chessground', () => ({
  Chessground: (
    _element: Element,
    config: { movable: { events?: { after?: MoveHandler }; free?: boolean } },
  ) => {
    chessgroundMock.after = config.movable.events?.after;
    chessgroundMock.free = config.movable.free;
    return {
      destroy: chessgroundMock.destroy,
      redrawAll: chessgroundMock.redrawAll,
      set: chessgroundMock.set,
    };
  },
}));

const lesson: LessonSpec = {
  schema_version: '1.0',
  lesson_id: 'lesson',
  source: { kind: 'position', fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1' },
  diagnosis: { primary: 'tactics.fork', secondary: [], confidence: 1, evidence_refs: [] },
  objective: { type: 'find_best_move', instruction: 'Play e4.' },
  interaction: { input: 'click', maximum_attempts: 3, accepted_moves: [{ san: 'e4', lan: 'e2-e4', reason: 'Centre control.' }] },
  hints: [],
  explanation: { text: 'Develop the centre.' },
  verification: { status: 'verified', engine: 'test', engine_binary_digest: 'digest', nodes: 1, multipv: 1, verified_at: '2026-07-28T00:00:00Z' },
  mastery: { skill_key: 'tactics.fork', delta: 0 },
};

function registeredMoveHandler(): MoveHandler {
  if (chessgroundMock.after === undefined) throw new Error('Expected Chessground move handler');
  return chessgroundMock.after;
}

describe('LessonBoard', () => {
  beforeEach(() => {
    chessgroundMock.after = undefined;
    chessgroundMock.free = undefined;
    chessgroundMock.destroy.mockReset();
    chessgroundMock.redrawAll.mockReset();
    chessgroundMock.set.mockReset();
  });

  it('resets the position after a rejected attempt without remounting the board', async () => {
    const onMove = vi.fn();
    const rendered = render(<LessonBoard lesson={lesson} onMove={onMove} />);
    await waitFor(() => expect(chessgroundMock.after).toBeDefined());

    act(() => registeredMoveHandler()('e2', 'e4'));
    rendered.rerender(<LessonBoard lesson={lesson} disabled onMove={onMove} />);
    rendered.rerender(<LessonBoard lesson={lesson} disabled={false} onMove={onMove} />);
    act(() => registeredMoveHandler()('e2', 'e4'));

    expect(onMove).toHaveBeenCalledTimes(2);
    expect(onMove).toHaveBeenLastCalledWith('e2e4');
    expect(chessgroundMock.destroy).not.toHaveBeenCalled();
    expect(chessgroundMock.free).toBe(false);
  });

  it('restores the source position after an invalid board move', async () => {
    const onMove = vi.fn();
    render(<LessonBoard lesson={lesson} onMove={onMove} />);
    await waitFor(() => expect(chessgroundMock.after).toBeDefined());

    act(() => registeredMoveHandler()('e2', 'e5'));

    expect(onMove).not.toHaveBeenCalled();

    expect(chessgroundMock.set).toHaveBeenCalledWith(expect.objectContaining({
      fen: lesson.source.fen,
    }));
  });

  it('submits the chosen promotion piece in UCI form', async () => {
    const onMove = vi.fn();
    const promotionLesson: LessonSpec = {
      ...lesson,
      lesson_id: 'promotion',
      source: { kind: 'position', fen: '8/P7/8/8/8/8/8/k6K w - - 0 1' },
      interaction: { input: 'click', maximum_attempts: 3, accepted_moves: [{ san: 'a8=R+', lan: 'a7-a8=R', reason: 'Promotion test.' }] },
    };
    render(<LessonBoard lesson={promotionLesson} onMove={onMove} />);
    await waitFor(() => expect(chessgroundMock.after).toBeDefined());

    act(() => registeredMoveHandler()('a7', 'a8'));
    fireEvent.click(await screen.findByRole('button', { name: 'Promote to rook' }));

    expect(onMove).toHaveBeenCalledWith('a7a8r');
  });
});
