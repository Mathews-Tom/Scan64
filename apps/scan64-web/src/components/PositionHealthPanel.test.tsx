import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import type { HealthMeterRead, PositionHealthRead } from '../api/types';
import { PositionHealthPanel } from './PositionHealthPanel';
import { filledSegments, meterText, readHealthPanel, saveHealthPanel, SEGMENTS } from '../healthPanel';

const meter = (health: number, level: HealthMeterRead['level'], trend: HealthMeterRead['trend'], pawns = -1.2): HealthMeterRead =>
  ({ health, level, trend, terms: [{ name: 'King danger', pawns }] });

const READ: PositionHealthRead = {
  fen: '8/8/8/8/8/8/8/K6k w - - 0 1',
  learner: 'white',
  overall: { key: 'Overall', name: 'overall', learner: meter(0.3, 'low', 'falling', -2.1), opponent: meter(0.95, 'good', 'rising', 2.1) },
  groups: [{ key: 'King', name: 'king safety', learner: meter(0.2, 'low', 'falling'), opponent: meter(0.9, 'good', 'steady', 0.4) }],
  outcome: null,
};

beforeEach(() => {
  localStorage.setItem('scan64_player_id', 'health-test');
  localStorage.setItem('scan64_player_token:health-test', 'test-token');
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

describe('position health meters', () => {
  it('reads level and trend in words for screen readers', () => {
    expect(meterText('king safety', meter(0.2, 'low', 'falling'))).toBe('king safety: low, falling');
    expect(meterText('space', meter(0.9, 'good', 'steady'))).toBe('space: good');
  });

  it('fills whole segments in proportion to health', () => {
    expect(filledSegments(0)).toBe(0);
    expect(filledSegments(1)).toBe(SEGMENTS);
    expect(filledSegments(0.5)).toBe(SEGMENTS / 2);
  });

  it.each([
    ['checkmate', 'Checkmate'],
    ['stalemate', 'Stalemate'],
    ['insufficient_material', 'Draw by insufficient material'],
    ['seventyfive_moves', 'Draw by the 75-move rule'],
  ] as const)('shows the result instead of meters when the game is over: %s', async (outcome, text) => {
    vi.spyOn(ApiClient, 'positionHealth').mockResolvedValue({ ...READ, outcome });
    render(<PositionHealthPanel fen={READ.fen} previousFen={null} />);
    expect(await screen.findByTestId('position-health-outcome')).toHaveTextContent(`${text}: the game is over`);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('keeps the last reading on screen while the next position loads', async () => {
    let release: (read: PositionHealthRead) => void = () => {};
    vi.spyOn(ApiClient, 'positionHealth')
      .mockResolvedValueOnce(READ)
      .mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
    const { rerender } = render(<PositionHealthPanel fen="fen-1" previousFen={null} />);
    expect(await screen.findByRole('img', { name: 'White king safety: low, falling' })).toBeInTheDocument();
    rerender(<PositionHealthPanel fen="fen-2" previousFen="fen-1" />);
    const panel = screen.getByTestId('position-health');
    await waitFor(() => expect(panel).toHaveAttribute('aria-busy', 'true'));
    expect(screen.getByRole('img', { name: 'White king safety: low, falling' })).toBeInTheDocument();
    release({ ...READ, groups: [{ ...READ.groups[0], learner: meter(0.9, 'good', 'rising') }] });
    expect(await screen.findByRole('img', { name: 'White king safety: good, rising' })).toBeInTheDocument();
    expect(panel).toHaveAttribute('aria-busy', 'false');
  });

  it('says a meter has no nonzero terms rather than no change from the start', async () => {
    const empty = { ...meter(0.45, 'fair', 'steady'), terms: [] };
    vi.spyOn(ApiClient, 'positionHealth').mockResolvedValue({ ...READ, groups: [{ key: 'Pawns', name: 'pawn structure', learner: empty, opponent: empty }] });
    render(<PositionHealthPanel fen={READ.fen} previousFen={null} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Show details' }));
    expect(screen.getByText('White: no nonzero terms')).toBeInTheDocument();
    expect(screen.queryByText(/no change from the start/)).not.toBeInTheDocument();
  });

  it('cancels the request for a position left behind and sends the current and previous FEN', async () => {
    const signals: AbortSignal[] = [];
    const call = vi.spyOn(ApiClient, 'positionHealth').mockImplementation(async (_data, signal) => {
      signals.push(signal!);
      return READ;
    });
    const { rerender } = render(<PositionHealthPanel fen="fen-1" previousFen={null} learner="black" />);
    rerender(<PositionHealthPanel fen="fen-2" previousFen="fen-1" learner="black" />);
    await waitFor(() => expect(call).toHaveBeenCalledTimes(2));
    expect(call).toHaveBeenLastCalledWith(expect.objectContaining({ fen: 'fen-2', previous_fen: 'fen-1', learner: 'black' }), expect.any(AbortSignal));
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
  });

  it('renders one meter per side with labels, and details on request', async () => {
    const call = vi.spyOn(ApiClient, 'positionHealth').mockResolvedValue(READ);
    render(<PositionHealthPanel fen={READ.fen} previousFen={null} />);
    expect(await screen.findByRole('img', { name: 'White king safety: low, falling' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Black king safety: good' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'White overall: low, falling' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Black overall: good, rising' })).toBeInTheDocument();
    expect(call).toHaveBeenCalledWith(expect.objectContaining({ fen: READ.fen, learner: 'white', previous_fen: null }), expect.any(AbortSignal));
    fireEvent.click(screen.getByRole('button', { name: 'Show details' }));
    expect(screen.getByText('White: King danger -1.20')).toBeInTheDocument();
    expect(screen.getByText('Black: King danger +0.40')).toBeInTheDocument();
  });

  it('shows the error instead of meters when the server refuses', async () => {
    vi.spyOn(ApiClient, 'positionHealth').mockRejectedValue(new Error('Failed to read position health: Invalid FEN'));
    render(<PositionHealthPanel fen="bad" previousFen={null} />);
    await waitFor(() => expect(screen.getByTestId('position-health-error')).toHaveTextContent('Invalid FEN'));
  });
});

it('speaks for the learner in game review', async () => {
  const call = vi.spyOn(ApiClient, 'positionHealth').mockResolvedValue({ ...READ, learner: 'black' });
  render(<PositionHealthPanel fen={READ.fen} previousFen={null} learner="black" />);
  expect(await screen.findByRole('img', { name: 'You king safety: low, falling' })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Opponent' })).toBeInTheDocument();
  expect(call).toHaveBeenCalledWith(expect.objectContaining({ learner: 'black' }), expect.any(AbortSignal));
});

describe('health panel setting', () => {
  it('is on by default and remembers being turned off', () => {
    expect(readHealthPanel()).toBe(true);
    expect(saveHealthPanel(false)).toBe(true);
    expect(localStorage.getItem('scan64-health-panel')).toBe('false');
    expect(readHealthPanel()).toBe(false);
    saveHealthPanel(true);
  });
});
