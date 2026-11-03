import { useState, useEffect, useCallback, useRef } from 'react';
import type { StreamEvent, Session, Run, NewSessionForm } from './types';
import {
  createSession,
  listSessions,
  sendMessage,
  subscribeToEvents,
  decideApproval,
  cancelRun,
} from './lib/api';

/** Payload of an `approval.request` event (see the API's event contract). */
interface ApprovalRequestData {
  approval_id: string;
  action: string;
  args: Record<string, unknown>;
  reason?: string | null;
  [key: string]: unknown;
}
import SessionList from './components/SessionList';
import ChatStream from './components/ChatStream';
import NewSessionDialog from './components/NewSessionDialog';
import RightPanel from './components/RightPanel';

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [currentRun, setCurrentRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [openDialog, setOpenDialog] = useState(false);
  const [approvals, setApprovals] = useState<
    Array<{ approval_id: string; [key: string]: unknown }>
  >([]);
  const eventSourceRef = useRef<(() => void) | null>(null);

  const [loadError, setLoadError] = useState<string | null>(null);

  // Only sessions the API returned can be selected: a made-up id would 404 on the first message.
  useEffect(() => {
    let cancelled = false;
    listSessions()
      .then((found) => {
        if (cancelled) return;
        setSessions(found);
        setSelectedSession(found[0] ?? null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleNewSession = useCallback(
    async (form: NewSessionForm) => {
      const session = await createSession(form);
      setSessions((prev) => [session, ...prev]);
      setSelectedSession(session);
      setOpenDialog(false);
    },
    [],
  );

  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!selectedSession) return;
      const run = await sendMessage(selectedSession.id, text);
      setCurrentRun(run);
      setEvents([]);

      // Clean up previous event source
      if (eventSourceRef.current) {
        eventSourceRef.current();
      }

      // Subscribe to SSE event stream
      const unsubscribe = subscribeToEvents(run.id, (event) => {
        setEvents((prev) => [...prev, event]);

        // Track approval requests
        if (event.type === 'approval.request') {
          const request = event.data as ApprovalRequestData;
          setApprovals((prev) =>
            prev.some((a) => a.approval_id === request.approval_id) ? prev : [...prev, request],
          );
        }

        // Update run status
        if (event.type === 'run.status') {
          setCurrentRun((prev) =>
            prev ? { ...prev, status: (event.data as { status: string }).status } : prev,
          );
        }
      });
      eventSourceRef.current = unsubscribe;
    },
    [selectedSession],
  );

  const handleApprove = useCallback(
    async (approvalId: string) => {
      await decideApproval(approvalId, 'approve');
      setApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId));
    },
    [],
  );

  const handleReject = useCallback(
    async (approvalId: string) => {
      await decideApproval(approvalId, 'reject');
      setApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId));
    },
    [],
  );

  const handleCancel = useCallback(async () => {
    if (!currentRun) return;
    await cancelRun(currentRun.id);
    setCurrentRun((prev) =>
      prev ? { ...prev, status: 'cancelled' } : prev,
    );
  }, [currentRun]);

  if (!selectedSession) {
    return (
      <div className="h-screen flex flex-col">
        <header className="h-16 bg-blue-900 text-white px-6 flex items-center gap-3">
          <div className="w-8 h-8 rounded bg-blue-400 flex items-center justify-center text-white font-bold">
            A
          </div>
          <h1 className="font-semibold text-lg">Anvil</h1>
        </header>
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <p className="text-slate-500">
              {loadError ? `Could not load sessions: ${loadError}` : 'No sessions yet.'}
            </p>
            <button
              onClick={() => setOpenDialog(true)}
              className="mt-4 btn-primary"
            >
              New Session
            </button>
          </div>
        </div>
        {openDialog && (
          <NewSessionDialog onSubmit={handleNewSession} onClose={() => setOpenDialog(false)} />
        )}
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <header className="h-16 bg-blue-900 text-white px-6 flex items-center gap-3 shrink-0">
        <div className="w-8 h-8 rounded bg-blue-400 flex items-center justify-center text-white font-bold">
          A
        </div>
        <h1 className="font-semibold text-lg">Anvil</h1>
        <span className="ml-auto text-xs text-blue-200">
          {selectedSession.environment} · {selectedSession.mode}
        </span>
        <button
          onClick={() => setOpenDialog(true)}
          className="btn-secondary text-xs px-3 py-1.5"
        >
          New Session
        </button>
      </header>
      <div className="flex-1 flex overflow-hidden">
        <SessionList
          sessions={sessions}
          selected={selectedSession}
          onSelect={setSelectedSession}
        />
        <div className="flex-1 flex flex-col min-w-0">
          <ChatStream
            session={selectedSession}
            run={currentRun}
            events={events}
            onSendMessage={handleSendMessage}
          />
          <RightPanel
            run={currentRun}
            events={events}
            approvals={approvals}
            onApprove={handleApprove}
            onReject={handleReject}
            onCancel={handleCancel}
          />
        </div>
      </div>
      {openDialog && (
        <NewSessionDialog onSubmit={handleNewSession} onClose={() => setOpenDialog(false)} />
      )}
    </div>
  );
}