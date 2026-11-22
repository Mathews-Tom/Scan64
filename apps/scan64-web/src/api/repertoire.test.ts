import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RepertoireApi } from './repertoire';
import { apiFetch } from '../platform';

vi.mock('../platform', () => ({ apiBase: () => '/v1', apiFetch: vi.fn() }));

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('scan64_player_id', 'repertoire-owner');
  localStorage.setItem('scan64_player_token:repertoire-owner', 'owner-token');
  vi.mocked(apiFetch).mockReset();
});
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

describe('repertoire platform boundary', () => {
  it('uses platform fetch and the authorized owner for import, playback and review', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ok: true, json: async () => [] } as unknown as Response);
    await RepertoireApi.list();
    await RepertoireApi.importPgn('1. e4 *');
    const attempt = { attempt_id: 'stable-id', node_id: 1, submitted_move: 'e7e5' };
    await RepertoireApi.review('owned/id', attempt);
    expect(apiFetch).toHaveBeenNthCalledWith(1, '/v1/repertoires', { method: 'GET', headers: { Authorization: 'Bearer owner-token' } });
    expect(apiFetch).toHaveBeenNthCalledWith(2, '/v1/repertoires', { method: 'POST', headers: { Authorization: 'Bearer owner-token', 'Content-Type': 'application/json' }, body: JSON.stringify({ pgn: '1. e4 *' }) });
    expect(apiFetch).toHaveBeenNthCalledWith(3, '/v1/repertoires/owned%2Fid/attempts', { method: 'POST', headers: { Authorization: 'Bearer owner-token', 'Content-Type': 'application/json' }, body: JSON.stringify(attempt) });
    for (const [, options] of vi.mocked(apiFetch).mock.calls) {
      expect(options?.headers).not.toHaveProperty('Idempotency-Key');
    }
  });

  it.each([404, 409, 422])('preserves status %s and actionable server validation without replay', async status => {
    vi.mocked(apiFetch).mockResolvedValue({ ok: false, status, json: async () => ({ detail: 'Invalid PGN move near e8' }) } as unknown as Response);
    await expect(RepertoireApi.importPgn('1. e8 *')).rejects.toMatchObject({ status, message: 'Invalid PGN move near e8' });
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });
});
