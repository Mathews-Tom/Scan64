import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StrengthSummary } from './StrengthSummary';
import { strengthFixture } from './strength.test-fixtures';

describe('StrengthSummary', () => {
  it('separates declaration, suggestion, provisional puzzle events and versioned WDL windows', () => {
    render(<StrengthSummary strength={strengthFixture} />);
    expect(screen.getByText('Declared level: Developing.')).toBeInTheDocument();
    expect(screen.getByText(/Suggested bot level: 3/)).toBeInTheDocument();
    expect(screen.getByText(/Scan64 puzzle rating: 1216 \(provisional\)/)).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Puzzle rating events' })).toHaveTextContent('Event 1: 1200 to 1216 (+16); score 1.');
    expect(screen.getByRole('heading', { name: 'Stockfish level 3 · policy v1' })).toBeInTheDocument();
    expect(screen.getByText('Total sample: 10 games: 5 wins, 2 draws, 3 losses.')).toBeInTheDocument();
    expect(screen.getByText('Latest five: 5 games: 3 wins, 1 draws, 1 losses.')).toBeInTheDocument();
    expect(screen.getByText('Previous five: 5 games: 2 wins, 1 draws, 2 losses.')).toBeInTheDocument();
    expect(screen.getByText('Trend: improving.')).toBeInTheDocument();
    expect(screen.getByText(/does not certify no takebacks or external help/)).toBeInTheDocument();
  });

  it('discloses short samples and legacy cohorts without converting them to Elo', () => {
    render(<StrengthSummary strength={{ ...strengthFixture, bot_cohorts: [{
      ...strengthFixture.bot_cohorts[0], bot_policy_version: 'v1(unstamped)', sample_size: 2,
      latest: { sample_size: 2, wins: 1, draws: 0, losses: 1 },
      previous: { sample_size: 0, wins: 0, draws: 0, losses: 0 }, trend: 'insufficient_data',
    }] }} />);
    expect(screen.getByRole('heading', { name: 'Stockfish level 3 · policy v1(unstamped)' })).toBeInTheDocument();
    expect(screen.getByText('Trend: Insufficient data (five games in each window required).')).toBeInTheDocument();
    expect(screen.queryByText(/Rating 1500/)).not.toBeInTheDocument();
  });

  it('shows unrated experience and empty history without inventing a player rating', () => {
    render(<StrengthSummary strength={{ ...strengthFixture, declared_level: null, suggested_bot_level: null,
      puzzle_rating: 1200, puzzle_rated_count: 0, puzzle_events: [], bot_cohorts: [] }} />);
    expect(screen.getByText('Declared level: Not declared.')).toBeInTheDocument();
    expect(screen.getByText(/Suggested bot level: None yet/)).toBeInTheDocument();
    expect(screen.getByText('No rated puzzle events yet.')).toBeInTheDocument();
    expect(screen.getByText('No eligible bot results yet.')).toBeInTheDocument();
  });
});
