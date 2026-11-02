import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  createSession,
  sendMessage,
  decideApproval,
  cancelRun,
  subscribeToEvents,
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
    (globalThis.fetch as ReturnType<typeof vi.spyOn>).mockResolvedValueOnce({
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
    (globalThis.fetch as ReturnType<typeof vi.spyOn>).mockResolvedValueOnce({
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
    (globalThis.fetch as ReturnType<typeof vi.spyOn>).mockResolvedValueOnce({
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
    (globalThis.fetch as ReturnType<typeof vi.spyOn>).mockResolvedValueOnce({
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
    (globalThis.fetch as ReturnType<typeof vi.spyOn>).mockResolvedValueOnce({
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
    (globalThis.fetch as ReturnType<typeof vi.spyOn>).mockResolvedValueOnce({
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
});

describe('subscribeToEvents', () => {
  it('creates EventSource with correct URL and returns unsubscribe that closes it', () => {
    const onEvent = vi.fn();
    let closeCalled = false;
    let createdUrl: string | null = null;

    (globalThis as any).EventSource = class {
      onmessage: ((e: MessageEvent) => void) | null = null;
      onerror: ((e: Event) => void) | null = null;
      constructor(url: string) {
        createdUrl = url;
      }
      close() {
        closeCalled = true;
      }
    };

    const unsubscribe = subscribeToEvents('r1', onEvent);

    expect(createdUrl).toBe(`${baseUrl}/runs/r1/events?token=${encodeURIComponent(demoToken)}`);

    unsubscribe();
    expect(closeCalled).toBe(true);
  });
});