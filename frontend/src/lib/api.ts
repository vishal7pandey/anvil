import type {
  ApprovalDecision,
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

export function subscribeToEvents(
  runId: string,
  onEvent: (event: StreamEvent) => void,
): () => void {
  const token = import.meta.env.VITE_DEMO_TOKEN;
  const url = token
    ? `${API_BASE}/runs/${runId}/events?token=${encodeURIComponent(token)}`
    : `${API_BASE}/runs/${runId}/events`;
  const es = new EventSource(url);

  es.onmessage = (evt) => {
    try {
      const event: StreamEvent = JSON.parse(evt.data);
      onEvent(event);
    } catch (e) {
      console.error('Failed to parse SSE event:', e);
    }
  };

  es.onerror = (err) => {
    console.error('EventSource error:', err);
    es.close();
  };

  return () => es.close();
}

export async function pollJiraIntake(): Promise<void> {
  const res = await fetch(`${API_BASE}/intake/jira/poll`, {
    method: 'POST',
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to poll Jira: ${res.statusText}`);
}