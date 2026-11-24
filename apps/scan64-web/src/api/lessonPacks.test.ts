import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '../platform';
import { LessonPacksApi } from './lessonPacks';

vi.mock('../platform', () => ({ apiBase: () => '/v1', apiFetch: vi.fn() }));
beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('scan64_player_id', 'pack-owner');
  localStorage.setItem('scan64_player_token:pack-owner', 'owner-token');
  vi.mocked(apiFetch).mockReset();
  vi.mocked(apiFetch).mockResolvedValue({ ok: true, status: 200, json: async () => ({}) } as Response);
});
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
describe('lesson pack platform boundary', () => {
  it('uploads raw bytes with owner authorization and encodes resource ids', async () => {
    const file = new File(['{ "original": true }\n'], 'pack.json');
    await LessonPacksApi.install(file);
    expect(apiFetch).toHaveBeenLastCalledWith('/v1/lesson-packs', {
      method: 'POST', headers: { Authorization: 'Bearer owner-token', 'Content-Type': 'application/json' }, body: file,
    });
    await LessonPacksApi.list();
    await LessonPacksApi.get('pack/id');
    await LessonPacksApi.retry('pack/id');
    expect(apiFetch).toHaveBeenLastCalledWith('/v1/lesson-packs/pack%2Fid/retry', { method: 'POST', headers: { Authorization: 'Bearer owner-token' } });
    await LessonPacksApi.lesson('pack/id', 'lesson/id');
    expect(apiFetch).toHaveBeenLastCalledWith('/v1/lesson-packs/pack%2Fid/lessons/lesson%2Fid', { method: 'GET', headers: { Authorization: 'Bearer owner-token' } });
    await LessonPacksApi.check('pack/id', 'lesson/id', { ply: 0, hint: true });
    expect(apiFetch).toHaveBeenLastCalledWith('/v1/lesson-packs/pack%2Fid/lessons/lesson%2Fid/check', {
      method: 'POST', headers: { Authorization: 'Bearer owner-token', 'Content-Type': 'application/json' }, body: JSON.stringify({ ply: 0, hint: true }),
    });
  });
  it('rejects excessive bytes without making a request', async () => {
    await expect(LessonPacksApi.install(new File([new Uint8Array(512 * 1024 + 1)], 'large.json'))).rejects.toThrow('512 KiB');
    expect(apiFetch).not.toHaveBeenCalled();
  });
  it('handles an empty uninstall response', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ok: true, status: 204 } as Response);
    await expect(LessonPacksApi.uninstall('pack')).resolves.toBeUndefined();
    expect(apiFetch).toHaveBeenLastCalledWith('/v1/lesson-packs/pack', { method: 'DELETE', headers: { Authorization: 'Bearer owner-token' } });
  });
  it.each([404, 409, 422, 503])('preserves server errors (%s) without retries', async status => {
    vi.mocked(apiFetch).mockResolvedValue({ ok: false, status, json: async () => ({ detail: 'Pack rejected' }) } as Response);
    await expect(LessonPacksApi.retry('pack')).rejects.toMatchObject({ status, message: 'Pack rejected' });
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });
});
