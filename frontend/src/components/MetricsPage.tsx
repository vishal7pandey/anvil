import { useEffect, useState, type FormEvent } from 'react';
import { getMetrics } from '../lib/api';
import type { Metrics } from '../types';

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—';
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  return minutes ? `${minutes}m ${remainingSeconds}s` : `${remainingSeconds}s`;
}

export default function MetricsPage() {
  const [scenarioInput, setScenarioInput] = useState('');
  const [scenario, setScenario] = useState<string | undefined>();
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getMetrics(scenario)
      .then((result) => {
        if (!cancelled) {
          setMetrics(result);
          setError(null);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scenario]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setScenario(scenarioInput.trim() || undefined);
  };

  return (
    <section className="flex-1 overflow-y-auto p-6">
      <div className="max-w-4xl mx-auto space-y-6">
        <div>
          <h2 className="text-xl font-semibold">Metrics</h2>
          <p className="text-sm text-slate-500">Run duration, model spend, human touches, and CI rounds.</p>
        </div>
        <form onSubmit={handleSubmit} className="card flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-48">
            <label htmlFor="metrics-scenario" className="label">Scenario</label>
            <input
              id="metrics-scenario"
              value={scenarioInput}
              onChange={(event) => setScenarioInput(event.target.value)}
              className="input"
              placeholder="All scenarios"
            />
          </div>
          <button type="submit" className="btn-primary">Apply filter</button>
        </form>

        {loading && <p className="text-sm text-slate-500">Loading metrics…</p>}
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        {metrics && !error && (
          <>
            <p className="text-sm text-slate-500">
              {metrics.scenario ? `Scenario: ${metrics.scenario}` : 'All scenarios'} · {metrics.run_count} run(s)
            </p>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Metric label="Total run time" value={formatDuration(metrics.total_wall_time_seconds)} />
              <Metric label="Average run time" value={formatDuration(metrics.average_wall_time_seconds)} />
              <Metric
                label="USD"
                value={
                  metrics.usd === null || metrics.cost_status === 'missing'
                    ? 'Cost unavailable'
                    : metrics.cost_status === 'partial'
                      ? `$${metrics.usd.toFixed(4)} (${metrics.runs_missing_cost} runs without cost data)`
                      : `$${metrics.usd.toFixed(4)}`
                }
              />
              <Metric label="Model calls" value={metrics.model_calls?.toLocaleString() ?? 'Unavailable'} />
              <Metric label="Human touches" value={metrics.human_touches.toLocaleString()} />
              <Metric label="CI rounds" value={metrics.ci_rounds.toLocaleString()} />
            </div>
            {metrics.cost_status !== 'available' && (
              <p className="text-sm text-amber-800">
                {metrics.cost_status === 'partial'
                  ? `${metrics.runs_missing_cost} run(s) do not have spend data.`
                  : 'Cost data is unavailable for all runs.'}
              </p>
            )}
            <section className="card">
              <h3 className="font-semibold mb-2">Outcomes</h3>
              {Object.keys(metrics.outcomes).length === 0 ? (
                <p className="text-sm text-slate-500">No outcomes recorded.</p>
              ) : (
                <ul className="flex flex-wrap gap-4 text-sm">
                  {Object.entries(metrics.outcomes).map(([outcome, count]) => (
                    <li key={outcome}>{outcome}: <strong>{count}</strong></li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <section className="card">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold">{value}</p>
    </section>
  );
}
