import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import App from '../App';

beforeEach(() => {
  vi.spyOn(globalThis, 'fetch');
  if (!(Element.prototype as any).scrollIntoView) {
    (Element.prototype as any).scrollIntoView = vi.fn();
  }
  vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const session = {
  id: '6f0c1a52-0000-4000-8000-000000000001',
  repo: 'vishal7pandey/rtems',
  environment: 'local',
  mode: 'autonomous',
  merge_policy: 'human',
  model_profile: 'anvil-main',
  title: 'RTEMS-1 mutex defect',
  created_at: '2026-11-03T08:00:00Z',
  updated_at: '2026-11-03T08:00:00Z',
};

function mockSessions(list: unknown[]) {
  vi.mocked(globalThis.fetch).mockResolvedValueOnce({
    ok: true,
    json: async () => list,
  } as Response);
  vi.mocked(globalThis.fetch).mockResolvedValueOnce({
    ok: true,
    json: async () => [],
  } as Response);
  // ANVIL-73: the first session's mount-attach loads its runs — an empty list is the no-run stub
  vi.mocked(globalThis.fetch).mockResolvedValueOnce({
    ok: true,
    json: async () => [],
  } as Response);
}

describe('App', () => {
  it('shows sessions loaded from the API, never a made-up one', async () => {
    mockSessions([session]);
    render(<App />);
    expect(screen.getByText('Anvil')).toBeInTheDocument();
    expect(await screen.findByText('RTEMS-1 mutex defect')).toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/sessions', expect.anything());
    expect(screen.queryByText('RTEMS pthread_mutex_init defect fix')).not.toBeInTheDocument();
  });

  it('with no sessions, offers New Session and the dialog opens', async () => {
    mockSessions([]);
    render(<App />);
    expect(await screen.findByText('No sessions yet.')).toBeInTheDocument();
    fireEvent.click(screen.getAllByText('New Session')[0]);
    expect(screen.getByRole('heading', { name: 'New Session' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('e.g. Fix pthread_mutex_init defect')).toBeInTheDocument();
  });

  it('shows the error when sessions cannot be loaded', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: false,
      statusText: 'Unauthorized',
    } as Response);
    render(<App />);
    expect(await screen.findByText('Could not load sessions: Failed to list sessions: Unauthorized')).toBeInTheDocument();
  });

  it('keeps approvals in the inbox while switching sessions', async () => {
    const other = { ...session, id: '6f0c1a52-0000-4000-8000-000000000002', title: 'RTEMS-2 lwIP upgrade' };
    const approval = JSON.stringify({
      run_id: 'r1', seq: 3, ts: '2026-11-03T08:00:00Z', type: 'approval.request', agent: 'main',
      data: { approval_id: 'a-1', action: 'git_push', args: { branch: 'anvil/rtems-1' } },
    });
    const encoder = new TextEncoder();
    let sent = false;
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => [session, other] } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => [] } as Response)
      // ANVIL-73: selecting the first session loads its runs — none here
      .mockResolvedValueOnce({ ok: true, json: async () => [] } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: 'r1', status: 'running' }) } as Response)
      .mockImplementationOnce(async (_url, init) => ({
        ok: true,
        body: {
          getReader: () => ({
            read: async () => {
              if (!sent) {
                sent = true;
                return { done: false, value: encoder.encode(`id: 3\nevent: approval.request\ndata: ${approval}\n\n`) };
              }
              // Stay open until the UI aborts the stream.
              return new Promise((_resolve, reject) =>
                init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))),
              );
            },
          }),
        },
      }) as unknown as Response);

    render(<App />);
    await screen.findAllByText('RTEMS-1 mutex defect');
    fireEvent.change(screen.getByPlaceholderText('Ask the agent to build, fix, or upgrade…'), {
      target: { value: 'fix RTEMS-1' },
    });
    fireEvent.submit(screen.getByPlaceholderText('Ask the agent to build, fix, or upgrade…').closest('form')!);

    expect(await screen.findByText('a-1')).toBeInTheDocument();
    expect(screen.getByText('No reason given')).toBeInTheDocument();
    expect(screen.getAllByText('main').length).toBeGreaterThan(0);

    fireEvent.click(screen.getAllByText('RTEMS-2 lwIP upgrade')[0]);
    expect(screen.queryByText('a-1')).not.toBeInTheDocument();
    const streamCall = fetchMock.mock.calls.find(
      ([url]) => String(url).includes('/events'),
    );
    expect(streamCall?.[1]?.signal?.aborted).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /Approvals/ }));
    expect(await screen.findByText('a-1')).toBeInTheDocument();
  });

  it('navigates to the approvals inbox without requiring a selected session', async () => {
    mockSessions([]);
    render(<App />);
    expect(await screen.findAllByText('No sessions yet.')).toHaveLength(1);
    fireEvent.click(screen.getAllByRole('button', { name: /Approvals/ })[0]);
    expect(await screen.findByRole('heading', { name: 'Approvals inbox' })).toBeInTheDocument();
    expect(screen.getByText('No pending approvals.')).toBeInTheDocument();
  });

  it('polls approvals periodically so a new server approval appears without reloading', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const fetchMock = vi.mocked(globalThis.fetch);
      fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] } as Response);
      fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] } as Response);
      const newApproval = {
        approval_id: 'appr-polled-99',
        run_id: 'r-99',
        action: 'jira_comment',
        args: { markdown: 'Proposed fix ready' },
        reason: 'Requires human approval',
        agent: 'anvil-agent',
        session_id: 's-99',
        session_title: 'Demo Session',
        repo: 'vishal7pandey/rtems',
        run_status: 'waiting_approval',
        requested_at: '2026-11-05T04:00:00Z',
      };
      fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [newApproval] } as Response);

      const { unmount } = render(<App />);

      fireEvent.click(screen.getByRole('button', { name: /Approvals/ }));
      expect(await screen.findByText('No pending approvals.')).toBeInTheDocument();

      await vi.advanceTimersByTimeAsync(3000);

      expect(await screen.findByText('appr-polled-99')).toBeInTheDocument();
      expect(screen.getByText('jira_comment {"markdown":"Proposed fix ready"}')).toBeInTheDocument();

      unmount();
      const callsBefore = fetchMock.mock.calls.length;
      await vi.advanceTimersByTimeAsync(6000);
      expect(fetchMock.mock.calls.length).toBe(callsBefore);
    } finally {
      vi.useRealTimers();
    }
  });

  it('New Session dialog shows repo hint and field descriptions', async () => {
    mockSessions([]);
    render(<App />);
    fireEvent.click(screen.getAllByText('New Session')[0]);
    expect(await screen.findByRole('heading', { name: 'New Session' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/vishal7pandey\/rtems or C:\\work\\rtems/)).toBeInTheDocument();
    expect(screen.getByText('GitHub owner/repository or an absolute local path')).toBeInTheDocument();
    expect(screen.getByText('Every tool waits for your approval (TC1 default)')).toBeInTheDocument();
    expect(screen.getByText('Balanced model for general tasks (TC1 default)')).toBeInTheDocument();
  });

  it('creating a session submits the form and closes the dialog', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => [] } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => [] } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ...session, id: 's-new' }),
      } as Response);

    render(<App />);
    await screen.findByText('No sessions yet.');
    fireEvent.click(screen.getAllByText('New Session')[0]);
    await screen.findByRole('heading', { name: 'New Session' });

    fireEvent.change(screen.getByPlaceholderText(/vishal7pandey\/rtems or C:\\work\\rtems/), {
      target: { value: 'vishal7pandey/rtems' },
    });
    fireEvent.submit(screen.getByRole('button', { name: /Create/ }).closest('form')!);

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"repo":"vishal7pandey/rtems"'),
      }),
    );
    // Dialog closes and the new session appears in the list
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'New Session' })).not.toBeInTheDocument();
    });
    expect(await screen.findByText('RTEMS-1 mutex defect')).toBeInTheDocument();
  });

  it('shows the API 422 error inline when session creation fails', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => [] } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => [] } as Response)
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        statusText: 'Repository must be a GitHub owner/repository',
        json: async () => ({ detail: 'Repository must be a GitHub owner/repository' }),
      } as Response);

    render(<App />);
    await screen.findByText('No sessions yet.');
    fireEvent.click(screen.getAllByText('New Session')[0]);
    await screen.findByRole('heading', { name: 'New Session' });

    fireEvent.change(screen.getByPlaceholderText(/vishal7pandey\/rtems or C:\\work\\rtems/), {
      target: { value: 'not-a-valid-repo' },
    });
    fireEvent.submit(screen.getByRole('button', { name: /Create/ }).closest('form')!);

    expect(await screen.findByText('Failed to create session: Repository must be a GitHub owner/repository')).toBeInTheDocument();
    // Dialog stays open so the user can fix the input
    expect(screen.getByRole('heading', { name: 'New Session' })).toBeInTheDocument();
  });
});

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    ok: true, status: 200, statusText: 'OK',
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
  return `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify({ run_id: 'r1', seq, ts: '2026-11-03T08:00:00Z', type, agent: 'main', data })}\n\n`;
}

const timelineEvent = (seq: number, role: string, text: string) => ({
  run_id: 'r1', seq, ts: '2026-11-03T08:00:00Z', type: 'message', agent: 'main',
  data: { role, text },
});

const session2 = { ...session, id: '6f0c1a52-0000-4000-8000-000000000002', title: 'RTEMS-2 lwIP upgrade' };

describe('session run attach (ANVIL-73)', () => {
  const jsonRes = (body: unknown) => ({ ok: true, json: async () => body } as Response);

  it('selecting a session whose latest run finished shows its You message', async () => {
    mockSessions([session, session2]);
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(jsonRes([
      { id: 'r1', status: 'succeeded', trigger: 'ui' },
    ]));
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(jsonRes([
      timelineEvent(3, 'user', 'fix the mutex'),
      timelineEvent(4, 'assistant', 'done'),
    ]));
    render(<App />);
    fireEvent.click(await screen.findByText('RTEMS-2 lwIP upgrade'));
    expect(await screen.findByText('fix the mutex')).toBeInTheDocument();
    expect(screen.getByText('You')).toBeInTheDocument();
  });

  it('an active run reattaches at the last seq and shows no duplicate', async () => {
    mockSessions([session, session2]);
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(jsonRes([
      { id: 'r1', status: 'running', trigger: 'ui' },
    ]));
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(jsonRes([
      timelineEvent(5, 'user', 'keep going'),
    ]));
    // the stream resends seq 5 — dedupe by seq must keep only one copy on screen
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      streamResponse([sse(5, 'message', { role: 'user', text: 'keep going' })]),
    );
    render(<App />);
    fireEvent.click(await screen.findByText('RTEMS-2 lwIP upgrade'));
    await waitFor(() => expect(screen.getAllByText('keep going')).toHaveLength(1));
    const streamCall = vi.mocked(globalThis.fetch).mock.calls.find(
      ([url]) => String(url).endsWith('/runs/r1/events'),
    );
    expect(streamCall).toBeTruthy();
    const headers = new Headers(streamCall![1]?.headers as HeadersInit);
    expect(headers.get('Last-Event-ID')).toBe('5');
  });
});

describe('attach race (review on #79)', () => {
  const jsonRes = (body: unknown) => ({ ok: true, json: async () => body } as Response);

  it("a slow attach for session A cannot overwrite session B's chat", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    let resolveA: (v: unknown) => void = () => {};
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => [session, session2] } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => [] } as Response)
      // A's mount-attach stays in flight: its listRunsForSession resolves late
      .mockImplementationOnce(
        () => new Promise((resolve) => { resolveA = resolve; }) as Promise<Response>,
      )
      .mockResolvedValueOnce(jsonRes([{ id: 'rB', status: 'succeeded', trigger: 'ui' }]))
      .mockResolvedValueOnce(jsonRes([
        { run_id: 'rB', seq: 1, ts: 'x', type: 'message', agent: 'main', data: { role: 'user', text: 'B won' } },
      ]));
    render(<App />);
    await screen.findByText('RTEMS-1 mutex defect');
    fireEvent.click(screen.getByText('RTEMS-2 lwIP upgrade'));
    expect(await screen.findByText('B won')).toBeInTheDocument();
    // A's runs arrive only now — they must be discarded, not overwrite B's chat
    resolveA(jsonRes([{ id: 'rA', status: 'succeeded', trigger: 'ui' }]));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText('B won')).toBeInTheDocument();
  });
});
