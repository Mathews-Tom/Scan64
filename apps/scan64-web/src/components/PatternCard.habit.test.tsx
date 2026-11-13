import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { PatternsReport } from '../api/types';
import { PatternCard } from './PatternCard';

const report: PatternsReport = {
  player_id: 'owner', eligible_game_count: 5, minimum_occurrences: 3, status: 'recurring_diagnosis',
  recurring_diagnoses: [{ diagnosis: 'fork', name: 'Fork', template: 'Two targets attacked.',
    occurrence_count: 3, game_ids: ['one', 'two', 'three'], evidence_references: [],
    observed_loss: 0.6, scored_occurrences: 3, total_occurrences: 4 }],
};

describe('PatternCard habit footer', () => {
  it('places owned target actions and status inside the recurring-pattern card', () => {
    render(<PatternCard report={report}><button>Track practice target for Fork</button><p>Pattern practice target met</p></PatternCard>);
    const card = screen.getByRole('region', { name: 'Recurring pattern' });
    expect(within(card).getByRole('button', { name: 'Track practice target for Fork' })).toBeInTheDocument();
    expect(within(card).getByText('Pattern practice target met')).toBeInTheDocument();
  });

  it('does not expose the owned target footer to linked coaches', () => {
    render(<PatternCard report={report} owner={false}><button>Track practice target for Fork</button></PatternCard>);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText(/Coverage: 3\/4/)).toBeInTheDocument();
  });
});
