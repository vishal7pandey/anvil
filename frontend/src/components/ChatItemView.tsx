import { useState } from 'react';
import type { ChatItem, Action } from '../chat/chatItems';
import { itemEvents } from '../chat/chatItems';

/**
 * One chat item (ANVIL-52). Neutral surfaces; colour only where it means something:
 * blue = you, amber = needs a person, green = succeeded, red = failed.
 */

function Raw({ item }: { item: ChatItem }) {
  return (
    <details className="mt-2 text-[11px] text-slate-400">
      <summary className="cursor-pointer select-none hover:text-slate-600">raw events</summary>
      <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-2 text-slate-600">
        {JSON.stringify(itemEvents(item), null, 2)}
      </pre>
    </details>
  );
}

function Dot({ tone }: { tone: 'running' | 'ok' | 'failed' | 'neutral' | 'amber' }) {
  const cls = {
    running: 'bg-blue-500 animate-pulse',
    ok: 'bg-emerald-600',
    failed: 'bg-red-600',
    neutral: 'bg-slate-400',
    amber: 'bg-amber-500',
  }[tone];
  return <span aria-hidden className={`inline-block h-2 w-2 shrink-0 rounded-full ${cls}`} />;
}

const LONG_TEXT = 600;

/** Long text (a whole ticket pasted as the first message) starts clamped. */
function Prose({ text, className }: { text: string; className: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > LONG_TEXT;
  return (
    <>
      <div className={`whitespace-pre-wrap ${className} ${long && !open ? 'line-clamp-4' : ''}`}>{text}</div>
      {long && (
        <button type="button" onClick={() => setOpen(!open)} className="mt-1 text-xs text-blue-700 hover:underline">
          {open ? 'Show less' : 'Show more'}
        </button>
      )}
    </>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <div className="mb-1 text-xs font-medium text-slate-500">{children}</div>;
}

function ActionRow({ action }: { action: Action }) {
  const detail = action.output || null;
  return (
    <li className="py-1.5">
      <details className="group">
        <summary className="flex cursor-pointer select-none items-center gap-2 text-sm">
          <Dot tone={action.status} />
          <span className="shrink-0 font-mono text-xs text-slate-500">{action.name}</span>
          <span className="min-w-0 truncate font-mono text-xs text-slate-800">{action.summary}</span>
          {action.exitCode !== null && action.exitCode !== 0 && (
            <span className="shrink-0 text-xs text-red-700">exit {action.exitCode}</span>
          )}
          {action.status === 'failed' && action.exitCode === null && (
            <span className="shrink-0 text-xs text-red-700">failed</span>
          )}
        </summary>
        {detail && (
          <pre className="ml-4 mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded border border-slate-200 bg-slate-50 p-2 text-[11px] text-slate-700">
            {detail}
          </pre>
        )}
      </details>
    </li>
  );
}

function Activity({ item }: { item: Extract<ChatItem, { kind: 'activity' }> }) {
  const { actions } = item;
  const failed = actions.filter((a) => a.status === 'failed').length;
  const running = actions.some((a) => a.status === 'running');
  const latest = actions[actions.length - 1];
  const tone = running ? 'running' : failed ? 'failed' : 'ok';
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
      <details>
        <summary className="flex cursor-pointer select-none items-center gap-2 text-sm text-slate-700">
          <Dot tone={tone} />
          <span className="shrink-0">
            {actions.length} {actions.length === 1 ? 'action' : 'actions'}
            {failed > 0 && <span className="text-red-700"> · {failed} failed</span>}
          </span>
          <span className="min-w-0 truncate font-mono text-xs text-slate-400">
            {running ? 'now: ' : 'last: '}
            {latest.name} {latest.summary}
          </span>
        </summary>
        <ul className="mt-1 divide-y divide-slate-100 border-t border-slate-100">
          {actions.map((a) => (
            <ActionRow key={a.callId} action={a} />
          ))}
        </ul>
        <Raw item={item} />
      </details>
    </div>
  );
}

const STATUS_TEXT: Record<string, { text: string; cls: string }> = {
  waiting_approval: { text: 'Waiting for your approval', cls: 'text-amber-800 bg-amber-50 border-amber-200' },
  handoff: { text: 'Handed off: a person needs to take over', cls: 'text-amber-800 bg-amber-50 border-amber-200' },
  succeeded: { text: 'Run succeeded', cls: 'text-emerald-800 bg-emerald-50 border-emerald-200' },
  failed: { text: 'Run failed', cls: 'text-red-800 bg-red-50 border-red-200' },
  cancelled: { text: 'Run cancelled', cls: 'text-slate-700 bg-slate-100 border-slate-200' },
};

function argText(value: unknown): string {
  const s = typeof value === 'string' ? value : JSON.stringify(value);
  return s.length > 300 ? `${s.slice(0, 299)}…` : s;
}

export default function ChatItemView({ item }: { item: ChatItem }) {
  switch (item.kind) {
    case 'user':
      return (
        <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2">
          <Label>You</Label>
          <Prose text={item.text} className="text-sm text-slate-900" />
        </div>
      );
    case 'assistant':
      return (
        <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
          <Label>{item.agent === 'main' ? 'Agent' : `Agent (${item.agent})`}</Label>
          <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-900">
            {item.text}
            {item.streaming && <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-slate-400 align-middle" />}
          </div>
        </div>
      );
    case 'activity':
      return <Activity item={item} />;
    case 'approval':
      return (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2">
          <div className="mb-1 flex items-center gap-2 text-sm font-medium text-amber-900">
            <Dot tone="amber" /> Needs approval: <span className="font-mono">{item.action}</span>
          </div>
          {item.reason && <p className="mb-2 text-sm text-amber-900">{item.reason}</p>}
          {Object.keys(item.args).length > 0 && (
            <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
              {Object.entries(item.args).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="font-mono text-amber-800">{k}</dt>
                  <dd className="whitespace-pre-wrap break-words text-slate-800">{argText(v)}</dd>
                </div>
              ))}
            </dl>
          )}
          <p className="mt-2 text-xs text-amber-800">Decide in the Approvals panel.</p>
        </div>
      );
    case 'subagent':
      return (
        <div className="flex items-center gap-2 px-1 text-xs text-slate-500">
          <Dot tone="neutral" />
          Subagent <span className="font-mono">{item.name}</span> {item.phase === 'start' ? 'started' : 'finished'}
          {item.summary && <span className="min-w-0 truncate">: {item.summary}</span>}
        </div>
      );
    case 'artifact':
      return (
        <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm">
          <span className="text-xs text-slate-500">{item.artifactKind}</span>
          {item.url ? (
            <a href={item.url} target="_blank" rel="noreferrer" className="truncate text-blue-700 underline">
              {item.title}
            </a>
          ) : (
            <span className="truncate text-slate-800">{item.title}</span>
          )}
        </div>
      );
    case 'status': {
      const s = STATUS_TEXT[item.status] ?? { text: item.status, cls: 'text-slate-700 bg-slate-100 border-slate-200' };
      return (
        <div className="flex justify-center">
          <span className={`rounded-full border px-3 py-0.5 text-xs font-medium ${s.cls}`}>{s.text}</span>
        </div>
      );
    }
    case 'note':
      return (
        <div className="flex justify-center">
          <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-0.5 text-xs text-slate-500">{item.text}</span>
        </div>
      );
    case 'error':
      return (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2">
          <Label>Error</Label>
          <div className="whitespace-pre-wrap break-words font-mono text-xs text-red-900">{item.text}</div>
          <Raw item={item} />
        </div>
      );
  }
}
