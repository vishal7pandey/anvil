import { useEffect, useState } from 'react';
import { getRun, getRunTimeline, listRuns } from '../lib/api';
import type { Run, StreamEvent } from '../types';

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

export default function RunRecordsPage() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [timeline, setTimeline] = useState<StreamEvent[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    listRuns()
      .then((items) => {
        if (cancelled) return;
        setRuns(items);
        setSelectedId(items[0]?.id ?? null);
      })
      .catch((error: unknown) => {
        if (!cancelled) setListError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setRun(null);
      setTimeline([]);
      return;
    }

    let cancelled = false;
    setRun(null);
    setDetailError(null);
    Promise.all([getRun(selectedId), getRunTimeline(selectedId)])
      .then(([record, events]) => {
        if (cancelled) return;
        setRun(record);
        setTimeline(events);
      })
      .catch((error: unknown) => {
        if (!cancelled) setDetailError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  return (
    <div className="flex-1 min-h-0 flex">
      <aside className="w-80 shrink-0 border-r border-slate-200 bg-white overflow-y-auto p-4">
        <h2 className="text-lg font-semibold mb-3">Run records</h2>
        {loading && <p className="text-sm text-slate-500">Loading runs…</p>}
        {listError && <p role="alert" className="text-sm text-red-700">{listError}</p>}
        {!loading && !listError && runs.length === 0 && (
          <p className="text-sm text-slate-500">No runs yet.</p>
        )}
        <div className="space-y-2">
          {runs.map((item) => (
            <button
              key={item.id}
              onClick={() => setSelectedId(item.id)}
              aria-pressed={selectedId === item.id}
              className={`w-full rounded-md border p-3 text-left text-sm ${
                selectedId === item.id ? 'border-blue-400 bg-blue-50' : 'border-slate-200'
              }`}
            >
              <span className="block font-medium">{item.jira_key ?? item.repo}</span>
              <span className="block text-xs text-slate-500">{item.status} · {new Date(item.created_at).toLocaleString()}</span>
              <span className="block truncate text-xs text-slate-500">{item.id}</span>
            </button>
          ))}
        </div>
      </aside>

      <section className="flex-1 min-w-0 overflow-y-auto p-6">
        {detailError && <p role="alert" className="text-sm text-red-700">{detailError}</p>}
        {selectedId && !run && !detailError && <p className="text-sm text-slate-500">Loading run record…</p>}
        {run && (
          <div className="max-w-4xl space-y-6">
            <div>
              <h2 className="text-xl font-semibold">Run record</h2>
              <p className="text-sm text-slate-500 break-all">{run.id}</p>
            </div>
            <section className="card">
              <h3 className="font-semibold mb-3">Reproducibility</h3>
              <dl className="grid gap-3 sm:grid-cols-2 text-sm">
                {([
                  ['Status', run.status],
                  ['Scenario', run.run_record.scenario],
                  ['Repository', run.repo],
                  ['Base ref', run.base_ref],
                  ['Base SHA', run.base_sha ?? run.run_record.base_sha],
                  ['Branch', run.branch ?? run.run_record.branch],
                  ['Network', run.network],
                  ['Sandbox image digest', run.sandbox_image_digest ?? run.run_record.sandbox_image_digest],
                  ['Models', run.model_aliases],
                  ['Skills', run.skill_versions],
                  ['Prompt hash', run.prompt_hash ?? run.run_record.prompt_hash],
                  ['Budgets', run.budgets],
                ] as Array<[string, unknown]>).map(([label, value]) => (
                  <div key={label}>
                    <dt className="text-slate-500">{label}</dt>
                    <dd className="font-mono break-all">{displayValue(value)}</dd>
                  </div>
                ))}
              </dl>
            </section>

            <section className="card">
              <h3 className="font-semibold mb-3">Artifacts</h3>
              {run.artifacts.length === 0 ? (
                <p className="text-sm text-slate-500">No artifacts recorded.</p>
              ) : (
                <ul className="space-y-2">
                  {run.artifacts.map((artifact, index) => (
                    <li key={`${artifact.kind}-${artifact.url ?? index}`} className="text-sm">
                      {artifact.url ? (
                        <a href={artifact.url} target="_blank" rel="noreferrer" className="text-blue-700 underline">
                          {artifact.title ?? artifact.kind}
                        </a>
                      ) : (
                        <span>{artifact.title ?? artifact.kind}</span>
                      )}
                      <span className="ml-2 text-xs text-slate-500">{artifact.kind}</span>
                      {artifact.kind === 'handoff' && artifact.markdown && (
                        <pre className="mt-2 whitespace-pre-wrap rounded bg-amber-50 p-3 text-xs">
                          {artifact.markdown}
                        </pre>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="card">
              <h3 className="font-semibold mb-3">Event timeline</h3>
              {timeline.length === 0 ? (
                <p className="text-sm text-slate-500">No timeline events recorded.</p>
              ) : (
                <ol className="space-y-3">
                  {timeline.map((event) => (
                    <li key={`${event.run_id}-${event.seq}`} className="border-l-2 border-slate-200 pl-3">
                      <p className="text-xs text-slate-500">
                        {new Date(event.ts).toLocaleString()} · #{event.seq} · {event.agent ?? 'system'}
                      </p>
                      <p className="font-medium text-sm">{event.type}</p>
                      <pre className="mt-1 whitespace-pre-wrap break-words text-xs text-slate-600">
                        {JSON.stringify(event.data, null, 2)}
                      </pre>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          </div>
        )}
      </section>
    </div>
  );
}
