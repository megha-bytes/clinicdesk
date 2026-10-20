/** Read a server-sent event stream from a fetch() response (works for POST, unlike EventSource). */
export type SseHandler = (event: Record<string, unknown>) => void;

export function parseSseChunk(buffer: string): { events: Record<string, unknown>[]; rest: string } {
  const events: Record<string, unknown>[] = [];
  const blocks = buffer.split(/\r?\n\r?\n/);
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    const data = block
      .split(/\r?\n/)
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data) continue;
    try {
      events.push(JSON.parse(data));
    } catch {
      /* ignore a malformed block */
    }
  }
  return { events, rest };
}

export async function postStream(url: string, body: unknown, onEvent: SseHandler, signal?: AbortSignal,
                                 headers: Record<string, string> = {}, onResponse?: (res: Response) => void): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream", ...headers },
    body: JSON.stringify(body),
    signal,
  });
  if (res.ok) onResponse?.(res);
  if (!res.ok || !res.body) {
    let detail = "";
    try {
      detail = (await res.json()).detail ?? "";
    } catch {
      /* not JSON */
    }
    throw new StreamError(res.status, detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSseChunk(buffer);
    buffer = rest;
    events.forEach(onEvent);
  }
  const { events } = parseSseChunk(buffer + "\n\n");
  events.forEach(onEvent);
}

export class StreamError extends Error {
  constructor(public status: number, public detail: string) {
    super(`HTTP ${status}${detail ? `: ${detail}` : ""}`);
  }
}
