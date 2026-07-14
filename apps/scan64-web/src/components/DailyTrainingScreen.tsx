import React, { useState, useEffect } from 'react';
import { ApiClient } from '../api/client';
import type { LessonSpec } from '../api/types';

export const DailyTrainingScreen: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sessionLessons, setSessionLessons] = useState<LessonSpec[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);

  useEffect(() => {
    const loadSession = async () => {
      try {
        setLoading(true);
        // In a real app we would call a specific endpoint for the training session
        // For the UI milestone, we fetch a few lessons to simulate the session
        const session = await ApiClient.getTrainingSession();
        
        if (session.length === 0) {
          setSessionLessons([]);
        } else {
          setSessionLessons(session);
        }
      } catch (err) {
        console.error('Failed to load training session:', err);
        setError('Failed to load training session');
      } finally {
        setLoading(false);
      }
    };

    loadSession();
  }, []);

  if (loading) {
    return <div data-testid="loading-indicator">Loading your daily training...</div>;
  }

  if (error) {
    return <div data-testid="error-message">{error}</div>;
  }

  if (sessionLessons.length === 0) {
    return <div>No training available for today.</div>;
  }
  
  if (currentIndex >= sessionLessons.length) {
    return (
      <div data-testid="session-complete">
        <h2>Training Complete!</h2>
        <p>You have completed all your scheduled lessons for today.</p>
        <button onClick={() => window.location.href = '/'}>Return Home</button>
      </div>
    );
  }

  const currentLesson = sessionLessons[currentIndex];
  const progressPercent = Math.round((currentIndex / sessionLessons.length) * 100);

  return (
    <div className="daily-training-screen">
      <div className="training-header">
        <h2>Daily Training</h2>
        <div className="progress-bar-container" data-testid="progress-bar-container">
          <div 
            className="progress-bar-fill" 
            style={{ width: `${progressPercent}%`, backgroundColor: '#4caf50', height: '10px' }}
            data-testid="progress-fill"
          ></div>
          <span className="progress-text">{currentIndex} / {sessionLessons.length}</span>
        </div>
      </div>
      
      <div className="lesson-container">
        <div className="lesson-board-placeholder" data-testid="lesson-board">
          {/* We would render a chessboard here using currentLesson.source.fen */}
          Board: {currentLesson.source.fen}
        </div>
        
        <div className="lesson-instruction" data-testid="lesson-instruction">
          {currentLesson.objective.instruction}
        </div>
        
        <div className="lesson-controls">
          <button 
            data-testid="next-lesson-button"
            onClick={() => setCurrentIndex(idx => idx + 1)}
          >
            {currentIndex === sessionLessons.length - 1 ? 'Finish Session' : 'Next Lesson'}
          </button>
        </div>
      </div>
    </div>
  );
};
