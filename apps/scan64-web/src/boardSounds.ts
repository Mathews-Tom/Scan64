import type { Move } from 'chess.js';

export type BoardSound = 'move' | 'capture' | 'check' | 'end';
const STORAGE_KEY = 'scan64-board-sounds';
let sessionPreference: boolean | undefined;
let context: AudioContext | undefined;
let initialized = false;

export function readBoardSounds(): boolean {
  if (sessionPreference !== undefined) return sessionPreference;
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'false') return false;
    if (saved === 'true') return true;
  } catch { /* Restricted storage still permits a session preference. */ }
  return true;
}
export function saveBoardSounds(enabled: boolean): boolean {
  sessionPreference = enabled;
  if (!enabled) void context?.suspend().catch(() => undefined);
  try {
    localStorage.setItem(STORAGE_KEY, String(enabled));
    return true;
  } catch { return false; }
}

/** Browsers require a real gesture before sound; never create a context at startup. */
export function initializeBoardSounds(): void {
  if (initialized) return;
  initialized = true;
  const unlock = () => {
    if (!readBoardSounds() || !window.AudioContext) return;
    try {
      context ??= new AudioContext();
      if (context.state === 'suspended') void context.resume().catch(() => undefined);
    } catch { /* Sound is optional when Web Audio is unavailable. */ }
  };
  document.addEventListener('pointerdown', unlock, { capture: true, passive: true });
  document.addEventListener('keydown', unlock, { capture: true });
}

const tones: Record<BoardSound, readonly number[]> = {
  move: [440], capture: [220, 165], check: [740, 988], end: [523, 659, 784],
};

export function playBoardSound(sound: BoardSound, delay = 0): void {
  if (!readBoardSounds() || !context || context.state !== 'running') return;
  const audio = context;
  tones[sound].forEach((frequency, index) => {
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    const start = audio.currentTime + delay + index * 0.075;
    oscillator.type = sound === 'capture' ? 'triangle' : 'sine';
    oscillator.frequency.setValueAtTime(frequency, start);
    gain.gain.setValueAtTime(0, start);
    gain.gain.linearRampToValueAtTime(0.12, start + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.001, start + 0.09);
    oscillator.connect(gain);
    gain.connect(audio.destination);
    oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
    oscillator.start(start);
    oscillator.stop(start + 0.1);
  });
}

export function playMoveSound(move: Move, delay = 0): void {
  playBoardSound(/[+#]$/.test(move.san) ? 'check' : move.isCapture() || move.isEnPassant() ? 'capture' : 'move', delay);
}
