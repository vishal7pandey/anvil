import { type StreamEvent } from '../types';

interface EventBubbleProps {
  event: StreamEvent;
}

function formatData(data: Record<string, unknown>): string {
  return JSON.stringify(data, null, 2).slice(0, 200);
}

function getEventIcon(type: string): string {
  switch (type) {
    case 'run.status': return '🔄';
    case 'message': return '💬';
    case 'message.delta': return '✍️';
    case 'plan': return '📋';
    case 'tool.call': return '🔧';
    case 'tool.result': return '✅';
    case 'sandbox.exec': return '🖥️';
    case 'diff': return '📝';
    case 'approval.request': return '⏳';
    case 'subagent': return '🤖';
    case 'usage': return '💰';
    case 'artifact': return '📎';
    default: return '📡';
  }
}

export default function EventBubble({ event }: EventBubbleProps) {
  const icon = getEventIcon(event.type);
  const isToolResult = event.type === 'tool.result';
  const isDiff = event.type === 'diff';
  const isApproval = event.type === 'approval.request';

  const baseClasses = 'p-3 rounded-md border text-sm';
  const typeClasses: Record<string, string> = {
    'run.status': 'bg-blue-50 border-blue-200 text-blue-900',
    message: 'bg-green-50 border-green-200 text-green-900',
    'message.delta': 'bg-green-50 border-green-200 text-green-900',
    plan: 'bg-indigo-50 border-indigo-200 text-indigo-900',
    'tool.call': 'bg-purple-50 border-purple-200 text-purple-900',
    'tool.result': 'bg-emerald-50 border-emerald-200 text-emerald-900',
    'sandbox.exec': 'bg-orange-50 border-orange-200 text-orange-900',
    diff: 'bg-pink-50 border-pink-200 text-pink-900',
    'approval.request': 'bg-amber-50 border-amber-200 text-amber-900',
    subagent: 'bg-violet-50 border-violet-200 text-violet-900',
    usage: 'bg-gray-50 border-gray-200 text-gray-900',
    artifact: 'bg-slate-50 border-slate-200 text-slate-900',
  };

  return (
    <div className={`flex items-start gap-3 ${baseClasses} ${typeClasses[event.type] || typeClasses['run.status']}`}>
      <span className="text-lg shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-1">
          <span className="text-[10px] font-bold uppercase tracking-wider">
            {event.type}
          </span>
          <span className="text-[10px] opacity-60">{event.agent}</span>
          <span className="text-[10px] opacity-60">#{event.seq}</span>
        </div>
        {isToolResult && (
          <div className="text-xs font-mono mb-1">
            {(event.data as { ok: boolean }).ok ? 'ok' : 'error'} ·
            {(event.data as { duration_ms: number }).duration_ms}ms
          </div>
        )}
        {isDiff && (
          <div className="text-xs font-mono mb-1">
            {(event.data as { files: unknown[] }).files.length} file(s) changed
          </div>
        )}
        {isApproval && (
          <div className="text-xs font-mono mb-1">
            {(event.data as { action: string }).action} · {(event.data as { reason: string }).reason}
          </div>
        )}
        <pre className="text-[10px] break-all overflow-x-auto">{formatData(event.data)}</pre>
      </div>
    </div>
  );
}