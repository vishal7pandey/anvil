interface DiffViewerProps {
  diff: { files: Array<{ path: string; patch: string }> };
}

function lineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'text-slate-500';
  if (line.startsWith('+')) return 'bg-emerald-50 text-emerald-900';
  if (line.startsWith('-')) return 'bg-red-50 text-red-900';
  if (line.startsWith('@@')) return 'text-blue-700';
  return 'text-slate-700';
}

/** Whole patches, scrollable, with added/removed lines tinted (was clipped at 128px, ANVIL-52). */
export default function DiffViewer({ diff }: DiffViewerProps) {
  return (
    <section className="card space-y-3">
      <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wider">
        Diff · {diff.files.length} {diff.files.length === 1 ? 'file' : 'files'}
      </h3>
      <div className="space-y-3">
        {diff.files.map((file, idx) => (
          <details key={idx} open={diff.files.length <= 3} className="rounded border border-slate-200">
            <summary className="cursor-pointer select-none truncate bg-slate-50 px-2 py-1 font-mono text-xs text-slate-700">
              {file.path}
            </summary>
            <pre className="max-h-96 overflow-auto text-[11px] leading-snug">
              {file.patch.split('\n').map((line, i) => (
                <div key={i} className={`whitespace-pre px-2 ${lineClass(line)}`}>
                  {line || ' '}
                </div>
              ))}
            </pre>
          </details>
        ))}
      </div>
    </section>
  );
}
