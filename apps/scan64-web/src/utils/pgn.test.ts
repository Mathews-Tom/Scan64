import { describe, it, expect } from 'vitest';
import { Chess } from 'chess.js';

describe('PGN Export/Import', () => {
  it('performs a round-trip export-then-import that yields identical move sequence', () => {
    const original = new Chess();
    original.move('e4');
    original.move('e5');
    original.move('Nf3');
    original.move('Nc6');

    const exportedPgn = original.pgn();

    const imported = new Chess();
    imported.loadPgn(exportedPgn);

    expect(imported.history()).toEqual(original.history());
    expect(imported.fen()).toEqual(original.fen());
  });
});
