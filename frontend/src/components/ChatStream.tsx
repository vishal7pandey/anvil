import { useState, useRef, useEffect, useMemo, type FormEvent, type KeyboardEvent } from 'react';
import type { Session, Run, StreamEvent } from '../types';
import { toChatItems } from '../chat/chatItems';
import ChatItemView from './ChatItemView';

interface ChatStreamProps {
  session: Session | null;
  run: Run | null;
  events: StreamEvent[];
  onSendMessage: (text: string) => Promise<void>;
}

const MAX_INPUT_ROWS = 8;

export default function ChatStream({
  session,
  run,
  events,
  onSendMessage,
}: ChatStreamProps) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const disabled = !session;
  // The run as a conversation, not its event log (ANVIL-52).
  const items = useMemo(() => toChatItems(events), [events]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [items.length]);

  const send = async () => {
    if (!input.trim() || !session) return;
    await onSendMessage(input);
    setInput('');
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    await send();
  };

  const handleKeyDown = async (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends; Shift+Enter is a new line (and IME composition is left alone).
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      await send();
    }
  };

  const rows = Math.min(MAX_INPUT_ROWS, Math.max(1, input.split('\n').length));

  return (
    <div className="flex-1 flex flex-col min-w-0 min-h-0 bg-slate-50 relative">
      {!session ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
          <p className="text-sm">Create a session to start</p>
        </div>
      ) : (
        <>
          <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
            {items.map((item) => (
              <ChatItemView key={item.key} item={item} />
            ))}
            {run && run.status === 'running' && (
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse"></span>
                Agent working…
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
          <form onSubmit={handleSubmit} className="p-4 border-t border-slate-200 bg-white shrink-0">
            <div className="flex items-end gap-2">
              <textarea
                value={input}
                rows={rows}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask the agent to build, fix, or upgrade…"
                aria-label="Message the agent (Enter to send, Shift+Enter for a new line)"
                className="input flex-1 resize-none"
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
