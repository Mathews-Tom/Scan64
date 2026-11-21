import { describe, expect, it, vi } from 'vitest';
import { readProgress } from './progress';

interface Progress {
  status: 'running' | 'completed';
  detail?: string;
}
const terminal = (state: Progress) => state.status === 'completed';
const encoder = new TextEncoder();

function progressStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(value) { controller = value; },
    cancel,
  });
  return {
    body,
    cancel,
    response: new Response(body, { headers: { 'Content-Type': 'application/x-ndjson' } }),
    push: (value: string | Uint8Array) => controller.enqueue(typeof value === 'string' ? encoder.encode(value) : value),
    close: () => controller.close(),
    fail: (error: Error) => controller.error(error),
  };
}

describe('readProgress', () => {
  it('delivers records before EOF and reconstructs split UTF-8, JSON and newline boundaries', async () => {
    const stream = progressStream();
    const firstUpdate = Promise.withResolvers<void>();
    const updates = vi.fn((_state: Progress) => firstUpdate.resolve());
    const pending = readProgress<Progress>(stream.response, new AbortController().signal, updates, terminal);
    const first = { status: 'running', detail: 'café ♞ 棋' } as const;
    const bytes = encoder.encode(`${JSON.stringify(first)}\r\n`);
    for (const byte of bytes) stream.push(new Uint8Array([byte]));
    await firstUpdate.promise;
    expect(updates.mock.calls.map(([state]) => state)).toEqual([first]);
    expect(stream.cancel).not.toHaveBeenCalled();
    expect(stream.body.locked).toBe(true);

    stream.push('{"status":"com');
    stream.push('pleted","detail":"ready"}');
    stream.push('\n');
    await expect(pending).resolves.toEqual({ status: 'completed', detail: 'ready' });
    expect(updates.mock.calls.map(([state]) => state)).toEqual([first, { status: 'completed', detail: 'ready' }]);
    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(stream.body.locked).toBe(false);
  });

  it('handles several records in one chunk and stops at the terminal record without waiting for close', async () => {
    const stream = progressStream();
    const updates = vi.fn();
    const pending = readProgress<Progress>(stream.response, new AbortController().signal, updates, terminal);
    stream.push('{"status":"running"}\n{"status":"completed"}\n{"status":"running"}\n');
    await expect(pending).resolves.toEqual({ status: 'completed' });
    expect(updates.mock.calls.map(([state]) => state.status)).toEqual(['running', 'completed']);
    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(stream.body.locked).toBe(false);
  });

  it.each(['\n', '  \n', '{broken}\n', 'null\n', '[]\n', '"completed"\n', '1\n'])('rejects malformed record %j and releases its reader', async record => {
    const stream = progressStream();
    const updates = vi.fn();
    const pending = readProgress<Progress>(stream.response, new AbortController().signal, updates, terminal);
    stream.push(record);
    await expect(pending).rejects.toThrow(/malformed/i);
    expect(updates).not.toHaveBeenCalled();
    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(stream.body.locked).toBe(false);
  });

  it.each([
    ['invalid UTF-8', new Uint8Array([0xc3, 0x28])],
    ['unfinished UTF-8', new Uint8Array([0xe2, 0x99])],
  ] as const)('rejects %s instead of silently substituting characters', async (_name, bytes) => {
    const stream = progressStream();
    const updates = vi.fn();
    const pending = readProgress<Progress>(stream.response, new AbortController().signal, updates, terminal);
    stream.push('{"status":"running","detail":"');
    stream.push(bytes);
    stream.close();
    await expect(pending).rejects.toBeInstanceOf(TypeError);
    expect(updates).not.toHaveBeenCalled();
    expect(stream.body.locked).toBe(false);
  });

  it.each(['{"status":"completed"}', '{"status":'])('rejects an unterminated final record %j', async record => {
    const stream = progressStream();
    const updates = vi.fn();
    const pending = readProgress<Progress>(stream.response, new AbortController().signal, updates, terminal);
    stream.push(record);
    stream.close();
    await expect(pending).rejects.toThrow(/truncated/i);
    expect(updates).not.toHaveBeenCalled();
    expect(stream.body.locked).toBe(false);
  });

  it.each(['', '{"status":"running"}\n'])('rejects EOF without a terminal snapshot for %j', async records => {
    const stream = progressStream();
    const updates = vi.fn();
    const pending = readProgress<Progress>(stream.response, new AbortController().signal, updates, terminal);
    if (records) stream.push(records);
    stream.close();
    await expect(pending).rejects.toThrow(/disconnected before completion/i);
    expect(updates).toHaveBeenCalledTimes(records ? 1 : 0);
    expect(stream.body.locked).toBe(false);
  });

  it('preserves network stream failures and releases the reader', async () => {
    const stream = progressStream();
    const failure = new Error('Connection reset');
    const pending = readProgress<Progress>(stream.response, new AbortController().signal, vi.fn(), terminal);
    stream.fail(failure);
    await expect(pending).rejects.toBe(failure);
    expect(stream.body.locked).toBe(false);
  });

  it('reports HTTP failures before consuming a response body', async () => {
    const stream = progressStream();
    const response = new Response(stream.body, { status: 403, statusText: 'Forbidden' });
    const updates = vi.fn();
    await expect(readProgress<Progress>(response, new AbortController().signal, updates, terminal)).rejects.toThrow(/403 Forbidden/);
    expect(updates).not.toHaveBeenCalled();
    expect(stream.body.locked).toBe(false);
    expect(stream.cancel).not.toHaveBeenCalled();
  });

  it('reports a successful response with no stream', async () => {
    await expect(readProgress<Progress>(new Response(null), new AbortController().signal, vi.fn(), terminal)).rejects.toThrow(/no stream/i);
  });

  it('aborts a pending read, cancels its source and never delivers another update', async () => {
    const stream = progressStream();
    const controller = new AbortController();
    const received = Promise.withResolvers<void>();
    const updates = vi.fn((_state: Progress) => received.resolve());
    const pending = readProgress<Progress>(stream.response, controller.signal, updates, terminal);
    stream.push('{"status":"running"}\n');
    await received.promise;
    const aborted = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    controller.abort();
    await aborted;
    expect(stream.cancel).toHaveBeenCalledExactlyOnceWith(controller.signal.reason);
    expect(updates).toHaveBeenCalledTimes(1);
    expect(stream.body.locked).toBe(false);
  });

  it('does not acquire a reader when the signal is already aborted', async () => {
    const stream = progressStream();
    const controller = new AbortController();
    controller.abort();
    const updates = vi.fn();
    await expect(readProgress<Progress>(stream.response, controller.signal, updates, terminal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(updates).not.toHaveBeenCalled();
    expect(stream.body.locked).toBe(false);
    expect(stream.cancel).not.toHaveBeenCalled();
  });

  it.each(['update', 'terminal'] as const)('cleans up when the %s callback throws', async callback => {
    const stream = progressStream();
    const failure = new Error('Rejected progress state');
    const updates = vi.fn(() => { if (callback === 'update') throw failure; });
    const pending = readProgress<Progress>(stream.response, new AbortController().signal, updates, () => { throw failure; });
    stream.push('{"status":"running"}\n');
    await expect(pending).rejects.toBe(failure);
    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(stream.body.locked).toBe(false);
  });
});
