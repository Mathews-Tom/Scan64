import { useEffect, useRef, useState } from 'react';
import { Chess } from 'chess.js';
import { ApiClient } from '../api/client';
import { CoachBoard } from './CoachBoard';
import { resolveMove } from './moveEntry';

interface OpeningMission {
  id: string;
  description: string;
  invariant_type: string;
}

interface OpeningFamily {
  name: string;
  instructional_purpose: string;
  moves: string[];
  missions: OpeningMission[];
}

const SEED_FAMILIES: OpeningFamily[] = [
  {
    name: "Italian Game",
    instructional_purpose: "Rapid development, central tension, king safety.",
    moves: ["e4", "e5", "Nf3", "Nc6", "Bc4"],
    missions: [
      {
        id: "italian_dev_minor",
        description: "Develop both minor pieces before starting an attack.",
        invariant_type: "minor_pieces_developed"
      }
    ]
  },
  {
    name: "Queen's Gambit",
    instructional_purpose: "Pawn tension, space, minority structures, development.",
    moves: ["d4", "d5", "c4"],
    missions: [
      {
        id: "qg_central_tension",
        description: "Maintain central pawn tension without capturing prematurely.",
        invariant_type: "pawn_tension_maintained"
      }
    ]
  },
  {
    name: "Caro-Kann Defense",
    instructional_purpose: "Central response styles and defensive planning.",
    moves: ["e4", "c6"],
    missions: [
      {
        id: "ck_solid_structure",
        description: "Establish a solid pawn structure without early weaknesses.",
        invariant_type: "solid_pawn_structure"
      }
    ]
  }
];


export function OpeningExplorerScreen() {
  const [selectedFamily, setSelectedFamily] = useState<OpeningFamily | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [recordedMissionId, setRecordedMissionId] = useState<string | null>(null);
  const chessRef = useRef(new Chess());
  const [fen, setFen] = useState(chessRef.current.fen());
  const [status, setStatus] = useState('');


  useEffect(() => {
    void ApiClient.getTrainingSession()
      .then(session => setSessionId(session.session_id))
      .catch(() => setError('Could not create an opening study session.'));
  }, []);

  const handleMove = (uci: string) => {
    const resolved = resolveMove(chessRef.current, uci);
    if (resolved.kind !== 'move') throw new Error('Could not apply opening move.');
    const { from, to, promotion } = resolved.move;
    chessRef.current.move({ from, to, promotion });
    setFen(chessRef.current.fen());
    setError(null);
  };

  const loadFamily = (family: OpeningFamily) => {
    chessRef.current.reset();

    try {
      for (const move of family.moves) {
        chessRef.current.move(move);
      }
    } catch (error: unknown) {
      chessRef.current.reset();
      setSelectedFamily(null);
      setError(
        error instanceof Error ? `Could not load ${family.name}: ${error.message}` : `Could not load ${family.name}.`,
      );
      setFen(chessRef.current.fen());
      return;
    }

    setSelectedFamily(family);
    setError(null);
    setFen(chessRef.current.fen());
    setStatus(`${family.name} loaded.`);
  };

  const recordMission = async (mission: OpeningMission) => {
    if (sessionId === null) {
      setError('Opening study session is still loading.');
      return;
    }
    try {
      await ApiClient.recordLessonAttempt({
        session_id: sessionId,
        lesson_id: `opening-mission:${mission.id}`,
        source_kind: 'opening_mission',
        elapsed_ms: 0,
        hints_used: 0,
      });
      setRecordedMissionId(mission.id);
      setError(null);
      setStatus('Opening mission recorded.');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not record opening mission.');
    }
  };

  return (
    <div className="opening-explorer" data-testid="opening-explorer">
      <h2>Opening Explorer</h2>
      {error && <p role="alert">{error}</p>}
      <p role="status">{status}</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px' }}>
        <div>
          <h3>Families</h3>
          <ul style={{ listStyle: 'none', padding: 0 }}>
            {SEED_FAMILIES.map(family => (
              <li key={family.name}>
                <button onClick={() => loadFamily(family)}>{family.name}</button>
              </li>
            ))}
          </ul>
        </div>
        <CoachBoard fen={fen} pgn={chessRef.current.pgn()} onMove={handleMove} testId="chessground-board" />
        {selectedFamily && (
          <div data-testid="family-details">
            <h3>{selectedFamily.name}</h3>
            <p><strong>Instructional Purpose:</strong> {selectedFamily.instructional_purpose}</p>
            <h4>Missions</h4>
            <ul>
              {selectedFamily.missions.map(mission => (
                <li key={mission.id}>
                  <strong>{mission.invariant_type}:</strong> {mission.description}
                  <button onClick={() => void recordMission(mission)} disabled={sessionId === null}>
                    {recordedMissionId === mission.id ? 'Recorded' : 'Record mission'}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
