export async function readProgress<T>(
  response: Response,
  signal: AbortSignal,
  onUpdate: (state: T) => void,
  isTerminal: (state: T) => boolean,
): Promise<T> {
  signal.throwIfAborted();
  if (!response.ok) throw new Error(`Progress request failed (${response.status} ${response.statusText}).`);
  if (!response.body) throw new Error('Progress response has no stream.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let pending = '';
  const abort = () => {
    void reader.cancel(signal.reason).catch(error => console.error('Could not cancel progress stream.', error));
  };
  signal.addEventListener('abort', abort, { once: true });
  try {
    while (true) {
      signal.throwIfAborted();
      const { value, done } = await reader.read();
      signal.throwIfAborted();
      pending += decoder.decode(value, { stream: !done });
      let newline: number;
      while ((newline = pending.indexOf('\n')) !== -1) {
        const line = pending.slice(0, newline);
        pending = pending.slice(newline + 1);
        if (!line.trim()) throw new Error('Malformed progress record.');
        let state: T;
        try {
          const parsed: unknown = JSON.parse(line);
          if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Expected an object.');
          state = parsed as T;
        } catch (error) {
          throw new Error('Malformed progress record.', { cause: error });
        }
        onUpdate(state);
        signal.throwIfAborted();
        if (isTerminal(state)) return state;
      }
      if (done) {
        if (pending.length !== 0) throw new Error('Truncated progress record.');
        throw new Error('Progress stream disconnected before completion.');
      }
    }
  } finally {
    signal.removeEventListener('abort', abort);
    try {
      await reader.cancel();
    } finally {
      reader.releaseLock();
    }
  }
}
