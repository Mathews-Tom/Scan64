import { describe, expect, it } from 'vitest';
import { Chess } from 'chess.js';
import { analysisPath, analysisReplay, commitAnalysisMoves, createAnalysisTree, exportAnalysisPgn } from './analysisTree';

const initialFen = new Chess().fen();

describe('retained analysis tree', () => {
  it('keeps main and sibling continuations and revisits matching child moves', () => {
    const main = commitAnalysisMoves(createAnalysisTree(), 0, ['e2e4', 'e7e5', 'g1f3']);
    const branch = commitAnalysisMoves(main.tree, 1, ['c7c5', 'g1f3']);
    expect(branch.tree.nodes[1].children).toEqual([2, 4]);
    expect(branch.tree.nodes[2].children).toEqual([3]);
    expect(analysisPath(branch.tree, branch.selected)).toEqual([0, 1, 4, 5]);
    const revisit = commitAnalysisMoves(branch.tree, 1, ['e7e5']);
    expect(revisit.selected).toBe(2);
    expect(revisit.tree.nodes).toHaveLength(6);
    expect(main.tree.nodes).toHaveLength(4);
    expect(main.tree.nodes[1].children).toEqual([2]);
  });

  it('rejects an illegal PV prefix atomically without leaving partial nodes', () => {
    const tree = createAnalysisTree();
    expect(() => commitAnalysisMoves(tree, 0, ['e2e4', 'e7e4'])).toThrow();
    expect(tree.nodes).toHaveLength(1);
    expect(tree.nodes[0].children).toEqual([]);
  });

  it('replays the chosen branch with its retained continuation and correct selected ply', () => {
    const main = commitAnalysisMoves(createAnalysisTree(), 0, ['e2e4', 'e7e5', 'g1f3']);
    const side = commitAnalysisMoves(main.tree, 0, ['d2d4', 'd7d5']);
    const replay = analysisReplay(side.tree, 4);
    expect(replay.path).toEqual([0, 4, 5]);
    expect(replay.selectedPly).toBe(1);
    const game = new Chess();
    game.loadPgn(replay.pgn);
    expect(game.history()).toEqual(['d4', 'd5']);
    expect(analysisReplay(side.tree, 0).path).toEqual([0, 1, 2, 3]);
  });

  it('exports recursive PGN variations describing all retained branches', () => {
    let tree = commitAnalysisMoves(createAnalysisTree(), 0, ['e2e4', 'e7e5', 'g1f3']).tree;
    tree = commitAnalysisMoves(tree, 1, ['c7c5', 'g1f3']).tree;
    tree = commitAnalysisMoves(tree, 0, ['d2d4', 'd7d5']).tree;
    const d4 = tree.nodes[0].children[1];
    tree = commitAnalysisMoves(tree, d4, ['g8f6']).tree;
    const pgn = exportAnalysisPgn(tree);
    expect(pgn).toBe('[Result "*"]\n\n1. e4 (1. d4 1... d5 (1... Nf6)) 1... e5 (1... c5 2. Nf3) 2. Nf3 *\n');
    const game = new Chess();
    game.loadPgn(pgn);
    expect(game.history()).toEqual(['e4', 'e5', 'Nf3']);
    expect(pgn).not.toContain('[SetUp');
    expect(tree.nodes[0].fen).toBe(initialFen);
  });

  it('exports custom setup, black-to-move numbering, SAN check and underpromotion', () => {
    const fen = '7k/8/8/8/8/8/4p3/K7 b - - 0 23';
    let tree = commitAnalysisMoves(createAnalysisTree(fen), 0, ['e2e1q', 'a1a2']).tree;
    tree = commitAnalysisMoves(tree, 0, ['e2e1n']).tree;
    const pgn = exportAnalysisPgn(tree);
    expect(pgn).toContain('[SetUp "1"]');
    expect(pgn).toContain(`[FEN "${fen}"]`);
    expect(pgn).toContain('23... e1=Q+ (23... e1=N) 24. Ka2 *');
    const game = new Chess();
    game.loadPgn(pgn);
    expect(game.history()).toEqual(['e1=Q+', 'Ka2']);
  });

  it('exports a result token for checkmate, terminal roots and a repeated main line', () => {
    const mate = commitAnalysisMoves(createAnalysisTree(), 0, ['f2f3', 'e7e5', 'g2g4', 'd8h4']);
    expect(exportAnalysisPgn(mate.tree)).toContain('2... Qh4# 0-1');
    expect(exportAnalysisPgn(createAnalysisTree('7k/6Q1/5K2/8/8/8/8/8 b - - 0 42'))).toContain('[Result "1-0"]');
    const repeated = commitAnalysisMoves(createAnalysisTree(), 0, ['g1f3', 'g8f6', 'f3g1', 'f6g8', 'g1f3', 'g8f6', 'f3g1', 'f6g8']);
    expect(exportAnalysisPgn(repeated.tree)).toContain('[Result "1/2-1/2"]');
    expect(exportAnalysisPgn(repeated.tree)).toMatch(/1\/2-1\/2\n$/);
  });
});
