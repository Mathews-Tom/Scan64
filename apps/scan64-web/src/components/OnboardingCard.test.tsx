import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApiClient } from '../api/client';
import type { DeclaredLevel } from '../api/types';
import type * as ClientModule from '../api/client';
import { OnboardingCard } from './OnboardingCard';
import { onboardingFixture } from './strength.test-fixtures';

vi.mock('../api/client', async importOriginal => {
  const actual = await importOriginal<typeof ClientModule>();
  return { ...actual, ApiClient: { getPlayerOnboarding: vi.fn(), updatePlayerOnboarding: vi.fn() } };
});

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  localStorage.setItem('scan64_player_id', 'player-1');
  localStorage.setItem('scan64_player_token:player-1', 'kept-token');
  vi.mocked(ApiClient.getPlayerOnboarding).mockResolvedValue(onboardingFixture);
});

describe('OnboardingCard', () => {
  it.each<[DeclaredLevel, string, number]>([
    ['beginner', 'Beginner', 2], ['developing', 'Developing', 3], ['club', 'Club', 5],
    ['advanced', 'Advanced', 6], ['not_sure', 'Not sure', 1],
  ])('declares %s and immediately shows the server suggestion without puzzle placement', async (level, label, suggestion) => {
    vi.mocked(ApiClient.updatePlayerOnboarding).mockResolvedValue({
      ...onboardingFixture, status: 'in_progress', declared_level: level, suggested_bot_level: suggestion,
      mapping_version: 'declared-level-v1', intro_started_at: '2026-11-13T12:00:00Z',
    });
    const onChange = vi.fn();
    render(<OnboardingCard onChange={onChange} />);
    expect(await screen.findByRole('group', { name: 'How would you describe your chess experience?' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: label }));
    fireEvent.click(screen.getByRole('button', { name: 'Save level and show suggestion' }));
    expect(await screen.findByText(`Suggested bot level ${suggestion}. You can change it in Play.`)).toBeInTheDocument();
    expect(ApiClient.updatePlayerOnboarding).toHaveBeenCalledWith('player-1', { action: 'declare', declared_level: level });
    expect(screen.getByRole('heading', { name: 'Your starting point' })).toHaveFocus();
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '0');
    expect(onChange).toHaveBeenCalledOnce();
    expect(localStorage.getItem('scan64_player_id')).toBe('player-1');
    expect(localStorage.getItem('scan64_player_token:player-1')).toBe('kept-token');
  });

  it('leaves existing players opt-in without automatically declaring or starting puzzles', async () => {
    vi.mocked(ApiClient.getPlayerOnboarding).mockResolvedValue({ ...onboardingFixture, status: 'not_offered' });
    render(<OnboardingCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Set up my starting point' }));
    expect(screen.getByRole('radio', { name: 'Not sure' })).toBeChecked();
    expect(ApiClient.updatePlayerOnboarding).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: 'Play now' })).toHaveAttribute('href', '/play');
  });

  it.each(['skip', 'continue_later'] as const)('keeps identity and server progress for %s and reload', async action => {
    const saved = { ...onboardingFixture, status: action === 'skip' ? 'skipped' as const : 'pending' as const };
    vi.mocked(ApiClient.updatePlayerOnboarding).mockResolvedValue(saved);
    const view = render(<OnboardingCard />);
    const name = action === 'skip' ? 'Skip introduction' : 'Continue later';
    fireEvent.click(await screen.findByRole('button', { name }));
    await waitFor(() => expect(ApiClient.updatePlayerOnboarding).toHaveBeenCalledWith('player-1', { action }));
    await screen.findByText(/Your identity and progress are kept/);
    view.unmount();
    vi.mocked(ApiClient.getPlayerOnboarding).mockResolvedValue(saved);
    render(<OnboardingCard />);
    await screen.findByText(/Declared level: Not declared/);
    expect(localStorage.getItem('scan64_player_id')).toBe('player-1');
    expect(localStorage.getItem('scan64_player_token:player-1')).toBe('kept-token');
  });

  it('uses endpoint progress across days and the existing daily puzzle navigation', async () => {
    const record = { ...onboardingFixture, status: 'in_progress' as const, intro_started_at: '2026-11-12T10:00:00Z', intro_started: 2, intro_closed: 2 };
    vi.mocked(ApiClient.getPlayerOnboarding).mockResolvedValue(record);
    const onNavigate = vi.fn();
    const view = render(<OnboardingCard onNavigate={onNavigate} />);
    expect(await screen.findByText('2 of 5 introductory puzzles closed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: 'Start/resume daily puzzles' }));
    expect(onNavigate).toHaveBeenCalledWith('/puzzles');
    expect(ApiClient.updatePlayerOnboarding).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: 'Play now' })).toBeInTheDocument();
    view.unmount();
    vi.mocked(ApiClient.getPlayerOnboarding).mockResolvedValue({ ...record, intro_started: 4, intro_closed: 3 });
    render(<OnboardingCard />);
    expect(await screen.findByText('3 of 5 introductory puzzles closed')).toBeInTheDocument();
    expect(screen.getByText(/Short or empty daily supply/)).toBeInTheDocument();
  });

  it('keeps a failed declaration retryable without fabricating a suggestion', async () => {
    vi.mocked(ApiClient.updatePlayerOnboarding).mockRejectedValue(new Error('Connection lost'));
    render(<OnboardingCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Save level and show suggestion' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Connection lost');
    expect(screen.getByRole('button', { name: 'Save level and show suggestion' })).toBeEnabled();
    expect(screen.getByText(/Suggested bot level: None yet/)).toBeInTheDocument();
  });
});
