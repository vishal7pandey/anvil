import { useState } from 'react';
import type { ApprovalInboxItem } from '../types';

interface ApprovalsInboxProps {
  approvals: ApprovalInboxItem[];
  onApprove: (id: string, comment: string) => Promise<void>;
  onReject: (id: string, comment: string) => Promise<void>;
  showEmpty?: boolean;
}

export default function ApprovalsInbox({
  approvals,
  onApprove,
  onReject,
  showEmpty = false,
}: ApprovalsInboxProps) {
  const [comments, setComments] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const decide = async (
    approvalId: string,
    decision: 'approve' | 'reject',
  ) => {
    setBusyId(approvalId);
    setError(null);
    try {
      await (decision === 'approve' ? onApprove : onReject)(
        approvalId,
        comments[approvalId] ?? '',
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  if (approvals.length === 0 && !showEmpty) return null;

  return (
    <section className="card space-y-3">
      <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wider flex items-center gap-2">
        {approvals.length > 0 && <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />}
        Approvals ({approvals.length})
      </h3>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {approvals.length === 0 ? (
        <p className="text-sm text-slate-500">No pending approvals.</p>
      ) : <div className="space-y-3">
        {approvals.map((approval) => (
          <div key={approval.approval_id} className="p-3 bg-amber-50 border border-amber-200 rounded-md">
            <div className="flex justify-between items-start mb-2">
              <span className="text-xs font-mono text-amber-700 truncate">
                {approval.approval_id}
              </span>
              <span className="text-xs text-amber-600">{approval.agent ?? 'agent'}</span>
            </div>
            <div className="text-sm text-slate-800 mb-2">{approval.reason ?? 'No reason given'}</div>
            <p className="text-xs text-slate-600 mb-2">
              {approval.session_title ?? approval.repo} · {approval.repo} · {approval.run_status}
            </p>
            <div className="text-xs text-slate-600 font-mono mb-3">
              {approval.action} {JSON.stringify(approval.args).slice(0, 80)}
            </div>
            <label className="block text-xs font-medium text-slate-700 mb-1" htmlFor={`approval-comment-${approval.approval_id}`}>
              Comment (optional)
            </label>
            <textarea
              id={`approval-comment-${approval.approval_id}`}
              value={comments[approval.approval_id] ?? ''}
              onChange={(event) => setComments((previous) => ({
                ...previous,
                [approval.approval_id]: event.target.value,
              }))}
              maxLength={4000}
              rows={2}
              className="input mb-3"
              disabled={busyId === approval.approval_id}
            />
            <div className="flex gap-2">
              <button
                onClick={() => void decide(approval.approval_id, 'approve')}
                className="flex-1 btn-primary text-xs py-1.5"
                disabled={busyId === approval.approval_id}
              >
                Approve
              </button>
              <button
                onClick={() => void decide(approval.approval_id, 'reject')}
                className="flex-1 btn-danger text-xs py-1.5"
                disabled={busyId === approval.approval_id}
              >
                Reject
              </button>
            </div>
          </div>
        ))}
      </div>}
    </section>
  );
}