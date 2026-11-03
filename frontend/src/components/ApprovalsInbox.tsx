import type { ApprovalRequest } from '../types';


interface ApprovalsInboxProps {
  approvals: ApprovalRequest[];
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}

export default function ApprovalsInbox({
  approvals,
  onApprove,
  onReject,
}: ApprovalsInboxProps) {
  if (approvals.length === 0) return null;

  return (
    <section className="card space-y-3">
      <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wider flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
        Approvals ({approvals.length})
      </h3>
      <div className="space-y-3">
        {approvals.map((approval) => (
          <div key={approval.approval_id} className="p-3 bg-amber-50 border border-amber-200 rounded-md">
            <div className="flex justify-between items-start mb-2">
              <span className="text-xs font-mono text-amber-700 truncate">
                {approval.approval_id}
              </span>
              <span className="text-xs text-amber-600">{approval.agent}</span>
            </div>
            <div className="text-sm text-slate-800 mb-2">{approval.reason ?? 'No reason given'}</div>
            <div className="text-xs text-slate-600 font-mono mb-3">
              {approval.action} {JSON.stringify(approval.args).slice(0, 80)}
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => onApprove(approval.approval_id)}
                className="flex-1 btn-primary text-xs py-1.5"
              >
                Approve
              </button>
              <button
                onClick={() => onReject(approval.approval_id)}
                className="flex-1 btn-danger text-xs py-1.5"
              >
                Reject
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}