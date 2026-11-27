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
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ method: 'PUT', body: JSON.stringify({ model: 'local:b', consent: true, voice: 'coach' }) });
    expect(consent).not.toBeChecked();
  });

  it('sends the chosen narrator voice with the grant and shows the saved voice', async () => {
    owner();
    fetchMock.mockResolvedValueOnce(response({ mode: 'disabled', model: null, voice: null }))
      .mockResolvedValueOnce(response({ available: true, models: ['local:a'] }))
      .mockResolvedValueOnce(response({ mode: 'local', model: 'local:a', voice: 'terse' }));
    render(<LocalExplanationSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Detect local Ollama' }));
    const voice = await screen.findByRole('combobox', { name: 'Narrator voice' });
    expect(voice).toHaveValue('coach');
    expect(screen.getAllByRole('option').map(option => option.getAttribute('value'))).toEqual(['local:a', 'coach', 'terse', 'playful']);
    fireEvent.change(voice, { target: { value: 'terse' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Enable local wording' }));
    expect(await screen.findByText('Enabled for new explanations using local:a with the terse voice.')).toBeInTheDocument();
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ method: 'PUT', body: JSON.stringify({ model: 'local:a', consent: true, voice: 'terse' }) });
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

  it.each(['focus', 'storage'])('refreshes the same owner persisted status on %s without clearing the last confirmed status', async eventType => {
    owner();
    const { promise: pendingRefresh, resolve: finishRefresh } = Promise.withResolvers<Response>();
    fetchMock.mockResolvedValueOnce(response({ mode: 'disabled', model: null }))
      .mockReturnValueOnce(pendingRefresh);
    render(<LocalExplanationSettings />);
    await screen.findByText('Disabled. Explanations use the built-in templates.');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Detect local Ollama' })).toBeEnabled());

    fireEvent(window, new Event(eventType));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText('Disabled. Explanations use the built-in templates.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Detect local Ollama' })).toBeDisabled();
    expect(fetchMock.mock.calls[1][0]).toBe('/v1/players/player%2Fone/explanation-settings');
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'GET' });

    await act(async () => { finishRefresh(response({ mode: 'local', model: 'local:external' })); });
    expect(await screen.findByText('Enabled for new explanations using local:external.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Disable local wording' })).toBeEnabled();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.map(call => call[1]?.method)).toEqual(['GET', 'GET']);
  });

  it('restores revocation after a refresh settles during a transient owner change', async () => {
    owner();
    const { promise, resolve } = Promise.withResolvers<Response>();
    fetchMock.mockResolvedValueOnce(response({ mode: 'local', model: 'local:initial' }))
      .mockReturnValueOnce(promise)
      .mockResolvedValueOnce(response({ mode: 'local', model: 'local:refreshed' }))
      .mockResolvedValueOnce(response({ mode: 'disabled', model: null }));
    render(<LocalExplanationSettings />);
    const revocation = await screen.findByRole('button', { name: 'Disable local wording' });
    await waitFor(() => expect(revocation).toBeEnabled());

    fireEvent(window, new Event('focus'));
    expect(revocation).toBeDisabled();
    // The parent has not rendered the intermediate owner when the request settles.
    owner('other');
    await act(async () => { resolve(response({ mode: 'local', model: 'local:stale' })); });
    owner();
    fireEvent(window, new StorageEvent('storage', { key: 'scan64_player_id' }));

    expect(await screen.findByText('Enabled for new explanations using local:refreshed.')).toBeInTheDocument();
    expect(screen.queryByText(/local:stale/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Disable local wording' }));
    expect(await screen.findByText('Disabled. Explanations use the built-in templates.')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith('/v1/players/player%2Fone/explanation-settings', expect.objectContaining({
      method: 'PUT', body: JSON.stringify({ model: null, consent: false }),
    }));
  });

  it('defers and coalesces same-owner refreshes until the pending save finishes', async () => {
    owner();
    const { promise: pendingSave, resolve: finishSave } = Promise.withResolvers<Response>();
    const { promise: pendingRefresh, resolve: finishRefresh } = Promise.withResolvers<Response>();
    fetchMock.mockResolvedValueOnce(response({ mode: 'disabled', model: null }))
      .mockResolvedValueOnce(response({ available: true, models: ['local:a'] }))
      .mockReturnValueOnce(pendingSave)
      .mockReturnValueOnce(pendingRefresh);
    render(<LocalExplanationSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Detect local Ollama' }));
    const consent = await screen.findByRole('checkbox');
    fireEvent.click(consent);
    fireEvent.click(screen.getByRole('button', { name: 'Enable local wording' }));
    const saveSignal = fetchMock.mock.calls[2][1]?.signal;

    fireEvent(window, new Event('focus'));
    fireEvent(window, new Event('storage'));
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(saveSignal?.aborted).toBe(false);
    expect(consent).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Enable local wording' })).toBeDisabled();

    await act(async () => { finishSave(response({ mode: 'local', model: 'local:a' })); });
    expect(await screen.findByText('Enabled for new explanations using local:a.')).toBeInTheDocument();
    expect(screen.queryByText('Disabled. Explanations use the built-in templates.')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.map(call => call[1]?.method)).toEqual(['GET', 'POST', 'PUT', 'GET']);
    await act(async () => { finishRefresh(response({ mode: 'local', model: 'local:other' })); });
    expect(await screen.findByText('Enabled for new explanations using local:other.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Disable local wording' })).toBeEnabled();
    expect(saveSignal?.aborted).toBe(false);
  });

  it('shows unknown persisted status after failed enablement and allows immediate revocation without new consent', async () => {
    owner();
    fetchMock.mockResolvedValueOnce(response({ mode: 'disabled', model: null }))
      .mockResolvedValueOnce(response({ available: true, models: ['local:a'] }))
      .mockRejectedValueOnce(new Error('save response lost'))
      .mockResolvedValueOnce(response({ mode: 'disabled', model: null }));
    render(<LocalExplanationSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Detect local Ollama' }));
    const consent = await screen.findByRole('checkbox');
    fireEvent.click(consent);
    fireEvent.click(screen.getByRole('button', { name: 'Enable local wording' }));

    expect(await screen.findByText(/Persisted wording status is unknown/)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Could not confirm saved settings');
    expect(screen.queryByText('Disabled. Explanations use the built-in templates.')).not.toBeInTheDocument();
    expect(screen.queryByText(/Enabled for new explanations using/)).not.toBeInTheDocument();
    expect(consent).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Enable local wording' })).toBeDisabled();
    const disable = screen.getByRole('button', { name: 'Disable local wording' });
    expect(disable).toBeEnabled();
    fireEvent.click(disable);

    expect(await screen.findByText('Disabled. Explanations use the built-in templates.')).toBeInTheDocument();
    expect(screen.queryByText(/Persisted wording status is unknown/)).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(disable).toBeDisabled();
    expect(fetchMock.mock.calls.map(call => call[1]?.method)).toEqual(['GET', 'POST', 'PUT', 'PUT']);
    expect(fetchMock.mock.calls[3][1]?.body).toBe(JSON.stringify({ model: null, consent: false }));
  });

  it('shows unknown persisted status after failed revocation and permits retry without detection', async () => {
    owner();
    const { promise: pendingSave, reject: failSave } = Promise.withResolvers<Response>();
    const { promise: pendingRefresh, resolve: finishRefresh } = Promise.withResolvers<Response>();
    fetchMock.mockResolvedValueOnce(response({ mode: 'local', model: 'local:a' }))
      .mockReturnValueOnce(pendingSave)
      .mockReturnValueOnce(pendingRefresh)
      .mockResolvedValueOnce(response({ mode: 'disabled', model: null }));
    render(<LocalExplanationSettings />);
    await screen.findByText('Enabled for new explanations using local:a.');
    const disable = screen.getByRole('button', { name: 'Disable local wording' });
    await waitFor(() => expect(disable).toBeEnabled());
    fireEvent.click(disable);
    const saveSignal = fetchMock.mock.calls[1][1]?.signal;
    fireEvent(window, new Event('focus'));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(saveSignal?.aborted).toBe(false);
    await act(async () => { failSave(new Error('revoke response lost')); });

    expect(await screen.findByText(/Persisted wording status is unknown/)).toBeInTheDocument();
    expect(screen.queryByText(/Enabled for new explanations using/)).not.toBeInTheDocument();
    expect(screen.queryByText('Disabled. Explanations use the built-in templates.')).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(disable).toBeEnabled();
    fireEvent.click(disable);
    const refreshSignal = fetchMock.mock.calls[2][1]?.signal;
    await act(async () => { finishRefresh(response({ mode: 'local', model: 'local:a' })); });
    expect(refreshSignal?.aborted).toBe(true);

    expect(await screen.findByText('Disabled. Explanations use the built-in templates.')).toBeInTheDocument();
    expect(screen.queryByText(/Persisted wording status is unknown/)).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.map(call => call[1]?.method)).toEqual(['GET', 'PUT', 'GET', 'PUT']);
    expect(fetchMock.mock.calls[3][1]?.body).toBe(JSON.stringify({ model: null, consent: false }));
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
