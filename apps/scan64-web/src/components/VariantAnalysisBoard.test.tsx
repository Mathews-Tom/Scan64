import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiRequestError } from '../api/client';
import * as client from '../api/client';
import type { VariantPositionRead } from '../api/types';
import { VariantAnalysisBoard } from './VariantAnalysisBoard';
// Written by the server's own variant_position() (scan64.api.variants) for these
// move lists; regenerate it from there if the endpoint changes.
import positions from './variantPositions.test-fixtures.json';

const fixtures = positions as unknown as Record<string, Record<string, VariantPositionRead>>;

describe('VariantAnalysisBoard', () => {
  beforeEach(() => {
    vi.spyOn(client, 'ensurePlayerAuthorization').mockResolvedValue('player-1');
    vi.spyOn(ApiClient, 'variantPosition').mockImplementation(async (_player, { variant, moves }) => {
      const read = fixtures[variant][moves.join(' ')];
      if (!read) throw new ApiRequestError(`No fixture for ${moves.join(' ')}`, 422);
      return read;
    });
    vi.spyOn(ApiClient, 'analyzePosition').mockImplementation(async ({ fen, variant }) => ({
      fen, variant: variant ?? 'chess', lines: [],
    }));
  });
  afterEach(() => { vi.restoreAllMocks(); });

  const enter = (uci: string) => {
    fireEvent.change(screen.getByLabelText('Move (UCI)'), { target: { value: uci } });
    fireEvent.click(screen.getByRole('button', { name: 'Play move' }));
  };

  it('plays a crazyhouse drop from the pocket and asks for lines under crazyhouse', async () => {
    render(<VariantAnalysisBoard variant="crazyhouse" />);
    for (const uci of ['e2e4', 'd7d5', 'e4d5', 'd8d5']) {
      await screen.findByText(/to move\./);
      enter(uci);
    }
    const whiteHand = await screen.findByRole('group', { name: 'White pieces in hand' });
    await waitFor(() => expect(whiteHand).toHaveTextContent('pawn ×1'));
    expect(screen.getByRole('group', { name: 'Black pieces in hand' }).querySelector('button')).toBeDisabled();
    enter('p@e6');
    // python-chess writes a pawn drop without its letter.
    await screen.findByRole('button', { name: '3. @e6' });
    expect(ApiClient.analyzePosition).toHaveBeenLastCalledWith(expect.objectContaining({ variant: 'crazyhouse' }));
    expect(screen.getByRole('group', { name: 'White pieces in hand' })).toHaveTextContent('none');
  });

  it('counts the checks each side still needs in three-check', async () => {
    render(<VariantAnalysisBoard variant="3check" />);
    for (const uci of ['e2e4', 'e7e5', 'f1c4', 'b8c6', 'c4f7']) {
      await screen.findByText(/to move\./);
      enter(uci);
    }
    await waitFor(() => expect(screen.getByTestId('remaining-checks')).toHaveTextContent('White needs 2, Black needs 3.'));
    expect(screen.getByTestId('variant-position-status')).toHaveTextContent('Black to move. Check.');
  });

  it('refuses a move that is not legal under the variant', async () => {
    render(<VariantAnalysisBoard variant="crazyhouse" />);
    await screen.findByText('White to move.');
    enter('e2e5');
    expect(await screen.findByRole('alert')).toHaveTextContent('e2e5 is not a legal Crazyhouse move here.');
    expect(ApiClient.variantPosition).toHaveBeenCalledTimes(1);
  });

  it('names the engine problem and keeps the board usable when no engine declares the variant', async () => {
    vi.mocked(ApiClient.analyzePosition).mockRejectedValue(new ApiRequestError(
      'Failed to analyse position: Analysing crazyhouse needs an engine that declares it as a variant', 503));
    render(<VariantAnalysisBoard variant="crazyhouse" />);
    const lines = screen.getByTestId('variant-engine-lines');
    await waitFor(() => expect(lines).toHaveTextContent('Engine unavailable. Failed to analyse position: Analysing crazyhouse needs an engine'));
    enter('e2e4');
    await screen.findByText('Black to move.');
  });

  it('a move from an earlier position replaces the moves after it', async () => {
    render(<VariantAnalysisBoard variant="3check" />);
    for (const uci of ['e2e4', 'e7e5']) {
      await screen.findByText(/to move\./);
      enter(uci);
    }
    await screen.findByRole('button', { name: 'e5' });
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await screen.findByText('Black to move.');
    enter('e7e5');
    await screen.findByText('White to move.');
    expect(screen.getAllByRole('listitem').filter(item => item.closest('[aria-label="Move list"]'))).toHaveLength(2);
  });
});
