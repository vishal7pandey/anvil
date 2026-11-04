import { useState, useEffect, useCallback, useRef } from 'react';
import type {
  ApprovalInboxItem,
  ApprovalRequest,
  StreamEvent,
  Session,
  Run,
  NewSessionForm,
} from './types';
import {
  createSession,
  listSessions,
  listApprovals,
  listRunsForSession,
  getRun,
  getRunTimeline,
  sendMessage,
  subscribeToEvents,
  decideApproval,
  cancelRun,
} from './lib/api';

const TERMINAL_STATUSES = new Set(['handoff', 'succeeded', 'failed', 'cancelled']);
import SessionList from './components/SessionList';
import ChatStream from './components/ChatStream';
import NewSessionDialog from './components/NewSessionDialog';
import RightPanel from './components/RightPanel';
import ApprovalsInbox from './components/ApprovalsInbox';
import RunRecordsPage from './components/RunRecordsPage';
import MetricsPage from './components/MetricsPage';
import { useApprovalsPolling } from './useApprovalsPolling';

type AppView = 'chat' | 'approvals' | 'runs' | 'metrics';

function isApprovalRequestEvent(
  data: Record<string, unknown>,
): data is Record<string, unknown> & Pick<ApprovalRequest, 'approval_id' | 'action' | 'args'> {
  return (
    typeof data.approval_id === 'string' &&
    typeof data.action === 'string' &&
    typeof data.args === 'object' &&
    data.args !== null &&
    !Array.isArray(data.args)
  );
}

function isRunStatus(value: unknown): value is Run['status'] {
  return [
    'queued',
    'running',
    'waiting_approval',
    'handoff',
    'succeeded',
    'failed',
    'cancelled',
  ].some((status) => status === value);
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [currentRun, setCurrentRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [openDialog, setOpenDialog] = useState(false);
  const [approvals, setApprovals] = useState<ApprovalInboxItem[]>([]);
  const eventSourceRef = useRef<(() => void) | null>(null);
  const [activeView, setActiveView] = useState<AppView>('chat');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [runsLoadError, setRunsLoadError] = useState<string | null>(null);

  const refreshApprovals = useCallback(async () => {
    setApprovals(await listApprovals());
    setApprovalError(null);
  }, []);

  useApprovalsPolling(refreshApprovals);

  const resetSessionRun = useCallback(() => {
    eventSourceRef.current?.();
    eventSourceRef.current = null;
    setCurrentRun(null);
    setEvents([]);
  }, []);

  const handleNewSession = useCallback(
    async (form: NewSessionForm) => {
      const session = await createSession(form);
      setSessions((prev) => [session, ...prev]);
      resetSessionRun();
      setSelectedSession(session);
      setOpenDialog(false);
    },
    [resetSessionRun],
  );

  // One event handler for live stream events — the same whether the run was started from this
  // tab or reattached to when reopening a session (ANVIL-73).
  const makeEventHandler = useCallback((run: Run, session: Session) => (event: StreamEvent) => {
    setEvents((prev) => prev.some((e) => e.seq === event.seq) ? prev : [...prev, event]);

    if (event.type === 'approval.request' && isApprovalRequestEvent(event.data)) {
      const request: ApprovalRequest = {
        ...event.data,
        reason: typeof event.data.reason === 'string' ? event.data.reason : null,
        agent: event.agent ?? 'main',
      };
      const approval: ApprovalInboxItem = {
        ...request,
        run_id: run.id,
        session_id: session.id,
        session_title: session.title,
        repo: session.repo,
        run_status: 'waiting_approval',
        requested_at: event.ts,
      };
      setApprovals((previous) => (
        previous.some((item) => item.approval_id === approval.approval_id)
          ? previous
          : [approval, ...previous]
      ));
    }

    if (event.type === 'run.status') {
      const status = event.data.status;
      if (isRunStatus(status)) {
        setCurrentRun((prev) => prev ? { ...prev, status } : prev);
        // Re-fetch the run on terminal status to get updated cost_usd/usd
        if (status === 'succeeded' || status === 'failed' || status === 'handoff' || status === 'cancelled') {
          getRun(run.id).then((updated) => {
            if (updated) setCurrentRun(updated);
          }).catch(() => {});
        }
      }
    }
  }, []);

  // Load a session's latest run: timeline into the chat, and if it is still active reattach the
  // stream at the last seen seq so no event arrives twice (ANVIL-73). attachSeq guards the race:
  // an in-flight attach for session A must not overwrite session B's chat or stream (review #79).
  const attachSeq = useRef(0);
  const attachToRun = useCallback(async (session: Session) => {
    const mine = ++attachSeq.current;
    const stale = () => mine !== attachSeq.current;
    try {
      const runs = await listRunsForSession(session.id);
      if (stale()) return;
      const latest = runs[0];   // the API lists newest first
      if (!latest) {
        setRunsLoadError(null);
        return;
      }
      const timeline = await getRunTimeline(latest.id);
      if (stale()) return;
      setCurrentRun(latest);
      setEvents(timeline);
      setRunsLoadError(null);
      if (!TERMINAL_STATUSES.has(latest.status)) {
        const lastSeq = timeline.length ? timeline[timeline.length - 1].seq : undefined;
        const unsub = subscribeToEvents(latest.id, makeEventHandler(latest, session),
          { afterSeq: lastSeq });
        if (stale()) {          // another select landed while the stream connected
          unsub();
          return;
        }
        eventSourceRef.current = unsub;
      }
    } catch (err) {
      if (stale()) return;
      setRunsLoadError(err instanceof Error ? err.message : String(err));
    }
  }, [makeEventHandler]);

  // A run belongs to its session: switching sessions must drop the old run's stream and state,
  // or the panels would show (and approve) another session's run.
  const handleSelectSession = useCallback((session: Session) => {
    if (selectedSession?.id === session.id) return;
    attachSeq.current += 1;                       // cancel any in-flight attach for the old session
    if (eventSourceRef.current) {
      eventSourceRef.current();
      eventSourceRef.current = null;
    }
    setCurrentRun(null);
    setEvents([]);
    setSelectedSession(session);
    void attachToRun(session);
  }, [selectedSession, attachToRun]);

  useEffect(() => {
    let cancelled = false;
    listSessions()
      .then((found) => {
        if (cancelled) return;
        setSessions(found);
        const first = found[0] ?? null;
        setSelectedSession(first);
        if (first) void attachToRun(first);   // reopening lands on the first session's run (ANVIL-73)
        setLoadError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err));
      });
    refreshApprovals().catch((err: unknown) => {
      if (!cancelled) setApprovalError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      cancelled = true;
      attachSeq.current += 1;   // unmount cancels an in-flight attach
      eventSourceRef.current?.();
      eventSourceRef.current = null;
    };
  }, [refreshApprovals, attachToRun]);

  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!selectedSession) return;
      eventSourceRef.current?.();
      eventSourceRef.current = null;
      const run = await sendMessage(selectedSession.id, text);
      setCurrentRun(run);
      setEvents([]);
      const unsubscribe = subscribeToEvents(run.id, makeEventHandler(run, selectedSession));
      eventSourceRef.current = unsubscribe;
    },
    [selectedSession, makeEventHandler],
  );

  const handleApprove = useCallback(
    async (approvalId: string, comment: string) => {
      await decideApproval(approvalId, 'approve', comment);
      await refreshApprovals();
    },
    [refreshApprovals],
  );

  const handleReject = useCallback(
    async (approvalId: string, comment: string) => {
      await decideApproval(approvalId, 'reject', comment);
      await refreshApprovals();
    },
    [refreshApprovals],
  );

  const handleCancel = useCallback(async () => {
    if (!currentRun) return;
    await cancelRun(currentRun.id);
    setCurrentRun((prev) =>
      prev ? { ...prev, status: 'cancelled' } : prev,
    );
  }, [currentRun]);

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <header className="h-14 bg-white border-b border-slate-200 text-slate-900 px-6 flex items-center gap-3 shrink-0">
        <img src="/icon-192.png" alt="" aria-hidden="true" className="w-8 h-8 rounded-lg" />
        <h1 className="font-semibold text-lg">Anvil</h1>
        <nav className="ml-auto flex items-center gap-1" aria-label="Main navigation">
          {([
            ['chat', 'Chat'],
            ['approvals', 'Approvals'],
            ['runs', 'Run records'],
            ['metrics', 'Metrics'],
          ] as const).map(([view, label]) => (
            <button
              key={view}
              onClick={() => setActiveView(view)}
              aria-current={activeView === view ? 'page' : undefined}
              className={`border-b-2 px-3 py-4 text-sm ${
                activeView === view
                  ? 'border-blue-600 font-medium text-slate-900'
                  : 'border-transparent text-slate-500 hover:text-slate-900'
              }`}
            >
              {label}
              {view === 'approvals' && approvals.length > 0 && (
                <span className="ml-1 rounded-full bg-amber-400 px-1.5 text-xs text-slate-900">
                  {approvals.length}
                </span>
              )}
            </button>
          ))}
        </nav>
        {activeView === 'chat' && (
          <button onClick={() => setOpenDialog(true)} className="btn-secondary text-xs px-3 py-1.5">
            New Session
          </button>
        )}
      </header>
      {runsLoadError && (
        <p role="alert" className="bg-amber-50 px-6 py-1.5 text-xs text-amber-800 border-b border-amber-200">
          Could not load the session's runs: {runsLoadError}
        </p>
      )}
      <main className="flex-1 min-h-0 flex overflow-hidden">
        {activeView === 'chat' && selectedSession && (
          <>
            <SessionList
              sessions={sessions}
              selected={selectedSession}
              onSelect={handleSelectSession}
            />
            <div className="flex-1 min-w-0 min-h-0 flex">
              <div className="flex-1 min-w-0 min-h-0 flex flex-col">
                <ChatStream
                  session={selectedSession}
                  run={currentRun}
                  events={events}
                  onSendMessage={handleSendMessage}
                />
              </div>
              <RightPanel
                run={currentRun}
                events={events}
                approvals={approvals.filter((item) => item.session_id === selectedSession.id)}
                onApprove={handleApprove}
                onReject={handleReject}
                onCancel={handleCancel}
              />
            </div>
          </>
        )}
        {activeView === 'chat' && !selectedSession && (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <p className="text-slate-500">
                {loadError ? `Could not load sessions: ${loadError}` : 'No sessions yet.'}
              </p>
              <button onClick={() => setOpenDialog(true)} className="mt-4 btn-primary">
                New Session
              </button>
            </div>
          </div>
        )}
        {activeView === 'approvals' && (
          <section className="flex-1 overflow-y-auto p-6">
            <div className="max-w-3xl mx-auto space-y-4">
              <div>
                <h2 className="text-xl font-semibold">Approvals inbox</h2>
                <p className="text-sm text-slate-500">Pending actions across all sessions.</p>
              </div>
              {approvalError && <p role="alert" className="text-sm text-red-700">{approvalError}</p>}
              <ApprovalsInbox
                approvals={approvals}
                onApprove={handleApprove}
                onReject={handleReject}
                showEmpty
              />
            </div>
          </section>
        )}
        {activeView === 'runs' && <RunRecordsPage />}
        {activeView === 'metrics' && <MetricsPage />}
      </main>
      {openDialog && (
        <NewSessionDialog onSubmit={handleNewSession} onClose={() => setOpenDialog(false)} />
      )}
    </div>
  );
}