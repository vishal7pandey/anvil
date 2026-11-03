import type {
  NewSessionForm,
  Run,
  Session,
  StreamEvent,
} from '../types';

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api';

function authHeaders(): HeadersInit {
  const token = import.meta.env.VITE_DEMO_TOKEN;
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export async function createSession(form: NewSessionForm): Promise<Session> {
  const res = await fetch(`${API_BASE}/sessions`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(form),
  });
  if (!res.ok) throw new Error(`Failed to create session: ${res.statusText}`);
  return res.json();
}

export async function sendMessage(sessionId: string, text: string): Promise<Run> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`Failed to send message: ${res.statusText}`);
  return res.json();
}

export async function getRun(runId: string): Promise<Run> {
  const res = await fetch(`${API_BASE}/runs/${runId}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to get run: ${res.statusText}`);
  return res.json();
}

export async function decideApproval(
  approvalId: string,
  decision: 'approve' | 'reject',
  comment?: string,
): Promise<void> {
  const res = await fetch(`${API_BASE}/approvals/${approvalId}`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ decision, comment }),
  });
  if (!res.ok) throw new Error(`Failed to decide approval: ${res.statusText}`);
}

export async function cancelRun(runId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/runs/${runId}/cancel`, {
    method: 'POST',
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to cancel run: ${res.statusText}`);
}

const TERMINAL_STATUSES = new Set(['handoff', 'succeeded', 'failed', 'cancelled']);

export interface SseMessage {
  id?: string;
  event?: string;
  data: string;
}

/**
 * Split a text/event-stream buffer into complete messages. Returns the messages and the
 * unfinished tail to keep for the next chunk. Named events (`event: tool.call`) are kept:
 * the API names every event, which `EventSource.onmessage` would silently drop.
 */
export function parseSse(buffer: string): { messages: SseMessage[]; rest: string } {
  const normalized = buffer.replace(/\r\n?/g, '\n');
  const blocks = normalized.split('\n\n');
  const rest = blocks.pop() ?? '';
  const messages: SseMessage[] = [];
  for (const block of blocks) {
    const msg: SseMessage = { data: '' };
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue;
      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
      if (field === 'data') data.push(value);
      else if (field === 'id') msg.id = value;
      else if (field === 'event') msg.event = value;
    }
    if (data.length) {
      msg.data = data.join('\n');
      messages.push(msg);
    }
  }
  return { messages, rest };
}

/**
 * Stream a run's events. Uses fetch (not EventSource) so the Bearer header can be sent, and
 * resumes with Last-Event-ID if the connection drops before the run reaches a final status.
 */
export function subscribeToEvents(
  runId: string,
  onEvent: (event: StreamEvent) => void,
  options: { maxReconnects?: number; reconnectDelayMs?: number } = {},
): () => void {
  const { maxReconnects = 20, reconnectDelayMs = 1000 } = options;
  const controller = new AbortController();
  let lastId: string | undefined;
  let finished = false;

  const connect = async (attempt: number): Promise<void> => {
    try {
      const headers = new Headers(authHeaders());
      headers.set('Accept', 'text/event-stream');
      if (lastId) headers.set('Last-Event-ID', lastId);
      const res = await fetch(`${API_BASE}/runs/${runId}/events`, {
        headers,
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`Event stream failed: ${res.status} ${res.statusText}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { messages, rest } = parseSse(buffer);
        buffer = rest;
        for (const msg of messages) {
          if (msg.id) lastId = msg.id;
          try {
            const event: StreamEvent = JSON.parse(msg.data);
            onEvent(event);
            const status = (event.data as { status?: string } | undefined)?.status;
            if (event.type === 'run.status' && status && TERMINAL_STATUSES.has(status)) finished = true;
          } catch (e) {
            console.error('Failed to parse SSE event:', e);
          }
        }
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      console.error('Event stream error:', err);
    }
    if (finished || controller.signal.aborted || attempt >= maxReconnects) return;
    await new Promise((resolve) => setTimeout(resolve, reconnectDelayMs));
    if (!controller.signal.aborted) await connect(attempt + 1);
  };

  void connect(0);
  return () => controller.abort();
}

export async function listSessions(): Promise<Session[]> {
  const res = await fetch(`${API_BASE}/sessions`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Failed to list sessions: ${res.statusText}`);
  return res.json();
}

export async function pollJiraIntake(): Promise<void> {
  const res = await fetch(`${API_BASE}/intake/jira/poll`, {
    method: 'POST',
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to poll Jira: ${res.statusText}`);
}