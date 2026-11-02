import { type Session } from '../types';

interface SessionListProps {
  sessions: Session[];
  selected: Session | null;
  onSelect: (session: Session) => void;
}

export default function SessionList({
  sessions,
  selected,
  onSelect,
}: SessionListProps) {
  return (
    <aside className="w-80 border-r border-slate-200 bg-white shrink-0 flex flex-col">
      <div className="p-4 border-b border-slate-200">
        <h2 className="text-sm font-semibold text-slate-900">Sessions</h2>
        <p className="text-xs text-slate-500 mt-1">{sessions.length} session(s)</p>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {sessions.length === 0 ? (
          <div className="text-center py-8 text-xs text-slate-400">
            No sessions yet
          </div>
        ) : (
          sessions.map((session) => (
            <button
              key={session.id}
              onClick={() => onSelect(session)}
              className={`w-full px-3 py-2.5 text-left rounded-md text-sm transition-colors ${
                selected?.id === session.id
                  ? 'bg-blue-100 text-blue-900 font-medium border border-blue-200'
                  : 'text-slate-700 hover:bg-slate-100'
              }`}
            >
              <div className="font-medium truncate">{session.title}</div>
              <div className="text-xs text-slate-500 truncate mt-0.5">
                {session.repo} · {session.environment}
              </div>
            </button>
          ))
        )}
      </div>
    </aside>
  );
}