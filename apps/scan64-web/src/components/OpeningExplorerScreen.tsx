import { useEffect, useRef, useState } from 'react';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Key } from 'chessground/types';
import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';
import { Chess } from 'chess.js';

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

function getDests(chess: Chess): Map<Key, Key[]> {
  const dests = new Map<Key, Key[]>();
  chess.moves({ verbose: true }).forEach((move) => {
    const orig = move.from as Key;
    const dest = move.to as Key;
    if (!dests.has(orig)) {
      dests.set(orig, []);
    }
    dests.get(orig)!.push(dest);
  });
  return dests;
}

function syncBoard(chess: Chess, board: Api): void {
  board.set({
    fen: chess.fen(),
    turnColor: chess.turn() === 'w' ? 'white' : 'black',
    movable: {
      color: chess.turn() === 'w' ? 'white' : 'black',
      dests: getDests(chess),
    },
  });
}

export function OpeningExplorerScreen() {
  const boardRef = useRef<HTMLDivElement>(null);
  const [cg, setCg] = useState<Api | null>(null);
  const [selectedFamily, setSelectedFamily] = useState<OpeningFamily | null>(null);
  const [error, setError] = useState<string | null>(null);
  const chessRef = useRef(new Chess());

  useEffect(() => {
    if (boardRef.current && !cg) {
      const api = Chessground(boardRef.current, {
        fen: chessRef.current.fen(),
        turnColor: 'white',
        movable: {
          color: 'white',
          free: false,
          dests: getDests(chessRef.current),
        }
      });
      setCg(api);
    }
  }, [cg]);

  useEffect(() => {
    if (!cg) return;

    cg.set({
      movable: {
        events: {
          after: (orig, dest, _metadata) => {
            try {
              chessRef.current.move({ from: orig, to: dest, promotion: 'q' });
              setError(null);
            } catch (error: unknown) {
              setError(error instanceof Error ? error.message : 'Could not apply opening move.');
            }
            syncBoard(chessRef.current, cg);
          },
        },
      },
    });
  }, [cg]);

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
      if (cg) syncBoard(chessRef.current, cg);
      return;
    }

    setSelectedFamily(family);
    setError(null);
    if (cg) syncBoard(chessRef.current, cg);
  };

  return (
    <div className="opening-explorer" data-testid="opening-explorer">
      <h2>Opening Explorer</h2>
      {error && <p role="alert">{error}</p>}
      <div style={{ display: 'flex', gap: '20px' }}>
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
        <div>
          <div ref={boardRef} style={{ width: '400px', height: '400px' }} data-testid="chessground-board" />
        </div>
        {selectedFamily && (
          <div data-testid="family-details">
            <h3>{selectedFamily.name}</h3>
            <p><strong>Instructional Purpose:</strong> {selectedFamily.instructional_purpose}</p>
            <h4>Missions</h4>
            <ul>
              {selectedFamily.missions.map(mission => (
                <li key={mission.id}>
                  <strong>{mission.invariant_type}:</strong> {mission.description}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
