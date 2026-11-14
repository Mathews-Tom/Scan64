import { useId, useMemo } from 'react';
import { Chess, type Square } from 'chess.js';
import type { Visualization } from '../api/types';

function annotationKind(visualization: Visualization, game: Chess, arrow: number) {
  const origin = visualization.origin;
  const piece = origin && /^[a-h][1-8]$/.test(origin) ? game.get(origin as Square) : undefined;
  if (piece && piece.color !== game.turn()) return 'threat';
  return (visualization.label ? visualization.label !== '1' : arrow > 1) ? 'alternative' : 'best';
}

const annotationNames = { best: 'Best move', threat: 'Threat', alternative: 'Alternative' } as const;

function defenders(fen: string, target: string): string[] {
    const game = new Chess(fen);
    if (!/^[a-h][1-8]$/.test(target)) return [];
    const square = target as Square;
    const piece = game.get(square);
    if (!piece) return [];
    return game.attackers(square, piece.color).filter(origin => {
      // A pinned piece cannot legally recapture on the defended square.
      const probe = new Chess(fen);
      const defender = probe.remove(origin);
      if (!defender) return false;
      if (piece.type !== 'k') {
        probe.remove(square);
        probe.put(defender, square);
      }
      const king = probe.board().flat().find(p => p?.type === 'k' && p.color === piece.color);
      return !!king && !probe.isAttacked(king.square, piece.color === 'w' ? 'b' : 'w');
    });
}

export function VisualizationDescriptions({ visualizations, fen }: { visualizations: Visualization[]; fen: string }) {
  const game = useMemo(() => new Chess(fen), [fen]);
  const graphical = visualizations.filter(v => ['draw_arrow', 'draw_defence_map', 'highlight_square', 'highlight_piece'].includes(v.command));
  if (!graphical.length) return null;
  let arrow = 0;
  return <ul aria-label="Board annotations" className="board-annotation-text">
    {graphical.map((v, index) => {
      if (v.command === 'draw_arrow') arrow += 1;
      const label = v.command === 'draw_arrow' ? v.label ?? String(arrow) : v.label;
      const squares = v.square ? [v.square] : v.squares ?? [];
      const kind = v.command === 'draw_arrow' ? annotationKind(v, game, arrow) : null;
      const prefix = label && !v.description.trimStart().startsWith(`${label}.`) ? `${label}. ` : '';
      return <li key={index}>{kind ? `${annotationNames[kind]}: ` : ''}{prefix}{v.description}
        {v.command === 'draw_arrow' ? ` (${v.origin} to ${v.target})` : squares.length ? ` (${squares.join(', ')})` : ''}
        {v.command === 'draw_defence_map' && squares.map(square => {
          const origins = defenders(fen, square);
          return <span key={square}> {square}: {origins.length ? `defended by ${origins.join(', ')}` : 'no legal defenders'}.</span>;
        })}
      </li>;
    })}
  </ul>;
}

export function BoardVisualizations({ visualizations, orientation, fen }: { visualizations: Visualization[]; orientation: 'white' | 'black'; fen: string }) {
  const id = useId().replace(/:/g, '');
  const game = useMemo(() => new Chess(fen), [fen]);
  const point = (square?: string) => {
    if (!square || !/^[a-h][1-8]$/.test(square)) return null;
    const file = square.charCodeAt(0) - 97;
    const rank = Number(square[1]) - 1;
    return orientation === 'white' ? [file * 12.5 + 6.25, (7 - rank) * 12.5 + 6.25] : [(7 - file) * 12.5 + 6.25, rank * 12.5 + 6.25];
  };
  let arrow = 0;
  return <svg aria-hidden="true" focusable="false" viewBox="0 0 100 100" className="board-visualizations">
    <defs>{(['best', 'threat', 'alternative'] as const).map(kind =>
      <marker key={kind} id={`${id}-${kind}`} markerWidth="4" markerHeight="4" refX="3" refY="2" orient="auto"><path d="M0,0 L4,2 L0,4 Z" fill={`var(--annotation-${kind})`} /></marker>)}</defs>
    {visualizations.map((v, index) => {
      if (v.command === 'draw_arrow') {
        arrow += 1;
        const from = point(v.origin); const to = point(v.target); const label = v.label ?? String(arrow);
        if (!from || !to) return null;
        // Number the arrow near its destination: candidates can share an origin.
        const labelX = from[0] + (to[0] - from[0]) * 0.75;
        const labelY = from[1] + (to[1] - from[1]) * 0.75;
        const kind = annotationKind(v, game, arrow);
        return <g key={index} aria-label={`${label}: ${v.description}`}><line x1={from[0]} y1={from[1]} x2={to[0]} y2={to[1]} stroke={`var(--annotation-${kind})`} strokeWidth="1.7" markerEnd={`url(#${id}-${kind})`} opacity="0.8" /><circle cx={labelX} cy={labelY} r="3.2" fill="var(--ground)" /><text x={labelX} y={labelY} dy="1.5" textAnchor="middle" fontSize="4.5" fill="var(--ink)">{label}</text></g>;
      }
      if (v.command === 'draw_defence_map') {
        return <g key={index} aria-label={v.description}>{(v.square ? [v.square] : v.squares ?? []).flatMap(square => {
          const to = point(square);
          if (!to) return [];
          return defenders(fen, square).map(origin => {
            const from = point(origin)!;
            return <line key={`${origin}-${square}`} aria-label={`Defender ${origin} to ${square}`}
              x1={from[0]} y1={from[1]} x2={to[0]} y2={to[1]}
              stroke="var(--annotation-alternative)" strokeWidth="1.4" markerEnd={`url(#${id}-alternative)`} opacity="0.85" />;
          });
        })}</g>;
      }
      if (['highlight_square', 'highlight_piece'].includes(v.command)) {
        return <g key={index} aria-label={v.description}>{(v.square ? [v.square] : v.squares ?? []).map(square => {
          const p = point(square); if (!p) return null;
          return v.command === 'highlight_piece'
            ? <circle key={square} cx={p[0]} cy={p[1]} r="5.2" stroke="var(--mark-ink)" strokeWidth="1" fill="var(--transparent)" />
            : <rect key={square} x={p[0] - 6.25} y={p[1] - 6.25} width="12.5" height="12.5" fill="var(--board-selection)" opacity="0.6" />;
        })}</g>;
      }
      return null;
    })}
  </svg>;
}
