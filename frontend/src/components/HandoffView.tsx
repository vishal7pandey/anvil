import type { Run } from '../types';

interface HandoffViewProps {
  run: Run | null;
}

export default function HandoffView({ run }: HandoffViewProps) {
  if (!run || run.status !== 'handoff') return null;
  const handoff = run.artifacts.find((artifact) => artifact.kind === 'handoff');

  return (
    <section className="card space-y-3">
      <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wider flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
        Handoff — human action needed
      </h3>
      <div className="p-3 bg-amber-50 border border-amber-200 rounded-md">
        <p className="text-sm text-amber-900 mb-3">
          The agent run has entered a handoff state. Review the handoff details and WIP branch before taking over.
        </p>
        {handoff?.markdown && (
          <pre className="whitespace-pre-wrap text-xs text-amber-950 mb-3">{handoff.markdown}</pre>
        )}
        {handoff?.url && (
          <a
            href={handoff.url}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-blue-700 underline"
          >
            Open WIP branch
          </a>
        )}
      </div>
      <div className="space-y-2 text-sm">
        <div className="flex justify-between">
          <span className="text-slate-600">Status</span>
          <span className="font-mono text-amber-700">{run.status}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-600">Run ID</span>
          <span className="font-mono text-slate-700">{run.id}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-600">Branch</span>
          <span className="font-mono text-slate-700">{run.branch ?? '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-600">WIP branch</span>
          <span className="font-mono text-slate-700">{run.branch ? `${run.branch}-wip` : '—'}</span>
        </div>
      </div>
    </section>
  );
}