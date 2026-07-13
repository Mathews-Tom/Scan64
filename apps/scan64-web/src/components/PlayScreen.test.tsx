import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PlayScreen } from './PlayScreen';
import { ApiClient } from '../api/client';

// Mock the API client
vi.mock('../api/client', () => ({
  ApiClient: {
    createPlaySession: vi.fn(),
    makePlaySessionMove: vi.fn(),
  },
}));

describe('PlayScreen', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders start button and board container', () => {
    render(<PlayScreen />);
    expect(screen.getByTestId('start-btn')).toBeTruthy();
    expect(screen.getByTestId('chessground-board')).toBeTruthy();
  });

  it('starts a game and updates session info', async () => {
    vi.mocked(ApiClient.createPlaySession).mockResolvedValueOnce({
      id: 'sess-123',
      fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      status: 'active',
      pgn: '',
    });

    render(<PlayScreen />);
    fireEvent.click(screen.getByTestId('start-btn'));

    await waitFor(() => {
      expect(ApiClient.createPlaySession).toHaveBeenCalledWith('1500');
      expect(screen.getByTestId('session-info').textContent).toContain('Status: active');
    });
  });

  it('shows error message if API fails', async () => {
    vi.mocked(ApiClient.createPlaySession).mockRejectedValueOnce(new Error('Network Error'));

    render(<PlayScreen />);
    fireEvent.click(screen.getByTestId('start-btn'));

    await waitFor(() => {
      expect(screen.getByText('Network Error')).toBeTruthy();
    });
  });
});
