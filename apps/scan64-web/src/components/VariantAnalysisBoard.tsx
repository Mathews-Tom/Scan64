import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Key } from 'chessground/types';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId } from '../api/client';
import type { PositionAnalysisLine, VariantName, VariantPositionRead } from '../api/types';
import { VARIANT_NAMES } from '../variants';
import { Button, Field, Panel } from './Primitives';
import 'chessground/assets/chessground.base.css';
import './Board.css';
import { useBoardFit } from './useBoardFit';
import '../pieces.css';
import './WorkingScreens.css';
import './AnalysisScreen.css';

const PIECE_NAMES: Record<string, string> = { p: 'pawn', n: 'knight', b: 'bishop', r: 'rook', q: 'queen', k: 'king' };
const POCKET_ORDER = ['p', 'n', 'b', 'r', 'q'] as const;

type Position =
  | { state: 'loading' }
  // ``prefix``: the moves this read is the position after, joined by spaces.
  | { state: 'ready'; read: VariantPositionRead; prefix: string }
  | { state: 'error'; message: string };

type Evaluation =
  | { state: 'idle' }
  | { state: 'loading' }
  | { state: 'ready'; fen: string; lines: PositionAnalysisLine[] }
  | { state: 'error'; message: string };

/** The placement field Chessground reads: no pockets, no promoted-piece marks. */
function boardFen(fen: string): string {
  return fen.split(' ')[0].split('[')[0].replace(/~/g, '');
}

/** Occupied squares of a FEN's placement, a8 to h1, for reading without the picture. */
function piecesBySquare(fen: string): { square: string; color: 'White' | 'Black'; name: string }[] {
  const pieces: { square: string; color: 'White' | 'Black'; name: string }[] = [];
  boardFen(fen).split('/').forEach((rank, index) => {
    let file = 0;
    for (const symbol of rank) {
      if (/\d/.test(symbol)) { file += Number(symbol); continue; }
      pieces.push({
        square: `${'abcdefgh'[file]}${8 - index}`,
        color: symbol === symbol.toUpperCase() ? 'White' : 'Black',
        name: PIECE_NAMES[symbol.toLowerCase()],
      });
      file += 1;
    }
  });
  return pieces;
}

function evaluationLabel(line: PositionAnalysisLine): string {
  if (line.mate !== null) return `M${line.mate}`;
  const pawns = (line.score_cp ?? 0) / 100;
  return `${pawns > 0 ? '+' : ''}${pawns.toFixed(2)}`;
}

function outcomeText(read: VariantPositionRead): string {
  const { result, termination } = read.outcome!;
  const winner = result === '1-0' ? 'White wins' : result === '0-1' ? 'Black wins' : 'Draw';
  return `${winner} (${termination.replace(/_/g, ' ')}).`;
}

/**
 * The Analysis Board under a variant's rules. Legality, FEN, SAN, pockets and
 * check counts come from the server's python-chess variant boards; the engine
 * lines need an engine that declares the variant.
 */
export function VariantAnalysisBoard({ variant }: { variant: Exclude<VariantName, 'chess'> }) {
  const element = useRef<HTMLDivElement>(null);
  const board = useRef<Api | null>(null);
  const generation = useRef(0);
  const [moves, setMoves] = useState<string[]>([]);
  const [selected, setSelected] = useState(0);
  const [sans, setSans] = useState<string[]>([]);
  const [position, setPosition] = useState<Position>({ state: 'loading' });
  const [evaluation, setEvaluation] = useState<Evaluation>({ state: 'idle' });
  const [pocketPiece, setPocketPiece] = useState<string | null>(null);
  const [promotions, setPromotions] = useState<string[] | null>(null);
  const [moveInput, setMoveInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  // Only the read of the moves now selected may take a move: while the next
  // position loads, the previous one's legal moves are not this position's.
  const read = position.state === 'ready' && position.prefix === moves.slice(0, selected).join(' ')
    ? position.read : null;
  const liveRead = useRef<VariantPositionRead | null>(null);
  useLayoutEffect(() => { liveRead.current = read; }, [read]);
  useBoardFit(element, board);

  // A pocket selection or a promotion choice belongs to the position it was made in.
  const goTo = useCallback((ply: number) => {
    setSelected(ply);
    setPocketPiece(null);
    setPromotions(null);
    setError(null);
  }, []);

  const play = useCallback((uci: string[]) => {
    setMoves(current => [...current.slice(0, selected), ...uci]);
    setSans(current => current.slice(0, selected));
    goTo(selected + uci.length);
  }, [selected, goTo]);

  // Position, then engine lines, for the selected ply; a newer request wins.
  useEffect(() => {
    const requestGeneration = ++generation.current;
    const current = () => generation.current === requestGeneration;
    const requested = moves.slice(0, selected);
    setPosition({ state: 'loading' });
    setEvaluation({ state: 'idle' });
    let positioned = false;
    void (async () => {
      let next: VariantPositionRead;
      try {
        const playerId = await ensurePlayerAuthorization(getOrCreatePlayerId());
        if (!current()) return;
        next = await ApiClient.variantPosition(playerId, { variant, moves: requested });
        if (!current()) return;
        setPosition({ state: 'ready', read: next, prefix: requested.join(' ') });
        positioned = true;
        if (requested.length === moves.length) setSans(next.san);
        if (next.outcome) return;
        setEvaluation({ state: 'loading' });
        const result = await ApiClient.analyzePosition({ player_id: playerId, fen: next.fen, variant });
        if (!current()) return;
        if (result.variant !== variant) throw new Error(`Engine searched ${result.variant}, not ${variant}. Retry analysis.`);
        setEvaluation({ state: 'ready', fen: next.fen, lines: result.lines });
      } catch (caught) {
        if (!current()) return;
        const message = caught instanceof Error ? caught.message : 'Could not read this position.';
        if (positioned) {
          setEvaluation({ state: 'error', message });
        } else {
          setPosition({ state: 'error', message });
        }
      }
    })();
    // A replaced or closed board starts no engine search for its old position.
    return () => { generation.current += 1; };
  }, [variant, moves, selected, attempt]);

  useEffect(() => {
    if (!element.current) return;
    board.current = Chessground(element.current, { fen: '8/8/8/8/8/8/8/8', movable: { free: false } });
    return () => { board.current?.destroy(); board.current = null; };
  }, []);

  useEffect(() => {
    if (!board.current) return;
    if (!read) {
      board.current.set({ movable: { color: undefined, dests: new Map(), events: { after: undefined } },
        events: { select: undefined } });
      return;
    }
    const dests = new Map<Key, Key[]>();
    for (const uci of read.legal_moves) {
      if (uci.includes('@')) continue;
      const from = uci.slice(0, 2) as Key;
      const to = uci.slice(2, 4) as Key;
      const targets = dests.get(from) ?? [];
      if (!targets.includes(to)) dests.set(from, [...targets, to]);
    }
    const last = moves[selected - 1];
    board.current.set({
      fen: boardFen(read.fen),
      turnColor: read.turn,
      check: read.check,
      lastMove: last && !last.includes('@') ? [last.slice(0, 2) as Key, last.slice(2, 4) as Key] : undefined,
      movable: {
        color: read.outcome ? undefined : read.turn,
        dests,
        free: false,
        events: {
          after: (from, to) => {
            if (liveRead.current !== read) return;
            const matching = read.legal_moves.filter(uci => uci.startsWith(`${from}${to}`));
            if (matching.length === 1) play(matching);
            else if (matching.length > 1) setPromotions(matching);
          },
        },
      },
      events: {
        select: key => {
          if (!pocketPiece || liveRead.current !== read) return;
          const drop = `${pocketPiece.toUpperCase()}@${key}`;
          if (read.legal_moves.includes(drop)) play([drop]);
          else setError(`A ${PIECE_NAMES[pocketPiece]} cannot be dropped on ${key}.`);
        },
      },
    });
  }, [read, moves, selected, pocketPiece, play]);

  const enterMove = () => {
    const uci = moveInput.trim();
    if (!read) return;
    // Drops are upper-case; promotions are lower-case.
    const normal = uci.includes('@') ? uci[0].toUpperCase() + uci.slice(1).toLowerCase() : uci.toLowerCase();
    if (!read.legal_moves.includes(normal)) {
      setError(`${uci} is not a legal ${VARIANT_NAMES[variant]} move here.`);
      return;
    }
    setMoveInput('');
    play([normal]);
  };

  const pockets = read?.pockets;
  return <div className="board-workspace">
    <div className="board-workspace__board">
      <div className="coach-board">
        <div className="coach-board__frame">
          <div ref={element} data-testid="variant-analysis-board" aria-hidden="true"
            aria-busy={position.state === 'loading'} className="coach-board__ground" />
        </div>
        <p role="status" data-testid="variant-position-status">
          {position.state === 'error' ? '' : read === null ? 'Loading position…'
            : read.outcome ? outcomeText(read)
              : `${read.turn === 'white' ? 'White' : 'Black'} to move.${read.check ? ' Check.' : ''}`}
        </p>
        {read && <>
          <details>
            <summary>Read board position</summary>
            <p>Coordinates identify each occupied square. Board viewed from White.</p>
            <ul aria-label="Pieces by square">
              {piecesBySquare(read.fen).map(piece => <li key={piece.square}>
                {piece.square}: {piece.color} {piece.name}
              </li>)}
            </ul>
          </details>
          <details>
            <summary>Legal moves and notation help</summary>
            <p>UCI uses source and destination squares, with q, r, b or n for promotion; a drop is the piece letter, @ and the square, such as N@f3.</p>
            <p data-testid="variant-legal-moves">{read.legal_moves.join(', ') || 'No legal moves.'}</p>
          </details>
        </>}
      </div>
    </div>
    <Panel className="board-workspace__side working-controls" aria-label="Variant analysis controls">
      {error && <p role="alert">{error}</p>}
      {position.state === 'error' && <>
        <p role="alert">{position.message}</p>
        <Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>
      </>}
      {read?.remaining_checks && <section className="working-controls__section" aria-label="Checks to win">
        <h3>Checks to win</h3>
        <p data-testid="remaining-checks">White needs {read.remaining_checks.white}, Black needs {read.remaining_checks.black}.</p>
      </section>}
      {pockets && <section className="working-controls__section" aria-label="Pieces in hand">
        <h3>Pieces in hand</h3>
        {(['white', 'black'] as const).map(side => {
          const hand = pockets[side];
          const pieces = POCKET_ORDER.filter(letter => (hand[letter] ?? 0) > 0);
          return <div key={side} className="working-actions" role="group" aria-label={`${side === 'white' ? 'White' : 'Black'} pieces in hand`}>
            <span>{side === 'white' ? 'White' : 'Black'}:</span>
            {pieces.length === 0 && <span>none</span>}
            {pieces.map(letter => <Button key={letter}
              aria-pressed={read.turn === side && pocketPiece === letter}
              disabled={read.turn !== side || read.outcome !== null}
              onClick={() => setPocketPiece(current => current === letter ? null : letter)}>
              {PIECE_NAMES[letter]} ×{hand[letter]}
            </Button>)}
          </div>;
        })}
        {pocketPiece && <p role="status">Select a square to drop the {PIECE_NAMES[pocketPiece]}.</p>}
      </section>}
      {promotions && <section className="working-controls__section" aria-label="Promotion">
        <h3>Promote to</h3>
        <div className="working-actions">
          {promotions.map(uci => <Button key={uci} disabled={!read} onClick={() => { if (read) play([uci]); }}>{PIECE_NAMES[uci[4]]}</Button>)}
          <Button onClick={() => { setPromotions(null); board.current?.set({ fen: read ? boardFen(read.fen) : undefined }); }}>Cancel</Button>
        </div>
      </section>}
      <section className="working-controls__section">
        <h3>Enter a move</h3>
        <p>UCI, such as e2e4, e7e8q, or a drop such as N@f3.</p>
        <Field label="Move (UCI)" id="variant-move" type="text" value={moveInput}
          onChange={event => setMoveInput(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter') enterMove(); }} />
        <Button onClick={enterMove} disabled={!read || read.outcome !== null}>Play move</Button>
      </section>
      <section className="working-controls__section" aria-label="Moves">
        <h3>Moves</h3>
        <div className="working-actions" role="group" aria-label="Replay navigation">
          <Button disabled={selected === 0} onClick={() => goTo(0)}>First</Button>
          <Button disabled={selected === 0} onClick={() => goTo(selected - 1)}>Previous</Button>
          <Button disabled={selected >= moves.length} onClick={() => goTo(selected + 1)}>Next</Button>
          <Button disabled={selected >= moves.length} onClick={() => goTo(moves.length)}>Last</Button>
        </div>
        <p>A move from an earlier position replaces the moves after it.</p>
        <ol className="analysis-variations" aria-label="Move list">
          {moves.map((uci, index) => <li key={index}>
            <Button aria-current={selected === index + 1 ? 'step' : undefined}
              onClick={() => goTo(index + 1)}>{index % 2 === 0 ? `${index / 2 + 1}. ` : ''}{sans[index] ?? uci}</Button>
          </li>)}
        </ol>
      </section>
      <section className="working-controls__section" data-testid="variant-engine-lines" aria-label="Engine evaluation"
        aria-busy={evaluation.state === 'loading'}>
        <h3>Engine evaluation</h3>
        <p>White perspective: positive favours White. Needs an engine that plays {VARIANT_NAMES[variant]}, such as Fairy-Stockfish.</p>
        {evaluation.state === 'loading' && <p role="status">Analysing current position… You can keep moving.</p>}
        {evaluation.state === 'error' && <>
          <p role="alert">{evaluation.message}</p>
          <Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>
        </>}
        {read?.outcome && <p>The game has ended; there is nothing to analyse.</p>}
        {evaluation.state === 'ready' && <ol className="analysis-engine-lines">
          {evaluation.lines.map((line, index) => <li key={index}>
            <strong aria-label={`Line ${index + 1} evaluation ${evaluationLabel(line)}`}>{evaluationLabel(line)}</strong>
            <div className="working-actions" aria-label={`Engine line ${index + 1}`}>
              {line.moves.map((move, moveIndex) => <Button key={moveIndex}
                aria-label={`Play line ${index + 1} through ${moveIndex + 1}: ${move.san}`}
                onClick={() => {
                  if (read?.fen !== evaluation.fen) return;
                  play(line.moves.slice(0, moveIndex + 1).map(item => item.uci));
                }}>{move.san}</Button>)}
            </div>
          </li>)}
        </ol>}
        <p>Explanations, diagnosis, lessons and puzzles cover standard chess only.</p>
      </section>
    </Panel>
  </div>;
}
