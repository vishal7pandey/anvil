import { useState, type FormEvent } from 'react';
import type { NewSessionForm } from '../types';

interface NewSessionDialogProps {
  onSubmit: (form: NewSessionForm) => Promise<void>;
  onClose: () => void;
}

export default function NewSessionDialog({
  onSubmit,
  onClose,
}: NewSessionDialogProps) {
  const [form, setForm] = useState<NewSessionForm>({
    repo: '',
    environment: 'local',
    mode: 'interactive',
    model_profile: 'anvil-main',
    title: '',
  });
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await onSubmit(form);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : 'Failed to create session',
      );
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="relative z-10 bg-white rounded-lg shadow-xl w-full max-w-md p-6">
        <h2 className="text-lg font-semibold text-slate-900 mb-4">New Session</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="label">Title</label>
            <input
              type="text"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="e.g. Fix pthread_mutex_init defect"
              className="input"
            />
          </div>
          {error && (
            <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1.5">
              {error}
            </p>
          )}
          <div>
            <label className="label">Repository</label>
            <input
              type="text"
              value={form.repo}
              onChange={(e) => setForm({ ...form, repo: e.target.value })}
              placeholder="vishal7pandey/rtems or C:\work\rtems"
              className="input"
            />
            <p className="text-xs text-slate-400 mt-1">
              GitHub owner/repository or an absolute local path
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">Environment</label>
              <select
                value={form.environment}
                onChange={(e) =>
                  setForm({ ...form, environment: e.target.value as 'local' | 'cloud' })
                }
                className="input"
                disabled
              >
                <option value="local">Local sandbox</option>
                <option value="cloud" disabled>
                  Cloud (GitHub Actions)
                </option>
              </select>
              <p className="text-xs text-slate-400 mt-1">Runs use the local sandbox.</p>
            </div>
            <div>
              <label className="label">Mode</label>
              <select
                value={form.mode}
                onChange={(e) =>
                  setForm({ ...form, mode: e.target.value as 'interactive' | 'autonomous' })
                }
                className="input"
              >
                <option value="interactive">
                  Interactive
                </option>
                <option value="autonomous">Autonomous</option>
              </select>
              <p className="text-xs text-slate-400 mt-1">
                {form.mode === 'interactive'
                  ? 'Every tool waits for your approval (TC1 default)'
                  : 'Agent runs without waiting'}
              </p>
            </div>
          </div>
          <div>
            <label className="label">Model profile</label>
            <select
              value={form.model_profile}
              onChange={(e) =>
                setForm({ ...form, model_profile: e.target.value as 'anvil-main' | 'anvil-fast' | 'anvil-embed' })
              }
              className="input"
            >
              <option value="anvil-main">anvil-main</option>
              <option value="anvil-fast">anvil-fast</option>
              <option value="anvil-embed">anvil-embed</option>
            </select>
            <p className="text-xs text-slate-400 mt-1">
              {form.model_profile === 'anvil-main'
                ? 'Balanced model for general tasks (TC1 default)'
                : form.model_profile === 'anvil-fast'
                ? 'Faster, cheaper model for simple tasks'
                : 'Embedding model for knowledge base'}
            </p>
          </div>
          <div className="flex gap-3 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="btn-secondary flex-1"
            >
              Cancel
            </button>
            <button type="submit" className="btn-primary flex-1" disabled={!form.repo}>
              Create
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}