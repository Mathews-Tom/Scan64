import { useEffect, useState, useRef } from 'react';
import { ApiClient } from '../api/client';
import type { FamousGameRead, PlaySessionRead } from '../api/types';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import { Chess } from 'chess.js';

export interface FamousGameStudyScreenProps {
  onPlayFromHere: (session: PlaySessionRead, fen: string) => void;
}

export function FamousGameStudyScreen({ onPlayFromHere }: FamousGameStudyScreenProps) {
  const [games, setGames] = useState<FamousGameRead[]>([]);
  const [selectedGame, setSelectedGame] = useState<FamousGameRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [playerId, setPlayerId] = useState('');

  const boardRef = useRef<HTMLDivElement>(null);
  const [cg, setCg] = useState<Api | null>(null);
  
  // Study state
  const chessRef = useRef(new Chess());
  const [currentFen, setCurrentFen] = useState(chessRef.current.fen());
  const [moveHistory, setMoveHistory] = useState<string[]>([]);
  const [currentMoveIndex, setCurrentMoveIndex] = useState(0);
  
  // Predict state
  const [isPredicting, setIsPredicting] = useState(false);
  const [hintsUsed, setHintsUsed] = useState(0);
  const [feedback, setFeedback] = useState<{message: string, isCorrect: boolean} | null>(null);

  useEffect(() => {
    // Generate simple player ID for tests
    const id = localStorage.getItem('scan64_player_id') || 'player-' + Math.random().toString(36).substring(7);
    localStorage.setItem('scan64_player_id', id);
    setPlayerId(id);

    ApiClient.getFamousGames()
      .then(g => {
        setGames(g);
        setLoading(false);
      })
      .catch(err => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  const selectGame = (game: FamousGameRead) => {
    setSelectedGame(game);
    const c = new Chess(game.payload.fen as string || 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1');
    chessRef.current = c;
    setCurrentFen(c.fen());
    
    // Parse moves (very simple space-separated parser for MVP)
    const rawMoves = (game.payload.moves as string || '').replace(/\d+\./g, '').trim().split(/\s+/).filter(m => m.length > 0);
    setMoveHistory(rawMoves);
    setCurrentMoveIndex(0);
    setIsPredicting(true);
    setHintsUsed(0);
    setFeedback(null);
  };

  useEffect(() => {
    if (selectedGame && boardRef.current && !cg) {
      const api = Chessground(boardRef.current, {
        fen: currentFen,
        movable: {
          color: chessRef.current.turn() === 'w' ? 'white' : 'black',
          free: false,
          dests: new Map(), // We calculate these in a real app, keeping simple here
        }
      });
      setCg(api);
    }
  }, [selectedGame, boardRef, cg, currentFen]);

  useEffect(() => {
    if (cg) {
      cg.set({ fen: currentFen, movable: { color: chessRef.current.turn() === 'w' ? 'white' : 'black' } });
    }
  }, [currentFen, cg]);

  const handlePredict = async (predictedMove: string) => {
    if (!selectedGame || currentMoveIndex >= moveHistory.length) return;
    
    const correctMove = moveHistory[currentMoveIndex];
    // Simple validation (in real app, would use chess.js move validation)
    const isCorrect = predictedMove === correctMove;
    
    setFeedback({
      message: isCorrect ? 'Correct!' : `Incorrect. The played move was ${correctMove}`,
      isCorrect
    });

    try {
      await ApiClient.recordFamousGameAttempt(selectedGame.id, {
        player_id: playerId,
        success: isCorrect,
        hint_assisted: hintsUsed > 0,
        response_payload: { move: predictedMove }
      });
    } catch (e) {
      console.error("Failed to record attempt", e);
    }

    if (isCorrect) {
      chessRef.current.move(correctMove);
      setCurrentFen(chessRef.current.fen());
      setCurrentMoveIndex(prev => prev + 1);
      setIsPredicting(currentMoveIndex + 1 < moveHistory.length);
      setHintsUsed(0);
      setTimeout(() => setFeedback(null), 2000);
    }
  };

  const handleShowHint = () => {
    setHintsUsed(prev => prev + 1);
    const correctMove = moveHistory[currentMoveIndex];
    const hintText = correctMove.substring(0, hintsUsed + 1);
    setFeedback({ message: `Hint: Starts with ${hintText}`, isCorrect: false });
  };
  
  const handleNextMove = () => {
     if (currentMoveIndex < moveHistory.length) {
       const move = moveHistory[currentMoveIndex];
       chessRef.current.move(move);
       setCurrentFen(chessRef.current.fen());
       setCurrentMoveIndex(prev => prev + 1);
       setIsPredicting(true);
       setFeedback(null);
       setHintsUsed(0);
     }
  };

  const handleContinuePlaying = async () => {
    try {
      const session = await ApiClient.createPlaySession({
        player_id: playerId,
        opponent_config: { engine_level: "1" }
      });
      onPlayFromHere(session, currentFen);
    } catch (e) {
      console.error(e);
    }
  };

  if (loading) return <div>Loading famous games...</div>;
  if (error) return <div>Error: {error}</div>;

  if (!selectedGame) {
    return (
      <div className="famous-games-list">
        <h2>Study Famous Games</h2>
        <ul>
          {games.map(game => (
            <li key={game.id}>
              <button onClick={() => selectGame(game)}>
                {game.payload.title as string}
              </button>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="famous-game-study" style={{ display: 'flex', gap: '20px' }}>
      <div className="board-section">
        <div ref={boardRef} style={{ width: '400px', height: '400px' }} />
      </div>
      <div className="controls-section">
        <h2>{selectedGame.payload.title as string}</h2>
        <div style={{ marginBottom: '10px' }}>
          Move {Math.floor(currentMoveIndex / 2) + 1} {currentMoveIndex % 2 === 0 ? 'White' : 'Black'} to move
        </div>
        
        {feedback && (
          <div style={{ padding: '10px', backgroundColor: feedback.isCorrect ? '#d4edda' : '#f8d7da', marginBottom: '10px' }}>
            {feedback.message}
          </div>
        )}

        {isPredicting && (
           <div className="predict-controls" style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
             <h3>Predict the next move</h3>
             <div style={{ display: 'flex', gap: '10px' }}>
               <input 
                 type="text" 
                 placeholder="e.g. e4 or Nf3" 
                 id="predict-input"
                 onKeyDown={(e) => {
                   if (e.key === 'Enter') {
                     handlePredict(e.currentTarget.value);
                     e.currentTarget.value = '';
                   }
                 }}
               />
               <button onClick={() => {
                 const input = document.getElementById('predict-input') as HTMLInputElement;
                 if (input.value) {
                   handlePredict(input.value);
                   input.value = '';
                 }
               }}>Submit</button>
             </div>
             <button onClick={handleShowHint}>Show Hint</button>
             <button onClick={handleNextMove}>Show Move</button>
           </div>
        )}
        
        {!isPredicting && currentMoveIndex >= moveHistory.length && (
          <div>
            <h3>Game Completed</h3>
          </div>
        )}

        <div style={{ marginTop: '20px' }}>
          <button onClick={handleContinuePlaying}>Continue from here against engine</button>
        </div>
        
        <div style={{ marginTop: '20px' }}>
          <button onClick={() => setSelectedGame(null)}>Back to Games</button>
        </div>
      </div>
    </div>
  );
}
