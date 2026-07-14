import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import App from './App';

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
  it('navigates to daily training screen', async () => {
    render(<App />);
    fireEvent.click(screen.getByText('Daily Training'));
    // DailyTrainingScreen will initially show loading or an error if fetch fails
    expect(await screen.findByTestId('error-message')).toBeInTheDocument();
  });
});
