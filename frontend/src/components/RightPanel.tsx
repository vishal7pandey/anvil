import { type ApprovalInboxItem, type StreamEvent, type Run } from '../types';
import PlanPanel from './PlanPanel';
import DiffViewer from './DiffViewer';
import ApprovalsInbox from './ApprovalsInbox';
import CostMeter from './CostMeter';
import HandoffView from './HandoffView';

interface RightPanelProps {
  run: Run | null;
  events: StreamEvent[];
  approvals: ApprovalInboxItem[];
  onApprove: (id: string, comment: string) => Promise<void>;
  onReject: (id: string, comment: string) => Promise<void>;
  onCancel?: () => void;
}

export default function RightPanel({
  run,
  events,
  approvals,
  onApprove,
  onReject,
  onCancel,
}: RightPanelProps) {
  // Extract plan event
  const planEvent = events
    .slice()
    .reverse()
    .find((e) => e.type === 'plan');

  // Extract latest diff event
  const diffEvent = events
    .slice()
    .reverse()
    .find((e) => e.type === 'diff');

  return (
    <aside className="w-96 border-l border-slate-200 bg-white shrink-0 flex flex-col">
      <div className="p-4 border-b border-slate-200 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Run Record</h2>
        <div className="flex gap-2">
          {run ? (
            <>
              <span
                className={`px-2 py-0.5 rounded text-xs font-medium ${
                  run.status === 'succeeded'
                    ? 'bg-green-100 text-green-700'
                    : run.status === 'failed'
                      ? 'bg-red-100 text-red-700'
                      : run.status === 'waiting_approval'
                        ? 'bg-amber-100 text-amber-700'
                        : 'bg-slate-100 text-slate-700'
                }`}
              >
                {run.status.replace('_', ' ')}
              </span>
              {(run.status === 'running' || run.status === 'waiting_approval') && onCancel ? (
                <button
                  onClick={onCancel}
                  className="btn-danger text-xs px-2 py-1"
                >
                  Cancel
                </button>
              ) : null}
            </>
          ) : (
            <span className="px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-500">
              idle
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-6">
        {planEvent && <PlanPanel plan={planEvent.data as { todos: Array<{ content: string; status: string }> }} />}
        {diffEvent && <DiffViewer diff={diffEvent.data as { files: Array<{ path: string; patch: string }> }} />}
        <ApprovalsInbox
          approvals={approvals}
          onApprove={onApprove}
          onReject={onReject}
        />
        <CostMeter events={events} />
        <HandoffView run={run} />
      </div>
    </aside>
  );
}