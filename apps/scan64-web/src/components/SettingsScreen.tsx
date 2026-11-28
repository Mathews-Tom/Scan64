import { FeedbackPanel } from './FeedbackPanel';
import { PuzzleLibraryPanel } from './PuzzleImport';
import { UpdatesPanel } from './Updates';
import { ProgressReset } from './ProgressReset';
import { LocalExplanationSettings } from './LocalExplanationSettings';
import { useEffect, useId, useState } from 'react';
import { ListRow, Panel, SegmentedControl, StatusLine } from './Primitives';
import { applyBoardAppearance, BOARD_THEME_OPTIONS, PIECE_SET_OPTIONS, readBoardAppearance, saveBoardChoice } from '../boardAppearance';
import type { BoardTheme, PieceSet } from '../boardAppearance';
import { readBoardSounds, saveBoardSounds } from '../boardSounds';
import { readHealthPanel, saveHealthPanel } from '../healthPanel';

import { APPEARANCE_STORAGE_KEY, applyAppearance, readAppearance } from '../appearance';
import type { Appearance } from '../appearance';

const APPEARANCE_OPTIONS = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
] as const;

const STAUNTON_PIECE_SETS = PIECE_SET_OPTIONS.filter((option) => option.staunton);
const OTHER_PIECE_SETS = PIECE_SET_OPTIONS.filter((option) => !option.staunton);
const FLAT_BOARDS = BOARD_THEME_OPTIONS.filter((option) => option.material === 'flat');
const WOOD_BOARDS = BOARD_THEME_OPTIONS.filter((option) => option.material === 'wood');
const STONE_BOARDS = BOARD_THEME_OPTIONS.filter((option) => option.material === 'stone');

export function SettingsScreen() {
  const [appearance, setAppearance] = useState<Appearance>(readAppearance);
  const [saveError, setSaveError] = useState(false);

  const [boardAppearance, setBoardAppearance] = useState(readBoardAppearance);
  const [pieceSaveError, setPieceSaveError] = useState(false);
  const [boardSaveError, setBoardSaveError] = useState(false);
  const [sounds, setSounds] = useState(readBoardSounds);
  const [soundSaveError, setSoundSaveError] = useState(false);
  const [healthPanel, setHealthPanel] = useState(readHealthPanel);
  const [healthSaveError, setHealthSaveError] = useState(false);
  const pieceSetName = useId();
  const boardThemeName = useId();

  useEffect(() => {
    applyBoardAppearance(boardAppearance);
  }, [boardAppearance]);

  function changePieceSet(pieceSet: PieceSet) {
    setBoardAppearance((current) => ({ ...current, pieceSet }));
    setPieceSaveError(!saveBoardChoice('pieceSet', pieceSet));
  }

  function changeBoardTheme(boardTheme: BoardTheme) {
    setBoardAppearance((current) => ({ ...current, boardTheme }));
    setBoardSaveError(!saveBoardChoice('boardTheme', boardTheme));
  }
  useEffect(() => {
    applyAppearance(appearance);
  }, [appearance]);

  function changeAppearance(next: Appearance) {
    setAppearance(next);
    try {
      window.localStorage.setItem(APPEARANCE_STORAGE_KEY, next);
      setSaveError(false);
    } catch {
      setSaveError(true);
    }
  }

  return (
    <section className="settings-screen working-screen" aria-labelledby="settings-title">
      <h1 id="settings-title">Settings</h1>
      <Panel title="Appearance">
        <p>Choose a light or dark appearance, or follow your device setting.</p>
        <SegmentedControl
          label="Colour scheme"
          value={appearance}
          options={APPEARANCE_OPTIONS}
          onChange={changeAppearance}
        />
        {saveError ? (
          <StatusLine kind="error">Appearance changed for this session. Your browser did not allow saving the preference.</StatusLine>
        ) : null}
      </Panel>
      <PuzzleLibraryPanel />
      <Panel title="Chessboard" className="settings-board-appearance">
        <p>Piece graphics and square colours apply to every board, independently of the app colour scheme.</p>
        <fieldset className="ui-segmented settings-piece-sets">
          <legend>Piece set</legend>
          <SegmentedControl label="Staunton pattern" name={pieceSetName} value={boardAppearance.pieceSet} options={STAUNTON_PIECE_SETS} onChange={changePieceSet} />
          <SegmentedControl label="Other designs" name={pieceSetName} value={boardAppearance.pieceSet} options={OTHER_PIECE_SETS} onChange={changePieceSet} />
        </fieldset>
        <p>Staunton-pattern sets follow the piece form that the FIDE equipment standard requires for tournament play (FIDE Handbook C.02: Staunton style, light and dark sets, matte finish). Staunton (cburnett) is the default.</p>
        <p>California, Maestro and Staunty are licensed for non-commercial use only (CC BY-NC-SA 4.0). Other artwork has separate licences; see the artwork notices below.</p>
        {pieceSaveError ? <StatusLine kind="error">Piece set changed for this session. Your browser did not allow saving the preference.</StatusLine> : null}
        <fieldset className="ui-segmented settings-board-themes">
          <legend>Board palette</legend>
          <SegmentedControl label="Flat colours" name={boardThemeName} value={boardAppearance.boardTheme} options={FLAT_BOARDS} onChange={changeBoardTheme} />
          <SegmentedControl label="Wood" name={boardThemeName} value={boardAppearance.boardTheme} options={WOOD_BOARDS} onChange={changeBoardTheme} />
          <SegmentedControl label="Stone" name={boardThemeName} value={boardAppearance.boardTheme} options={STONE_BOARDS} onChange={changeBoardTheme} />
        </fieldset>
        <p>Brown and cream and Classic brown use the square colours named in the FIDE equipment standard for tournament boards (FIDE Handbook C.02: cream or buff light squares, brown dark squares). Green and buff and Classic green follow earlier editions of that standard, which also named green.</p>
        <p>Wood and stone boards use photographed veneer and stone with a matte finish: no gloss or glare. Every square has its own grain or veining, and light and dark squares keep at least the contrast of Brown and cream. Textures are public-domain (CC0) and load when a board first shows.</p>
        {boardSaveError ? <StatusLine kind="error">Board palette changed for this session. Your browser did not allow saving the preference.</StatusLine> : null}
        <label><input type="checkbox" checked={sounds} onChange={event => {
          setSounds(event.target.checked);
          setSoundSaveError(!saveBoardSounds(event.target.checked));
        }} /> Move sounds</label>
        <p>Synthesised move, capture, check and game-end sounds start after your first interaction. No audio is downloaded.</p>
        {soundSaveError ? <StatusLine kind="error">Sounds changed for this session. Your browser did not allow saving the preference.</StatusLine> : null}
        <label><input type="checkbox" checked={healthPanel} onChange={event => {
          setHealthPanel(event.target.checked);
          setHealthSaveError(!saveHealthPanel(event.target.checked));
        }} data-testid="health-panel-setting" /> Position health panel</label>
        <p>Meters for king safety, material, threats, pieces, mobility, pawn structure, passed pawns and space, from Scan64's evaluation factors. No engine search or language model.</p>
        {healthSaveError ? <StatusLine kind="error">Position health changed for this session. Your browser did not allow saving the preference.</StatusLine> : null}
      </Panel>
      <UpdatesPanel />
      <LocalExplanationSettings />
      <ProgressReset />
      <FeedbackPanel />
      <Panel title="About and licences">
        <p>Scan64 is a local-first chess study app. Play, review your games and practise with lessons grounded in your moves.</p>
        <p>Scan64 is free software licensed under the GNU General Public License, version 3 or later (GPL-3.0-or-later). Stockfish, python-chess and Chessground are also licensed under GPL-3.0-or-later.</p>
        <p><a href="/licenses.html">Read software and artwork licences</a></p>
        <p>Host Grotesk and JetBrains Mono are bundled with the app under the SIL Open Font License 1.1. No font service is contacted.</p>
        <p>Chess piece artwork is separately licensed, not covered by Scan64's GPL licence. Staunton (Colin M.L. Burnett) and Merida (Armando Hernandez Marroquin): GPL-2.0-or-later. California (Jerry S.), Maestro and Staunty (sadsnake1): CC BY-NC-SA 4.0, non-commercial only. Chessnut (Alexis Luengas): Apache 2.0. Spatial (Maurizio Monge): MIT. Firi (James Faure): CC BY 4.0. mpchess (Maxime Chupin): GPL-3.0-or-later. Sophia (GNOME Chess, after Max Froumentin): W3C Software Notice and License.</p>
        <p><a href="/pieces/NOTICE.txt">Piece artwork attribution and licence notices</a></p>
        <ul className="settings-licences" aria-label="Bundled font licences">
          <ListRow label={<a href="/fonts/host-grotesk-OFL.txt">Host Grotesk licence</a>} />
          <ListRow label={<a href="/fonts/jetbrains-mono-OFL.txt">JetBrains Mono licence</a>} />
        </ul>
      </Panel>
    </section>
  );
}
