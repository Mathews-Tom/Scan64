import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LocalExplanationSettings } from './LocalExplanationSettings';
import { apiFetch } from '../platform';

vi.mock('../platform', () => ({ apiBase: () => '/v1', apiFetch: vi.fn() }));
const fetchMock = vi.mocked(apiFetch);

function owner(id = 'player/one') {
  localStorage.setItem('scan64_player_id', id);
  localStorage.setItem(`scan64_player_token:${id}`, `test-token-${id}`);
}

function response(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

beforeEach(() => {
  localStorage.clear();
  fetchMock.mockReset();
});
afterEach(cleanup);

describe('Local explanation settings', () => {
  it('does not create an identity or request anything without an existing bearer', () => {
    const view = render(<LocalExplanationSettings />);
    expect(screen.getByText(/Disabled. Sign in/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    localStorage.setItem('scan64_player_id', 'no-token');
    view.rerender(<LocalExplanationSettings />);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(localStorage.getItem('scan64_player_token:no-token')).toBeNull();
  });

  it('probes only explicitly and requires fresh consent for each model change', async () => {
    owner();
    fetchMock.mockResolvedValueOnce(response({ mode: 'disabled', model: null }))
      .mockResolvedValueOnce(response({ available: true, models: ['local:a', 'local:b'] }))
      .mockResolvedValueOnce(response({ mode: 'local', model: 'local:b' }));
    render(<LocalExplanationSettings />);
    const detect = await screen.findByRole('button', { name: 'Detect local Ollama' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/v1/players/player%2Fone/explanation-settings');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'GET', headers: { Authorization: ['Bearer', 'test-token-player/one'].join(' ') } });
    fireEvent.click(detect);
    const consent = await screen.findByRole('checkbox');
    expect(consent).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Enable local wording' })).toBeDisabled();
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST' });
    expect(fetchMock.mock.calls[1][0]).toBe('/v1/players/player%2Fone/explanation-settings/probe');
    expect(fetchMock.mock.calls[1][1]).not.toHaveProperty('body');
    fireEvent.click(consent);
    fireEvent.change(screen.getByRole('combobox', { name: 'Local model' }), { target: { value: 'local:b' } });
    expect(consent).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Enable local wording' })).toBeDisabled();
    const disclosure = screen.getByText(/can log or forward this evidence/);
    expect(disclosure).toBeVisible();
    expect(disclosure).toHaveTextContent('Existing lessons are not rewritten');
    fireEvent.click(consent);
    fireEvent.click(screen.getByRole('button', { name: 'Enable local wording' }));
    expect(await screen.findByText('Enabled for new explanations using local:b.')).toBeInTheDocument();
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ method: 'PUT', body: JSON.stringify({ model: 'local:b', consent: true }) });
    expect(consent).not.toBeChecked();
  });

  it('reads persisted enablement on remount and disables without probing', async () => {
    owner();
    fetchMock.mockResolvedValueOnce(response({ mode: 'local', model: 'local:a' }))
      .mockResolvedValueOnce(response({ mode: 'local', model: 'local:a' }))
      .mockResolvedValueOnce(response({ mode: 'disabled', model: null }));
    const first = render(<LocalExplanationSettings />);
    await screen.findByText('Enabled for new explanations using local:a.');
    first.unmount();
    render(<LocalExplanationSettings />);
    await screen.findByText('Enabled for new explanations using local:a.');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Disable local wording' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Disable local wording' }));
    await screen.findByText('Disabled. Explanations use the built-in templates.');
    expect(fetchMock.mock.calls.map(call => call[1]?.method)).toEqual(['GET', 'GET', 'PUT']);
    expect(fetchMock.mock.calls[2][1]?.body).toBe(JSON.stringify({ model: null, consent: false }));
  });

  it.each([
    [{ available: false, models: [] }, 'Local Ollama is unavailable.'],
    [{ available: true, models: [] }, 'No compatible local models found.'],
  ])('explains detection state without offering consent: %j', async (probe, message) => {
    owner();
    fetchMock.mockResolvedValueOnce(response({ mode: 'disabled', model: null })).mockResolvedValueOnce(response(probe));
    render(<LocalExplanationSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Detect local Ollama' }));
    await screen.findByText(new RegExp(message));
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('keeps operator configuration read-only', async () => {
    owner();
    fetchMock.mockResolvedValue(response({ mode: 'operator', model: 'managed' }));
    render(<LocalExplanationSettings />);
    await screen.findByText(/Managed by the operator/);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('abandons an old owner probe and an unmounted load', async () => {
    owner('one');
    const { promise: pendingProbe, resolve: finishProbe } = Promise.withResolvers<Response>();
    fetchMock.mockResolvedValueOnce(response({ mode: 'disabled', model: null }))
      .mockReturnValueOnce(pendingProbe)
      .mockResolvedValueOnce(response({ mode: 'disabled', model: null }));
    const view = render(<LocalExplanationSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Detect local Ollama' }));
    const oldSignal = fetchMock.mock.calls[1][1]?.signal;
    owner('two');
    fireEvent(window, new Event('storage'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    await act(async () => { finishProbe(response({ available: true, models: ['old-owner-model'] })); });
    expect(oldSignal?.aborted).toBe(true);
    expect(screen.queryByText('old-owner-model')).not.toBeInTheDocument();
    view.unmount();
    const { promise: pendingLoad, resolve: finishLoad } = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(pendingLoad);
    const next = render(<LocalExplanationSettings />);
    const loadSignal = fetchMock.mock.calls[3][1]?.signal;
    next.unmount();
    await act(async () => { finishLoad(response({ mode: 'local', model: 'late-model' })); });
    expect(loadSignal?.aborted).toBe(true);
    expect(screen.queryByText(/late-model/)).not.toBeInTheDocument();
  });

  it('shows actionable failures without automatic retries', async () => {
    owner();
    fetchMock.mockResolvedValueOnce(response({ mode: 'disabled', model: null })).mockRejectedValueOnce(new Error('offline'));
    render(<LocalExplanationSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Detect local Ollama' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Start local Ollama');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
