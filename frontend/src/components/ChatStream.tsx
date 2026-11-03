import { useState, useRef, useEffect, type FormEvent } from 'react';
import type { Session, Run, StreamEvent } from '../types';
import EventBubble from './EventBubble';

interface ChatStreamProps {
  session: Session | null;
  run: Run | null;
  events: StreamEvent[];
  onSendMessage: (text: string) => Promise<void>;
}

export default function ChatStream({
  session,
  run,
  events,
  onSendMessage,
}: ChatStreamProps) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const disabled = !session;

  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [events]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !session) return;
    await onSendMessage(input);
    setInput('');
  };

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-slate-50 relative">
      {!session ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
          <div className="text-xl">👋</div>
          <p className="mt-2 text-sm">Create a session to start</p>
        </div>
      ) : (
        <>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {events.map((event, idx) => (
              <EventBubble key={`${event.seq}-${idx}`} event={event} />
            ))}
            {run && run.status === 'running' && (
              <div className="flex items-center gap-2 text-xs text-slate-400 animate-pulse">
                <span className="w-2 h-2 rounded-full bg-green-500 animate-ping"></span>
                Agent working…
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
          <form onSubmit={handleSubmit} className="p-4 border-t border-slate-200 bg-white shrink-0">
            <div className="flex gap-2">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask the agent to build, fix, or upgrade…"
                className="input flex-1"
                disabled={disabled}
              />
              <button
                type="submit"
                disabled={disabled || !input.trim()}
                className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Send
              </button>
          </div>
          </form>
        </>
      )}
    </div>
  );
}