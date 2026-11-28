import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Mock } from 'vitest';
import { act, cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PgnImportScreen } from './PgnImportScreen';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId, getStoredPlayerId } from '../api/client';
import type { AnalysisJobRead, OnlineGameImportResponse } from '../api/types';
import { readProgress } from '../api/progress';

vi.mock('../api/client', () => ({
  ApiClient: {
    createGame: vi.fn(),
    importOnlineGames: vi.fn(),
    getPlayerProfile: vi.fn(),
    createPlayer: vi.fn(),
    getLearningOpportunities: vi.fn(),
    createAnalysisJob: vi.fn(),
    getAnalysisJob: vi.fn(),
    watchAnalysisJob: vi.fn(),
  },
  ensurePlayerAuthorization: vi.fn(),
  getOrCreatePlayerId: vi.fn(() => 'player-1'),
  getStoredPlayerId: vi.fn(),
}));

function analysisStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(value) { controller = value; },
    cancel,
  });
  return {
    body,
    cancel,
    response: new Response(body, { headers: { 'Content-Type': 'application/x-ndjson' } }),
    push: (status: string) => controller.enqueue(new TextEncoder().encode(`${JSON.stringify({ id: 'job-1', game_id: 'game-1', status })}\n`)),
    close: () => controller.close(),
  };
}

describe('PgnImportScreen', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    events = analysisStream();
    vi.mocked(ApiClient.createGame).mockResolvedValue({ variant: 'chess', id: 'game-1', pgn: '...', white: 'w', black: 'b', result: '*' });
    vi.mocked(ApiClient.createAnalysisJob).mockResolvedValue({ id: 'job-1', game_id: 'game-1', status: 'pending' });
    vi.mocked(ApiClient.getLearningOpportunities).mockResolvedValue({ session_id: 'study-1', lessons: [], next_cursor: null });
    vi.mocked(ApiClient.watchAnalysisJob).mockImplementation((_jobId, onUpdate, signal) => readProgress<AnalysisJobRead>(
      events.response, signal, onUpdate, job => job.status === 'completed' || job.status === 'failed',
    ));
    vi.mocked(ApiClient.getPlayerProfile).mockResolvedValue({ player_id: 'player-1', rating: 1500, display_name: null });
    vi.mocked(getOrCreatePlayerId).mockReturnValue('player-1');
    vi.mocked(getStoredPlayerId).mockReturnValue('player-1');
    vi.mocked(ensurePlayerAuthorization).mockResolvedValue('recovered-player-2');
  });
  let events: {
    body: ReadableStream<Uint8Array>;
    cancel: Mock;
    response: Response;
    push: (status: string) => void;
    close: () => void;
  };
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  const emptyImport = { imported_count: 0, duplicate_count: 0, skipped_count: 0, games: [], message: 'No public standard games found.' };
  const fillOnline = (source = 'lichess', count = '10') => {
    fireEvent.change(screen.getByLabelText('Source'), { target: { value: source } });
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'Alice_1' } });
    fireEvent.change(screen.getByLabelText('Number of games'), { target: { value: count } });
  };

  it('does not authorize or import on mount, edits, source switches or profile completion', async () => {
    render(<PgnImportScreen />);
    fillOnline('chesscom', '3');
    fireEvent.change(screen.getByLabelText('Source'), { target: { value: 'lichess' } });
    await act(async () => {});
    expect(ApiClient.getPlayerProfile).toHaveBeenCalledOnce();
    expect(ensurePlayerAuthorization).not.toHaveBeenCalled();
    expect(ApiClient.importOnlineGames).not.toHaveBeenCalled();
    expect(ApiClient.createGame).not.toHaveBeenCalled();
  });

  it.each([['lichess', '1'], ['chesscom', '50']])('submits %s with bounded count %s only after authorization', async (source, count) => {
    const authorization = Promise.withResolvers<string>();
    vi.mocked(ensurePlayerAuthorization).mockReturnValue(authorization.promise);
    vi.mocked(ApiClient.importOnlineGames).mockResolvedValue(emptyImport);
    render(<PgnImportScreen />);
    fillOnline(source, count);
    fireEvent.click(screen.getByRole('button', { name: 'Import online games' }));
    expect(ensurePlayerAuthorization).toHaveBeenCalledWith('player-1');
    expect(ApiClient.importOnlineGames).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Importing online games…' })).toBeDisabled();
    await act(async () => { authorization.resolve('recovered-player-2'); });
    await screen.findByText(emptyImport.message);
    expect(ApiClient.importOnlineGames).toHaveBeenCalledExactlyOnceWith({ source, username: 'Alice_1', max_games: Number(count) });
    expect(ApiClient.createAnalysisJob).not.toHaveBeenCalled();
  });

  it.each(['', '0', '51', '2.5', '-1'])('rejects invalid count %s before authorization', async count => {
    render(<PgnImportScreen />);
    fillOnline('lichess', count);
    fireEvent.submit(screen.getByRole('button', { name: 'Import online games' }).closest('form')!);
    expect(await screen.findByRole('alert')).toHaveTextContent('whole number of games from 1 to 50');
    expect(ensurePlayerAuthorization).not.toHaveBeenCalled();
    expect(ApiClient.importOnlineGames).not.toHaveBeenCalled();
  });

  it.each(['a', 'https://lichess.org/Alice', 'alice.name', 'alice name', 'a'.repeat(31)])('rejects unsafe username %s', async username => {
    render(<PgnImportScreen />);
    fillOnline();
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: username } });
    fireEvent.click(screen.getByRole('button', { name: 'Import online games' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('not a URL');
    expect(ensurePlayerAuthorization).not.toHaveBeenCalled();
  });

  it('reports counts, prevents duplicate submits and reviews each new game without another job', async () => {
    const pending = Promise.withResolvers<OnlineGameImportResponse>();
    vi.mocked(ApiClient.importOnlineGames).mockReturnValue(pending.promise);
    const onOpenGame = vi.fn();
    render(<PgnImportScreen onOpenGame={onOpenGame} />);
    fillOnline();
    const form = screen.getByRole('button', { name: 'Import online games' }).closest('form')!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(ApiClient.importOnlineGames).toHaveBeenCalledOnce());
    await act(async () => {
      pending.resolve({ imported_count: 2, duplicate_count: 3, skipped_count: 1, message: 'Skipped a non-standard game.',
        games: [
          { id: 'online-1', white: 'Alice_1', black: 'Bob', result: '1-0', owner_color: 'white', analysis_job_id: 'job-1' },
          { id: 'online-2', white: 'Carol', black: 'Alice_1', result: '0-1', owner_color: 'black', analysis_job_id: 'job-2' },
        ] });
    });
    expect(screen.getByText('2 new · 3 duplicate · 1 skipped')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Review Alice_1 vs Bob' }));
    fireEvent.click(screen.getByRole('button', { name: 'Review Carol vs Alice_1' }));
    expect(onOpenGame.mock.calls).toEqual([['online-1'], ['online-2']]);
    expect(ApiClient.createAnalysisJob).not.toHaveBeenCalled();
    expect(ApiClient.createGame).not.toHaveBeenCalled();
    vi.mocked(ApiClient.importOnlineGames).mockResolvedValue({ ...emptyImport, duplicate_count: 2, message: 'All games already imported.' });
    fireEvent.click(screen.getByRole('button', { name: 'Import online games' }));
    await screen.findByText('0 new · 2 duplicate · 0 skipped');
    expect(screen.queryByRole('button', { name: /^Review / })).not.toBeInTheDocument();
  });

  it.each([
    [0, 'No public standard games found.'],
    [3, 'Skipped 3 non-standard games; no standard games found.'],
  ])('shows empty and variant-only results (%s skipped)', async (skipped_count, message) => {
    vi.mocked(ApiClient.importOnlineGames).mockResolvedValue({ ...emptyImport, skipped_count, message });
    render(<PgnImportScreen onOpenGame={vi.fn()} />);
    fillOnline();
    fireEvent.click(screen.getByRole('button', { name: 'Import online games' }));
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByText(`0 new · 0 duplicate · ${skipped_count} skipped`)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Review / })).not.toBeInTheDocument();
  });

  it.each(['Lichess username not found.', 'Chess.com rate limit; try again later.', 'Upstream network unavailable.', 'Malformed public PGN.'])('preserves actionable error and supports retry: %s', async message => {
    vi.mocked(ApiClient.importOnlineGames).mockRejectedValueOnce(new Error(message)).mockResolvedValueOnce(emptyImport);
    render(<PgnImportScreen />);
    fillOnline('chesscom');
    fireEvent.click(screen.getByRole('button', { name: 'Import online games' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(`Chess.com: ${message}`);
    fireEvent.click(screen.getByRole('button', { name: 'Import online games' }));
    await screen.findByText(emptyImport.message);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(ApiClient.importOnlineGames).toHaveBeenCalledTimes(2);
  });

  it.each(['White', 'Black'] as const)('pre-selects %s from the player id', async header => {
    render(<PgnImportScreen />);
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: `[${header} "player-1"]\n\n1. e4 *` } });
    await waitFor(() => expect((screen.getByLabelText('Your side') as HTMLSelectElement).value).toBe(header.toLowerCase()));
  });

  it('pre-selects the display name and allows an explicit override', async () => {
    vi.mocked(ApiClient.getPlayerProfile).mockResolvedValue({ player_id: 'player-1', rating: 1500, display_name: 'Alice' });
    render(<PgnImportScreen />);
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: '[Black "Alice"]\n\n1. e4 *' } });
    await waitFor(() => expect((screen.getByLabelText('Your side') as HTMLSelectElement).value).toBe('black'));
    fireEvent.change(screen.getByLabelText('Your side'), { target: { value: 'white' } });
    expect((screen.getByLabelText('Your side') as HTMLSelectElement).value).toBe('white');
  });

  it.each([
    ['Anonymous placeholder', '[Black "Anonymous"]'],
    ['both names match', '[White "player-1"]\n[Black "Alice"]'],
  ])('keeps White as the default for %s', async (_, pgn) => {
    const displayName = pgn.includes('Anonymous') ? 'Anonymous' : 'Alice';
    const profile = Promise.withResolvers<{ player_id: string; rating: number; display_name: string }>();
    vi.mocked(ApiClient.getPlayerProfile).mockReturnValue(profile.promise);
    render(<PgnImportScreen />);
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: pgn } });
    await act(async () => {
      profile.resolve({ player_id: 'player-1', rating: 1500, display_name: displayName });
      await profile.promise;
    });
    expect((screen.getByLabelText('Your side') as HTMLSelectElement).value).toBe('white');
  });

  it('applies a display name delivered by a controlled profile promise', async () => {
    const profile = Promise.withResolvers<{ player_id: string; rating: number; display_name: string }>();
    vi.mocked(ApiClient.getPlayerProfile).mockReturnValue(profile.promise);
    render(<PgnImportScreen />);
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: '[Black "Alice"]' } });
    expect((screen.getByLabelText('Your side') as HTMLSelectElement).value).toBe('white');
    await act(async () => {
      profile.resolve({ player_id: 'player-1', rating: 1500, display_name: 'Alice' });
      await profile.promise;
    });
    expect((screen.getByLabelText('Your side') as HTMLSelectElement).value).toBe('black');
  });

  it('preserves a manual choice after PGN edits and a late profile response', async () => {
    const profile = Promise.withResolvers<{ player_id: string; rating: number; display_name: string }>();
    vi.mocked(ApiClient.getPlayerProfile).mockReturnValue(profile.promise);
    render(<PgnImportScreen />);
    fireEvent.change(screen.getByLabelText('Your side'), { target: { value: 'black' } });
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: '[White "player-1"]' } });
    await act(async () => {
      profile.resolve({ player_id: 'player-1', rating: 1500, display_name: 'Alice' });
      await profile.promise;
    });
    expect((screen.getByLabelText('Your side') as HTMLSelectElement).value).toBe('black');
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: '[White "Alice"]' } });
    expect((screen.getByLabelText('Your side') as HTMLSelectElement).value).toBe('black');
  });

  it('saves a variant game and says it is not analysed, without starting a job', async () => {
    vi.mocked(ApiClient.createGame).mockResolvedValueOnce({ variant: 'crazyhouse', id: 'game-2', pgn: '...', white: 'w', black: 'b', result: '*' });
    render(<PgnImportScreen />);
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: '[Variant "Crazyhouse"] 1. e4 d5 *' } });
    fireEvent.click(screen.getByTestId('import-btn'));
    expect(await screen.findByTestId('import-notice')).toHaveTextContent(
      'Saved the Crazyhouse game to Your Games. Game analysis, lessons and diagnosis cover standard chess only, so it is not analysed.');
    expect(ApiClient.createAnalysisJob).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('renders textarea and button', () => {
    render(<PgnImportScreen />);
    expect(screen.getByTestId('pgn-textarea')).toBeTruthy();
    expect(screen.getByTestId('import-btn')).toBeTruthy();
  });

  it.each([
    ['tactics.fork', 'tactics.fork'],
    ['threat_processing.allowed_mate', 'Allowed checkmate'],
  ])('imports PGN and displays %s without renaming other codes', async (primary, label) => {
    const onExploreAnalysis = vi.fn();
    vi.mocked(ApiClient.createGame).mockResolvedValueOnce({ variant: 'chess',
      id: 'game-1', pgn: '...', white: 'w', black: 'b', result: '*'
    });
    vi.mocked(ApiClient.createAnalysisJob).mockResolvedValueOnce({
      id: 'job-1', game_id: 'game-1', status: 'pending'
    });
    vi.mocked(ApiClient.getLearningOpportunities).mockResolvedValueOnce({
      session_id: 'study-1',
      lessons: [
        {
          schema_version: '0.2.0', lesson_id: 'les-1',
          source: { kind: 'player_game', fen: 'fen' },
          diagnosis: { primary, secondary: [], confidence: 0.9, evidence_refs: [] },
          objective: { type: 'play', instruction: 'win' },
          interaction: { input: 'move', maximum_attempts: 1, accepted_moves: [] },
          hints: [], explanation: { text: 'exp' },
          verification: { status: 'verified', engine: 'e', engine_binary_digest: 'd', nodes: 1, multipv: 1, verified_at: 'now' },
          mastery: { skill_key: 'key', delta: 0.1 },
        },
      ],
      next_cursor: null,
    });

    render(<PgnImportScreen onExploreAnalysis={onExploreAnalysis} />);
    const textarea = screen.getByTestId('pgn-textarea');
    fireEvent.change(textarea, { target: { value: '1. e4 e5' } });
    
    fireEvent.click(screen.getByTestId('import-btn'));
    await waitFor(() => expect(ApiClient.watchAnalysisJob).toHaveBeenCalledOnce());
    expect(ApiClient.getLearningOpportunities).not.toHaveBeenCalled();
    expect(screen.getByTestId('import-btn')).toBeDisabled();
    await act(async () => { events.push('running'); });
    expect(screen.getByRole('status')).toHaveTextContent('Analyzing game (status: running)...');
    await act(async () => { events.push('completed'); });
    await waitFor(() => {
      expect(ensurePlayerAuthorization).toHaveBeenCalledWith('player-1');
      expect(ApiClient.createGame).toHaveBeenCalledWith({ owner_color: 'white',  pgn: '1. e4 e5', player_id: 'recovered-player-2' });
      expect(ApiClient.createAnalysisJob).toHaveBeenCalledWith('game-1');
      expect(ApiClient.watchAnalysisJob).toHaveBeenCalledWith('job-1', expect.any(Function), expect.any(AbortSignal));
      expect(ApiClient.getAnalysisJob).not.toHaveBeenCalled();
      expect(ApiClient.getLearningOpportunities).toHaveBeenCalledWith('game-1');
      expect(screen.getByTestId('lessons-list').textContent).toContain(label);
    }, { timeout: 3000 });

    fireEvent.click(screen.getByTestId('explore-analysis-btn'));
    expect(onExploreAnalysis).toHaveBeenCalledWith('game-1');
  });

  it.each(['pending', 'running'])('subscribes once to a newly created %s job and waits for pushed completion', async status => {
    vi.mocked(ApiClient.createAnalysisJob).mockResolvedValueOnce({ id: 'job-1', game_id: 'game-1', status });
    render(<PgnImportScreen />);
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: '1. d4 d5' } });
    fireEvent.click(screen.getByTestId('import-btn'));
    await waitFor(() => expect(ApiClient.watchAnalysisJob).toHaveBeenCalledOnce());
    expect(ApiClient.getLearningOpportunities).not.toHaveBeenCalled();
    await act(async () => { events.push('running'); });
    expect(screen.getByRole('status')).toHaveTextContent('Analyzing game (status: running)...');
    expect(screen.getByTestId('import-btn')).toBeDisabled();
    await act(async () => { events.push('completed'); });
    await waitFor(() => expect(screen.getByTestId('import-btn')).toBeEnabled());
    expect(ApiClient.getLearningOpportunities).toHaveBeenCalledExactlyOnceWith('game-1');
    expect(ApiClient.getAnalysisJob).not.toHaveBeenCalled();
    expect(events.cancel).toHaveBeenCalledOnce();
    expect(events.body.locked).toBe(false);
  });

  it.each(['completed', 'failed'])('handles an already %s job without opening a subscription', async status => {
    vi.mocked(ApiClient.createAnalysisJob).mockResolvedValueOnce({ id: 'job-1', game_id: 'game-1', status });
    render(<PgnImportScreen onExploreAnalysis={vi.fn()} />);
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: '1. d4 d5' } });
    fireEvent.click(screen.getByTestId('import-btn'));
    if (status === 'failed') {
      expect(await screen.findByRole('alert')).toHaveTextContent('Analysis job failed');
      expect(ApiClient.getLearningOpportunities).not.toHaveBeenCalled();
      expect(screen.queryByTestId('explore-analysis-btn')).toBeNull();
    } else {
      expect(await screen.findByTestId('explore-analysis-btn')).toBeInTheDocument();
      expect(ApiClient.getLearningOpportunities).toHaveBeenCalledExactlyOnceWith('game-1');
    }
    expect(ApiClient.watchAnalysisJob).not.toHaveBeenCalled();
    expect(ApiClient.getAnalysisJob).not.toHaveBeenCalled();
    expect(screen.getByTestId('import-btn')).toBeEnabled();
  });

  it.each(['failed', 'disconnected'])('shows a %s analysis, never polls or reconnects, and allows an explicit retry', async outcome => {
    render(<PgnImportScreen onExploreAnalysis={vi.fn()} />);
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: '1. d4 d5' } });
    fireEvent.click(screen.getByTestId('import-btn'));
    await waitFor(() => expect(ApiClient.watchAnalysisJob).toHaveBeenCalledOnce());
    await act(async () => {
      events.push('running');
      if (outcome === 'failed') events.push('failed');
      else events.close();
    });
    expect(await screen.findByRole('alert')).toHaveTextContent(outcome === 'failed' ? 'Analysis job failed' : 'Progress stream disconnected before completion.');
    expect(screen.getByTestId('import-btn')).toBeEnabled();
    expect(ApiClient.getLearningOpportunities).not.toHaveBeenCalled();
    expect(screen.queryByTestId('explore-analysis-btn')).toBeNull();
    vi.useFakeTimers();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(ApiClient.watchAnalysisJob).toHaveBeenCalledOnce();
    expect(ApiClient.createAnalysisJob).toHaveBeenCalledOnce();
    expect(ApiClient.getAnalysisJob).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
    vi.useRealTimers();

    events = analysisStream();
    fireEvent.click(screen.getByTestId('import-btn'));
    await waitFor(() => expect(ApiClient.watchAnalysisJob).toHaveBeenCalledTimes(2));
    await act(async () => { events.push('completed'); });
    expect(await screen.findByTestId('explore-analysis-btn')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(ApiClient.createAnalysisJob).toHaveBeenCalledTimes(2);
    expect(ApiClient.getLearningOpportunities).toHaveBeenCalledExactlyOnceWith('game-1');
  });

  it('aborts and releases the pending subscription on unmount without fetching lessons', async () => {
    const view = render(<PgnImportScreen />);
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: '1. d4 d5' } });
    fireEvent.click(screen.getByTestId('import-btn'));
    await waitFor(() => expect(ApiClient.watchAnalysisJob).toHaveBeenCalledOnce());
    await act(async () => { events.push('running'); });
    const signal = vi.mocked(ApiClient.watchAnalysisJob).mock.calls[0][2];
    expect(signal.aborted).toBe(false);
    expect(events.body.locked).toBe(true);
    view.unmount();
    await act(async () => {});
    expect(signal.aborted).toBe(true);
    expect(events.cancel).toHaveBeenCalledExactlyOnceWith(signal.reason);
    expect(events.body.locked).toBe(false);
    expect(ApiClient.getLearningOpportunities).not.toHaveBeenCalled();
    expect(ApiClient.getAnalysisJob).not.toHaveBeenCalled();
  });

  it('does not subscribe when creation finishes after unmount', async () => {
    const creation = Promise.withResolvers<AnalysisJobRead>();
    vi.mocked(ApiClient.createAnalysisJob).mockReturnValueOnce(creation.promise);
    const view = render(<PgnImportScreen />);
    fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: '1. d4 d5' } });
    fireEvent.click(screen.getByTestId('import-btn'));
    await waitFor(() => expect(ApiClient.createAnalysisJob).toHaveBeenCalledOnce());
    view.unmount();
    await act(async () => { creation.resolve({ id: 'job-1', game_id: 'game-1', status: 'pending' }); });
    expect(ApiClient.watchAnalysisJob).not.toHaveBeenCalled();
    expect(ApiClient.getLearningOpportunities).not.toHaveBeenCalled();
    expect(events.body.locked).toBe(false);
  });
});
