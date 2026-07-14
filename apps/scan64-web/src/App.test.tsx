import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import App from './App';
import { ApiClient } from './api/client';
import type { LessonSpec } from './api/types';

vi.mock('./api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api/client')>();
  return {
    ...actual,
    ApiClient: {
      ...actual.ApiClient,
      getTrainingSession: vi.fn(),
    }
  };
});

describe('App', () => {
  it('renders welcome message by default', () => {
    render(<App />);
    expect(screen.getByText('Welcome to Scan64')).toBeInTheDocument();
  });

  it('navigates to play screen', () => {
    render(<App />);
    fireEvent.click(screen.getByText('Play Game'));
    expect(screen.getByTestId('play-screen')).toBeInTheDocument();
  });

  it('navigates to import screen', () => {
    render(<App />);
    fireEvent.click(screen.getByText('Import PGN'));
    expect(screen.getByTestId('pgn-import')).toBeInTheDocument();
  });

  it('navigates to daily training screen and loads a lesson', async () => {
    const mockLesson: LessonSpec = {
      schema_version: '1.0',
      lesson_id: 'lesson-1',
      source: {
        kind: 'position',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
      },
      diagnosis: {
        primary: 'tactics.fork',
        secondary: [],
        confidence: 0.9,
        evidence_refs: []
      },
      objective: {
        type: 'find_best_move',
        instruction: 'Find the winning tactic.'
      },
      interaction: {} as unknown as LessonSpec['interaction'],
      hints: [],
      explanation: {} as unknown as LessonSpec['explanation'],
      verification: {} as unknown as LessonSpec['verification'],
      mastery: {} as unknown as LessonSpec['mastery']
    };

    vi.spyOn(ApiClient, 'getTrainingSession').mockResolvedValue([mockLesson]);

    render(<App />);
    fireEvent.click(screen.getByText('Daily Training'));
    
    // Should show loading then the instruction
    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();
    
    await waitFor(() => {
      expect(screen.getByTestId('lesson-instruction')).toBeInTheDocument();
    });
    
    expect(screen.getByText('Find the winning tactic.')).toBeInTheDocument();
  });
});

  it('navigates to profile screen', async () => {
    // Mock global fetch for the ProfileScreen
    const mockFetch = vi.fn((url: string) => {
      if (url.includes('/progress')) {
        return Promise.resolve({ json: () => Promise.resolve({ skills: [] }) });
      }
      if (url.includes('/evidence')) {
        return Promise.resolve({ json: () => Promise.resolve({ evidence_items: [] }) });
      }
      if (url.includes('/patterns')) {
        return Promise.resolve({ json: () => Promise.resolve({ recurring_habits: [] }) });
      }
      return Promise.resolve({ json: () => Promise.resolve({}) });
    }) as any;
    
    vi.stubGlobal('fetch', mockFetch);

    render(<App />);
    fireEvent.click(screen.getByText('Profile'));
    
    await waitFor(() => {
      expect(screen.getByTestId('profile-screen')).toBeInTheDocument();
    });
    
    expect(screen.getByText(/Player Profile/i)).toBeInTheDocument();
    
    vi.unstubAllGlobals();
  });
