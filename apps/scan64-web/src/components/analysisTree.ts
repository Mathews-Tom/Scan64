import { Chess } from 'chess.js';

export interface AnalysisNode {
  id: number;
  parent: number | null;
  fen: string;
  uci: string | null;
  san: string | null;
  children: number[];
}

export interface AnalysisTree {
  nodes: AnalysisNode[];
}

function playUci(chess: Chess, uci: string) {
  return chess.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci.slice(4) || undefined });
}

export function createAnalysisTree(fen?: string): AnalysisTree {
  return { nodes: [{ id: 0, parent: null, fen: new Chess(fen).fen(), uci: null, san: null, children: [] }] };
}

/** Validate the complete prefix before changing the tree; existing child moves are reused. */
export function commitAnalysisMoves(tree: AnalysisTree, from: number, moves: string[]): { tree: AnalysisTree; selected: number } {
  const chess = new Chess(tree.nodes[from].fen);
  const played = moves.map(uci => playUci(chess, uci));
  const nodes = tree.nodes.slice();
  let selected = from;
  for (const move of played) {
    const uci = `${move.from}${move.to}${move.promotion ?? ''}`;
    const parent = nodes[selected];
    const existing = parent.children.find(id => nodes[id].uci === uci);
    if (existing !== undefined) selected = existing;
    else {
      const id = nodes.length;
      nodes[selected] = { ...parent, children: [...parent.children, id] };
      nodes.push({ id, parent: selected, fen: move.after, uci, san: move.san, children: [] });
      selected = id;
    }
  }
  return { tree: { nodes }, selected };
}

export function analysisPath(tree: AnalysisTree, selected: number): number[] {
  const path: number[] = [];
  let node = tree.nodes[selected];
  while (node.parent !== null) {
    path.push(node.id);
    node = tree.nodes[node.parent];
  }
  return [0, ...path.reverse()];
}

/** Replay follows the selected branch, then its retained first continuation. */
export function analysisReplay(tree: AnalysisTree, selected: number): { path: number[]; pgn: string; selectedPly: number } {
  const path = analysisPath(tree, selected);
  const selectedPly = path.length - 1;
  let node = tree.nodes[selected];
  while (node.children.length) {
    node = tree.nodes[node.children[0]];
    path.push(node.id);
  }
  const chess = new Chess(tree.nodes[0].fen);
  path.slice(1).forEach(id => playUci(chess, tree.nodes[id].uci!));
  return { path, pgn: chess.pgn(), selectedPly };
}

export function analysisMoveLabel(tree: AnalysisTree, id: number): string {
  const node = tree.nodes[id];
  if (node.parent === null) return 'Initial position';
  const before = tree.nodes[node.parent].fen.split(' ');
  return `${before[5]}${before[1] === 'w' ? '.' : '...'} ${node.san}`;
}

export function analysisEntries(tree: AnalysisTree): { id: number; depth: number }[] {
  const entries: { id: number; depth: number }[] = [];
  const visit = (id: number, depth: number) => {
    entries.push({ id, depth });
    tree.nodes[id].children.forEach((child, index) => visit(child, depth + (index === 0 ? 0 : 1)));
  };
  visit(0, 0);
  return entries;
}

export function exportAnalysisPgn(tree: AnalysisTree): string {
  const continuation = (id: number): string => {
    const [main, ...sides] = tree.nodes[id].children;
    if (main === undefined) return '';
    return [analysisMoveLabel(tree, main), ...sides.map(side => `(${[analysisMoveLabel(tree, side), continuation(side)].filter(Boolean).join(' ')})`), continuation(main)]
      .filter(Boolean).join(' ');
  };
  let last = tree.nodes[0];
  const final = new Chess(last.fen);
  while (last.children.length) {
    last = tree.nodes[last.children[0]];
    playUci(final, last.uci!);
  }
  const result = final.isCheckmate() ? final.turn() === 'w' ? '0-1' : '1-0' : final.isDraw() ? '1/2-1/2' : '*';
  const fen = tree.nodes[0].fen;
  const headers = [`[Result "${result}"]`];
  if (fen !== new Chess().fen()) headers.push('[SetUp "1"]', `[FEN "${fen}"]`);
  return `${headers.join('\n')}\n\n${[continuation(0), result].filter(Boolean).join(' ')}\n`;
}
