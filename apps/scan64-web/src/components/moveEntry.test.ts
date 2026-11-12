import { Chess } from 'chess.js';
import { describe, expect, it } from 'vitest';
import { resolveMove } from './moveEntry';

describe('strict move entry', () => {
  it.each([' e2e4 ', ' E2E4 ', 'e4', 'Nf3'])('resolves %s without mutating the position', text => {
    const chess = new Chess();
    const before = chess.fen();
    expect(resolveMove(chess, text).kind).toBe('move');
    expect(chess.fen()).toBe(before);
  });
  it.each(['e2-e4', 'e2 e4', 'e2e4q', 'e2e4junk', 'e2e4\ne7e5', 'nf3', 'e5', '', 'Pe4'])('rejects %s', text => {
    expect(resolveMove(new Chess(), text).kind).toBe('error');
  });
  it('rejects ambiguous SAN and accepts generated disambiguation', () => {
    const chess = new Chess('4k3/8/8/8/8/8/8/1N2KN2 w - - 0 1');
    expect(resolveMove(chess, 'Nd2').kind).toBe('error');
    const resolved = resolveMove(chess, 'Nbd2');
    expect(resolved.kind).toBe('move');
    if (resolved.kind === 'move') expect(resolved.move.from).toBe('b1');
  });
  it.each(['e1g1', 'O-O', 'e1c1', 'O-O-O'])('resolves castling %s', text => {
    expect(resolveMove(new Chess('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1'), text).kind).toBe('move');
  });
  it('requires promotion choice rather than selecting a knight', () => {
    expect(resolveMove(new Chess('8/4P3/8/8/8/8/8/k6K w - - 0 1'), 'e7e8'))
      .toEqual({ kind: 'promotion', from: 'e7', to: 'e8' });
  });
  it.each(['q', 'r', 'b', 'n'])('preserves explicit %s promotion', suffix => {
    const resolved = resolveMove(new Chess('8/4P3/8/8/8/8/8/k6K w - - 0 1'), `e7e8${suffix}`);
    expect(resolved.kind).toBe('move');
    if (resolved.kind === 'move') expect(resolved.move.promotion).toBe(suffix);
  });
  it('requires canonical check signs in SAN', () => {
    const chess = new Chess('7k/8/8/8/8/8/8/R6K w - - 0 1');
    expect(resolveMove(chess, 'Ra8').kind).toBe('error');
    expect(resolveMove(chess, 'Ra8+').kind).toBe('move');
  });
});
