interface DiffViewerProps {
  diff: { files: Array<{ path: string; patch: string }> };
}

export default function DiffViewer({ diff }: DiffViewerProps) {
  return (
    <section className="card space-y-3">
      <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wider">Diff</h3>
      <div className="space-y-2">
        {diff.files.map((file, idx) => (
          <div key={idx} className="flex items-start gap-2">
            <span className="text-xs font-mono text-slate-500">{file.path}</span>
            <pre className="text-[10px] text-slate-600 break-all max-h-32 overflow-hidden">
              {file.patch}
            </pre>
          </div>
        ))}
      </div>
    </section>
  );
}