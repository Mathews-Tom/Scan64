import type { GameReviewRead } from '../api/types';

interface Props {
  review: GameReviewRead;
  selectedPly: number;
  onSelectedPlyChange: (ply: number) => void;
}

export function ReviewProbabilityGraph({ review, selectedPly, onSelectedPlyChange }: Props) {
  const moments = new Set(review.key_moments.map(moment => moment.ply));
  const points = [
    { ply: 0, probability: review.initial_white_probability, label: 'Initial position', band: null },
    ...review.moves.map(move => ({
      ply: move.ply, probability: move.white_probability,
      label: `Ply ${move.ply}, ${move.san}`,
      band: move.is_owner_move ? move.band : null,
    })),
  ];
  const lastPly = review.moves.at(-1)?.ply ?? 0;
  const x = (ply: number) => 58 + 590 * ply / Math.max(1, lastPly);
  const y = (probability: number) => 140 - 120 * probability;
  const path = points.map((point, index) => point.probability === null ? ''
    : `${index > 0 && points[index - 1].probability !== null ? 'L' : 'M'}${x(point.ply).toFixed(2)},${y(point.probability).toFixed(2)}`).join(' ');

  return <figure className="review-probability-graph">
    <figcaption>White win probability</figcaption>
    <svg viewBox="0 0 680 200" role="group" aria-label="White-perspective win probability by ply">
      <title>White-perspective win probability. Select any point to replay that position.</title>
      {[1, 0.5, 0].map(probability => <g key={probability}>
        <text x="3" y={y(probability) + 4}>{probability * 100}%</text>
        <line className="review-gridline" x1="58" x2="648" y1={y(probability)} y2={y(probability)} />
      </g>)}
      <text x="3" y="176">Unknown</text>
      <path className="review-probability-line" d={path} />
      <line className="review-selection-line" x1={x(selectedPly)} x2={x(selectedPly)} y1="12" y2="182" />
      {points.map(point => {
        const probabilityText = point.probability === null ? 'probability unavailable' : `${(point.probability * 100).toFixed(1)}% White win probability`;
        const moment = moments.has(point.ply);
        const label = `${point.label}, ${probabilityText}${moment ? ', key moment' : ''}`;
        return <g key={point.ply} role="button" tabIndex={0} aria-label={label}
          aria-pressed={point.ply === selectedPly} data-testid={`review-graph-ply-${point.ply}`}
          className={`review-graph-point ${point.probability === null ? 'is-unscored' : ''} ${moment ? 'is-key-moment' : ''}`}
          data-band={point.band ?? undefined}
          onClick={() => onSelectedPlyChange(point.ply)}
          onKeyDown={event => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              onSelectedPlyChange(point.ply);
            } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
              event.preventDefault();
              onSelectedPlyChange(Math.max(0, Math.min(lastPly, selectedPly + (event.key === 'ArrowRight' ? 1 : -1))));
            }
          }}>
          <title>{label}</title>
          <circle className="review-point-hit-area" cx={x(point.ply)} cy={point.probability === null ? 172 : y(point.probability)} r="10" />
          <circle cx={x(point.ply)} cy={point.probability === null ? 172 : y(point.probability)} r={moment ? 6 : 4} />
          {moment && <text x={x(point.ply)} y={(point.probability === null ? 172 : y(point.probability)) - 11} textAnchor="middle">★</text>}
        </g>;
      })}
      <text x="58" y="198">Initial</text>
      <text x="648" y="198" textAnchor="end">Ply {lastPly}</text>
    </svg>
    <p className="muted">Always White's perspective, including when you played Black. Gaps and hollow points mean unavailable scores, not equal chances. Stars mark trusted key moments.</p>
  </figure>;
}
