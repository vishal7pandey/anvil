import { type StreamEvent } from '../types';

interface CostMeterProps {
  events: StreamEvent[];
}

export default function CostMeter({ events }: CostMeterProps) {
  const usageEvents = events.filter((e) => e.type === 'usage');
  const totalUsd = usageEvents.reduce(
    (sum, e) => sum + Number((e.data as { usd: number }).usd || 0),
    0,
  );
  const totalInputTokens = usageEvents.reduce(
    (sum, e) => sum + Number((e.data as { input_tokens: number }).input_tokens || 0),
    0,
  );
  const totalOutputTokens = usageEvents.reduce(
    (sum, e) => sum + Number((e.data as { output_tokens: number }).output_tokens || 0),
    0,
  );

  if (usageEvents.length === 0) return null;

  return (
    <section className="card space-y-3">
      <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wider">Cost Meter</h3>
      <div className="space-y-2 text-sm">
        <div className="flex justify-between">
          <span className="text-slate-600">Total USD</span>
          <span className="font-mono text-slate-900">${totalUsd.toFixed(4)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-600">Input tokens</span>
          <span className="font-mono text-slate-900">{totalInputTokens.toLocaleString()}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-600">Output tokens</span>
          <span className="font-mono text-slate-900">{totalOutputTokens.toLocaleString()}</span>
        </div>
        <div className="flex justify-between border-t border-slate-200 pt-2">
          <span className="text-slate-600">Events</span>
          <span className="font-mono text-slate-900">{usageEvents.length}</span>
        </div>
      </div>
    </section>
  );
}