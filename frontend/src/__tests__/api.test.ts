import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  createSession,
  sendMessage,
  decideApproval,
  cancelRun,
  getMetrics,
  getRunTimeline,
  listApprovals,
  listRuns,
  listRunsForSession,
  subscribeToEvents,
  parseSse,
  listSessions,
} from '../lib/api';
import type { NewSessionForm, Session, Run } from '../types';

const baseUrl = '/api';
const demoToken = 'sk-demo-test-token';

beforeEach(() => {
  vi.spyOn(globalThis, 'fetch');
  vi.stubEnv('VITE_DEMO_TOKEN', demoToken);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe('createSession', () => {
  it('posts to /sessions with bearer auth and returns a Session', async () => {
    const mockSession: Session = {
      id: 's1',
      repo: 'vishal7pandey/rtems',
      environment: 'local',
      mode: 'autonomous',
      merge_policy: 'human',
      model_profile: 'anvil-main',
      title: 'Test session',
      created_at: '2026-11-02T20:00:00Z',
      updated_at: '2026-11-02T20:00:00Z',
    };
    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSession,
    } as Response);

    const form: NewSessionForm = {
      repo: 'vishal7pandey/rtems',
      environment: 'local',
      mode: 'autonomous',
      model_profile: 'anvil-main',
      merge_policy: 'human',
      title: 'Test session',
    };
    const result = await createSession(form);

    expect(globalThis.fetch).toHaveBeenCalledWith(`${baseUrl}/sessions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${demoToken}`,
      },
      body: JSON.stringify(form),
    });
    expect(result).toEqual(mockSession);
  });

  it('throws when response is not ok', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: false,
      statusText: 'Internal Server Error',
    } as Response);

    const form: NewSessionForm = {
      repo: 'vishal7pandey/rtems',
      environment: 'local',
      mode: 'autonomous',
      model_profile: 'anvil-main',
    };
    await expect(createSession(form)).rejects.toThrow('Failed to create session');
  });
});

describe('sendMessage', () => {
  it('posts to /sessions/{id}/messages and returns a Run', async () => {
    const mockRun: Run = {
      id: 'r1',
      session_id: 's1',
      trigger: 'ui',
      repo: 'vishal7pandey/rtems',
      status: 'queued',
      mode: 'autonomous',
      network: 'local',
      base_ref: 'main',
      jira_key: null,
      base_sha: 'abc123',
      branch: 'cline/anvil-12-web-ui',
      sandbox_image_digest: null,
      model_aliases: {},
      skill_versions: {},
      prompt_hash: null,
      budgets: {},
      started_at: '2026-11-02T20:00:00Z',
      ended_at: null,
      usd: 0,
      input_tokens: 0,
      output_tokens: 0,
      human_touches: 0,
      artifacts: [],
      run_record: {},
      cancel_requested_at: null,
      error: null,
      created_at: '2026-11-02T20:00:00Z',
    };
    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => mockRun,
    } as Response);

    const result = await sendMessage('s1', 'Fix the mutex defect');

    expect(globalThis.fetch).toHaveBeenCalledWith(`${baseUrl}/sessions/s1/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${demoToken}`,
      },
      body: JSON.stringify({ text: 'Fix the mutex defect' }),
    });
    expect(result).toEqual(mockRun);
  });
});

describe('decideApproval', () => {
  it('posts approve decision with bearer auth', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: true,
    } as Response);

    await decideApproval('a1', 'approve', 'looks good');

    expect(globalThis.fetch).toHaveBeenCalledWith(`${baseUrl}/approvals/a1`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${demoToken}`,
      },
      body: JSON.stringify({ decision: 'approve', comment: 'looks good' }),
    });
  });

  it('posts reject decision with bearer auth', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: true,
    } as Response);

    await decideApproval('a1', 'reject');

    expect(globalThis.fetch).toHaveBeenCalledWith(`${baseUrl}/approvals/a1`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${demoToken}`,
      },
      body: JSON.stringify({ decision: 'reject', comment: undefined }),
    });
  });
});

describe('cancelRun', () => {
  it('posts to /runs/{id}/cancel with bearer auth', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: true,
    } as Response);

    await cancelRun('r1');

    expect(globalThis.fetch).toHaveBeenCalledWith(`${baseUrl}/runs/r1/cancel`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${demoToken}`,
      },
    });

  });

  describe('ANVIL-26 read APIs', () => {
      it('lists pending approvals with bearer auth', async () => {
        const approvals = [{ approval_id: 'a1', action: 'git_push' }];
        vi.mocked(globalThis.fetch).mockResolvedValueOnce({
          ok: true,
          json: async () => approvals,
        } as Response);

        await expect(listApprovals()).resolves.toEqual(approvals);
        expect(globalThis.fetch).toHaveBeenCalledWith(`${baseUrl}/approvals`, {
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${demoToken}`,
          },
        });
      });

      it('lists runs and loads a selected run timeline', async () => {
        const runs = [{ id: 'r1' }];
        const timeline = [{ run_id: 'r1', seq: 1 }];
        vi.mocked(globalThis.fetch)
          .mockResolvedValueOnce({ ok: true, json: async () => runs } as Response)
          .mockResolvedValueOnce({ ok: true, json: async () => timeline } as Response);

        await expect(listRuns()).resolves.toEqual(runs);
        await expect(getRunTimeline('r1')).resolves.toEqual(timeline);
        expect(globalThis.fetch).toHaveBeenNthCalledWith(1, `${baseUrl}/runs`, expect.anything());
        expect(globalThis.fetch).toHaveBeenNthCalledWith(2, `${baseUrl}/runs/r1/timeline`, expect.anything());
      });

      it('filters aggregate metrics by scenario', async () => {
        const metrics = { run_count: 2 };
        vi.mocked(globalThis.fetch).mockResolvedValueOnce({
          ok: true,
          json: async () => metrics,
        } as Response);

        await expect(getMetrics(' TC1 ')).resolves.toEqual(metrics);
        expect(globalThis.fetch).toHaveBeenCalledWith(`${baseUrl}/metrics?scenario=TC1`, expect.anything());
      });
    });
});

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { done: false, value: encoder.encode(chunks[i++]) }
            : { done: true, value: undefined },
      }),
    },
  } as unknown as Response;
}

function sse(seq: number, type: string, data: Record<string, unknown>): string {
  const payload = JSON.stringify({ run_id: 'r1', seq, ts: '2026-11-03T08:00:00Z', type, agent: 'main', data });
  return `id: ${seq}\nevent: ${type}\ndata: ${payload}\n\n`;
}

describe('parseSse', () => {
  it('keeps named events and returns the unfinished tail', () => {
    const { messages, rest } = parseSse('id: 1\nevent: tool.call\ndata: {"a":1}\n\nid: 2\ndata: {"b"');
    expect(messages).toEqual([{ id: '1', event: 'tool.call', data: '{"a":1}' }]);
    expect(rest).toBe('id: 2\ndata: {"b"');
  });

  it('handles CRLF, comments and multi-line data', () => {
    const { messages } = parseSse(': ping\r\n\r\ndata: x\r\ndata: y\r\n\r\n');
    expect(messages).toEqual([{ data: 'x\ny' }]);
  });
});

describe('subscribeToEvents', () => {
  it('streams with a Bearer header and delivers named events split across chunks', async () => {
    const first = sse(1, 'message', { text: 'hi' });
    const second = sse(2, 'run.status', { status: 'succeeded' });
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      streamResponse([first.slice(0, 20), first.slice(20) + second]),
    );
    const onEvent = vi.fn();
    subscribeToEvents('r1', onEvent, { reconnectDelayMs: 1 });
    await vi.waitFor(() => expect(onEvent).toHaveBeenCalledTimes(2));

    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0];
    expect(url).toBe(`${baseUrl}/runs/r1/events`);
    expect(new Headers(init?.headers).get('Authorization')).toBe(`Bearer ${demoToken}`);
    expect(onEvent.mock.calls.map((c) => c[0].type)).toEqual(['message', 'run.status']);
    // A final status ends the subscription: no reconnect.
    await new Promise((r) => setTimeout(r, 20));
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it('resumes with Last-Event-ID when the stream drops before a final status', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock
      .mockResolvedValueOnce(streamResponse([sse(7, 'tool.call', { name: 'execute' })]))
      .mockResolvedValueOnce(streamResponse([sse(8, 'run.status', { status: 'failed' })]));
    const onEvent = vi.fn();
    subscribeToEvents('r1', onEvent, { reconnectDelayMs: 1 });
    await vi.waitFor(() => expect(onEvent).toHaveBeenCalledTimes(2));
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('Last-Event-ID')).toBe('7');
  });

  it('stops when unsubscribed', async () => {
    let signal: AbortSignal | undefined;
    vi.mocked(globalThis.fetch).mockImplementationOnce(
      (_url, init) => {
        signal = init?.signal ?? undefined;
        return new Promise<Response>(() => {});
      },
    );
    const unsubscribe = subscribeToEvents('r1', vi.fn());
    unsubscribe();
    expect(signal?.aborted).toBe(true);
  });
});

describe('listSessions', () => {
  it('gets /sessions with bearer auth', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    } as Response);
    await expect(listSessions()).resolves.toEqual([]);
    expect(globalThis.fetch).toHaveBeenCalledWith(`${baseUrl}/sessions`, {
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${demoToken}` },
    });
  });
});

describe('listRunsForSession', () => {
  it('gets /sessions/{id}/runs with bearer auth', async () => {
    const mockRuns: Run[] = [
      {
        id: 'r1',
        session_id: 's1',
        trigger: 'ui',
        repo: 'vishal7pandey/rtems',
        status: 'succeeded',
        mode: 'autonomous',
        network: 'offline',
        base_ref: 'main',
        jira_key: null,
        base_sha: null,
        branch: null,
        sandbox_image_digest: null,
        model_aliases: {},
        skill_versions: {},
        prompt_hash: null,
        budgets: {},
        started_at: null,
        ended_at: null,
        usd: 0,
        input_tokens: 0,
        output_tokens: 0,
        human_touches: 0,
        artifacts: [],
        run_record: {},
        cancel_requested_at: null,
        error: null,
        created_at: '2026-11-04T08:00:00Z',
      },
    ];
    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => mockRuns,
    } as Response);
    await expect(listRunsForSession('s1')).resolves.toEqual(mockRuns);
    expect(globalThis.fetch).toHaveBeenCalledWith(`${baseUrl}/sessions/s1/runs`, {
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${demoToken}` },
    });
  });

  it('throws when response is not ok', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: false,
      statusText: 'Not Found',
    } as Response);
    await expect(listRunsForSession('s1')).rejects.toThrow('Failed to list runs');
  });
});
