import { afterEach, beforeEach, expect, it, vi, type Mock } from 'vitest';
import { Chess } from 'chess.js';

// Dynamic imports intentionally recreate the module's cold-start preference/audio boundary.
beforeEach(() => { localStorage.clear(); vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('defaults on and reloads a persisted mute without constructing audio', async () => {
  const sounds = await import('./boardSounds');
  expect(sounds.readBoardSounds()).toBe(true);
  expect(sounds.saveBoardSounds(false)).toBe(true);
  expect(localStorage.getItem('scan64-board-sounds')).toBe('false');
  vi.resetModules();
  expect((await import('./boardSounds')).readBoardSounds()).toBe(false);
});

it('synthesizes distinct move, capture, check and end envelopes only after a gesture and respects mute', async () => {
  const frequencies: number[] = [];
  const nodes: { start: Mock; stop: Mock }[] = [];
  const audio = {
    state: 'running', currentTime: 0, destination: {}, resume: vi.fn().mockResolvedValue(undefined), suspend: vi.fn().mockResolvedValue(undefined),
    createOscillator: vi.fn(() => {
      const node = { type: '', frequency: { setValueAtTime: vi.fn((value: number) => frequencies.push(value)) },
        connect: vi.fn(), disconnect: vi.fn(), onended: null, start: vi.fn(), stop: vi.fn() };
      nodes.push(node); return node;
    }),
    createGain: vi.fn(() => ({ gain: { setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() }, connect: vi.fn(), disconnect: vi.fn() })),
  };
  const constructor = vi.fn(function () { return audio; });
  vi.stubGlobal('AudioContext', constructor);
  const sounds = await import('./boardSounds');
  sounds.initializeBoardSounds();
  sounds.playBoardSound('move');
  expect(constructor).not.toHaveBeenCalled();
  document.dispatchEvent(new Event('pointerdown'));
  expect(constructor).toHaveBeenCalledOnce();
  sounds.playMoveSound(new Chess().move('e4'));
  const capture = new Chess(); capture.move('e4'); capture.move('d5');
  sounds.playMoveSound(capture.move('exd5'));
  const check = new Chess('4k3/8/8/8/8/8/8/R3K3 w Q - 0 1');
  sounds.playMoveSound(check.move('Ra8+'));
  sounds.playBoardSound('end');
  expect(frequencies).toEqual([440, 220, 165, 740, 988, 523, 659, 784]);
  expect(nodes.every(node => node.start.mock.calls.length === 1 && node.stop.mock.calls.length === 1)).toBe(true);
  sounds.saveBoardSounds(false);
  expect(audio.suspend).toHaveBeenCalledOnce();
  sounds.playBoardSound('move');
  expect(frequencies).toHaveLength(8);
});

it('retains the in-session preference if storage is blocked', async () => {
  const sounds = await import('./boardSounds');
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage blocked'); });
  expect(sounds.saveBoardSounds(false)).toBe(false);
  expect(sounds.readBoardSounds()).toBe(false);
});
