import { useCallback, useEffect, useRef, useState } from 'react';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';
import { Chess } from 'chess.js';
import { ApiClient } from '../api/client';
import type { PositionRead } from '../api/types';
import type { Key } from 'chessground/types';

interface AnalysisScreenProps {
  gameId?: string;
}

export function AnalysisScreen({ gameId }: AnalysisScreenProps) {
  const boardRef = useRef<HTMLDivElement>(null);
  const [cg, setCg] = useState<Api | null>(null);
  const [chess] = useState(() => new Chess());
  const [positions, setPositions] = useState<PositionRead[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fenInput, setFenInput] = useState(chess.fen());

  const updateFenInput = useCallback(() => setFenInput(chess.fen()), [chess]);

  useEffect(() => {
    if (gameId) {
      setLoading(true);
      ApiClient.getPositions(gameId)
        .then((data) => {
          setPositions(data);
          if (data.length > 0) {
            chess.load(data[0].fen);
            setCurrentIndex(0);
            updateFenInput();
          }
        })
        .catch((error: unknown) =>
          setError(error instanceof Error ? error.message : 'Failed to load analysis')
        )
        .finally(() => setLoading(false));
    }
  }, [gameId, chess, updateFenInput]);

  useEffect(() => {
    if (boardRef.current && !cg) {
      const api = Chessground(boardRef.current, {
        fen: chess.fen(),
        movable: {
          color: 'both',
          free: false,
          dests: getDests(chess),
        },
        events: {
          move: (orig, dest) => {
            try {
              chess.move({ from: orig, to: dest, promotion: 'q' });
              api.set({
                fen: chess.fen(),
                movable: { dests: getDests(chess) },
              });
              updateFenInput();
            } catch {
              api.set({ fen: chess.fen() });
            }
          },
        },
      });
      setCg(api);
    }

    return () => {
      if (cg) {
        cg.destroy();
        setCg(null);
      }
    };
  }, [cg, chess, updateFenInput]);

  useEffect(() => {
    const currentPosition = positions[currentIndex];
    if (cg && currentPosition) {
      chess.load(currentPosition.fen);
      cg.set({
        fen: chess.fen(),
        movable: { dests: getDests(chess) },
      });
    }
  }, [cg, chess, currentIndex, positions]);

  const goNext = () => {
    if (currentIndex < positions.length - 1) {
      const nextIdx = currentIndex + 1;
      chess.load(positions[nextIdx].fen);
      setCurrentIndex(nextIdx);
      updateFenInput();
    }
  };

  const goPrev = () => {
    if (currentIndex > 0) {
      const prevIdx = currentIndex - 1;
      chess.load(positions[prevIdx].fen);
      setCurrentIndex(prevIdx);
      updateFenInput();
    }
  };

  const handleLoadFen = () => {
    try {
      chess.load(fenInput);
      if (cg) cg.set({ fen: chess.fen(), movable: { dests: getDests(chess) } });
      setError(null);
    } catch {
      setError('Invalid FEN');
    }
  };



  const currentPos = positions[currentIndex];
  const multiPv = currentPos?.analysis?.raw_result || [];

  return (
    <div className="analysis-screen">
      <h2>Analysis Board</h2>
      {error && <div style={{ color: 'red' }}>{error}</div>}
      {loading && <div>Loading...</div>}

      <div style={{ display: 'flex', gap: '2rem' }}>
        <div ref={boardRef} style={{ width: '400px', height: '400px' }} />

        <div className="analysis-sidebar" style={{ width: '300px' }}>
          

          <div className="fen-setup" style={{ marginBottom: '1rem' }}>
            <h3>FEN Setup</h3>
            <input
              type="text"
              value={fenInput}
              onChange={(e) => setFenInput(e.target.value)}
              placeholder="Paste FEN here"
              style={{ width: '100%', marginBottom: '0.5rem' }}
            />
            <button onClick={handleLoadFen}>Load FEN</button>
          </div>


          <div className="controls" style={{ marginBottom: '1rem' }}>
            <button onClick={goPrev} disabled={currentIndex === 0}>
              Previous
            </button>
            <span style={{ margin: '0 1rem' }}>
              Position {currentIndex + 1} of {Math.max(1, positions.length)}
            </span>
            <button onClick={goNext} disabled={currentIndex >= positions.length - 1}>
              Next
            </button>
          </div>

          <div className="multipv-container" data-testid="multipv-lines">
            <h3>Engine Evaluation</h3>
            {multiPv.length === 0 ? (
              <p>No engine analysis available for this position.</p>
            ) : (
              <ul style={{ listStyle: 'none', padding: 0 }}>
                {multiPv.map((line, i) => (
                  <li key={i} style={{ marginBottom: '0.5rem' }}>
                    <strong>{line.score_mate ? `M${line.score_mate}` : (line.score_cp !== undefined ? (line.score_cp / 100).toFixed(2) : '?')}</strong>
                    {' '}- {line.pv.slice(0, 4).join(' ')}{line.pv.length > 4 ? '...' : ''}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function getDests(chess: Chess): Map<Key, Key[]> {
  const dests = new Map<Key, Key[]>();
  chess.moves({ verbose: true }).forEach((m) => {
    const from = m.from as Key;
    const to = m.to as Key;
    if (!dests.has(from)) dests.set(from, []);
    dests.get(from)!.push(to);
  });
  return dests;
}
