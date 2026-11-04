/**
 * The chat stream's render model (ANVIL-52): raw run events in, the items a person reads out.
 *
 * The backend emits one event per token delta, one per usage report, and three per shell command
 * (tool.call, tool.result, sandbox.exec). Rendering those one-to-one turned a run into a JSON
 * firehose. This pure function folds them:
 *
 * - consecutive `message.delta` from one agent become one assistant message; the final `message`
 *   replaces the streamed text;
 * - `tool.call` + its `tool.result` (by call_id) + the `sandbox.exec` that follows become one action;
 * - consecutive actions become one activity group;
 * - telemetry (`usage`, `run.record`, `plan`, `diff`, routine `run.status`) is not in the stream:
 *   the right panel shows it.
 *
 * Every item keeps its source events, so the view can offer them behind a "raw" disclosure.
 */
import type { StreamEvent } from '../types';

export type ActionStatus = 'running' | 'ok' | 'failed';

export interface Action {
  callId: string;
  name: string;
  summary: string;
  status: ActionStatus;
  exitCode: number | null;
  output: string;
  events: StreamEvent[];
}

export type ChatItem =
  | { kind: 'user'; key: string; text: string; events: StreamEvent[] }
  | { kind: 'assistant'; key: string; agent: string; text: string; streaming: boolean; events: StreamEvent[] }
  | { kind: 'activity'; key: string; actions: Action[] }
  | { kind: 'approval'; key: string; action: string; reason: string; args: Record<string, unknown>; events: StreamEvent[] }
  | { kind: 'subagent'; key: string; name: string; phase: string; summary: string; events: StreamEvent[] }
  | { kind: 'artifact'; key: string; title: string; url: string | null; artifactKind: string; events: StreamEvent[] }
  | { kind: 'status'; key: string; status: string; events: StreamEvent[] }
  | { kind: 'note'; key: string; text: string; events: StreamEvent[] }
  | { kind: 'error'; key: string; text: string; events: StreamEvent[] };

/** Run states worth a line in the stream; queued/running are shown by the "working" indicator. */
const NOTABLE_STATUS = new Set(['waiting_approval', 'handoff', 'succeeded', 'failed', 'cancelled']);
const HIDDEN = new Set(['usage', 'run.record', 'plan', 'diff']);

function str(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value);
}

function oneLine(text: string, max = 140): string {
  const line = text.replace(/\s+/g, ' ').trim();
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** What a tool call did, in one line: the command, path or query, not its JSON. */
export function summarizeCall(name: string, args: Record<string, unknown>): string {
  const pick = (...keys: string[]) => keys.map((k) => args[k]).find((v) => typeof v === 'string' && v) as string | undefined;
  switch (name) {
    case 'execute':
      return oneLine(pick('command') ?? '');
    case 'write_todos':
      return 'updated the plan';
    case 'task':
      return oneLine(pick('description') ?? pick('subagent_type') ?? '');
    default: {
      const target = pick('file_path', 'query', 'pattern', 'path', 'key', 'issue_key', 'branch', 'title', 'url');
      if (target) return oneLine(target);
      if (typeof args.pr === 'number') return `PR #${args.pr}`;
      const first = Object.values(args).find((v) => typeof v === 'string' && v);
      return first ? oneLine(first as string) : '';
    }
  }
}

function lastItem(items: ChatItem[]): ChatItem | undefined {
  return items[items.length - 1];
}

function findAction(items: ChatItem[], callId: string): Action | undefined {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind === 'activity') {
      const hit = item.actions.find((a) => a.callId === callId);
      if (hit) return hit;
    }
  }
  return undefined;
}

/** A run that ended (or crashed) left these calls unanswered: they did not succeed. */
function closeRunning(items: ChatItem[]): void {
  for (const item of items) {
    if (item.kind !== 'activity') continue;
    for (const action of item.actions) if (action.status === 'running') action.status = 'failed';
  }
}

const ENDED = new Set(['handoff', 'succeeded', 'failed', 'cancelled']);

export function toChatItems(events: StreamEvent[]): ChatItem[] {
  const items: ChatItem[] = [];
  let lastExecute: Action | undefined;

  for (const event of events) {
    const data = event.data ?? {};
    const key = `${event.type}-${event.seq}`;
    const agent = event.agent ?? 'main';

    if (HIDDEN.has(event.type)) continue;

    switch (event.type) {
      case 'message.delta': {
        const tail = lastItem(items);
        if (tail?.kind === 'assistant' && tail.streaming && tail.agent === agent) {
          tail.text += str(data.text);
          tail.events.push(event);
        } else {
          items.push({ kind: 'assistant', key, agent, text: str(data.text), streaming: true, events: [event] });
        }
        break;
      }
      case 'message': {
        const role = str(data.role);
        const text = str(data.text ?? data.content);
        if (role === 'user') {
          items.push({ kind: 'user', key, text, events: [event] });
        } else if (role === 'system') {
          closeRunning(items);
          items.push({ kind: 'error', key, text, events: [event] });
        } else {
          const tail = lastItem(items);
          if (tail?.kind === 'assistant' && tail.streaming && tail.agent === agent) {
            // The completed message is authoritative; the deltas were its preview.
            tail.text = text;
            tail.streaming = false;
            tail.events.push(event);
          } else {
            items.push({ kind: 'assistant', key, agent, text, streaming: false, events: [event] });
          }
        }
        break;
      }
      case 'tool.call': {
        const name = str(data.name);
        const args = (data.args as Record<string, unknown>) ?? {};
        const action: Action = {
          callId: str(data.call_id) || key,
          name,
          summary: summarizeCall(name, args),
          status: 'running',
          exitCode: null,
          output: '',
          events: [event],
        };
        const tail = lastItem(items);
        if (tail?.kind === 'activity') tail.actions.push(action);
        else items.push({ kind: 'activity', key, actions: [action] });
        if (name === 'execute') lastExecute = action;
        break;
      }
      case 'tool.result': {
        const action = findAction(items, str(data.call_id));
        if (!action) break; // a result without its call (a replay that started mid-run): nothing to attach to
        action.status = data.ok === false ? 'failed' : 'ok';
        action.output = str(data.output);
        action.events.push(event);
        break;
      }
      case 'sandbox.exec': {
        // Emitted right after the execute call's tool.result, without a call_id.
        const action = lastExecute;
        if (!action) break;
        const code = typeof data.exit_code === 'number' ? data.exit_code : null;
        action.exitCode = code;
        if (code !== null && code !== 0) action.status = 'failed';
        action.events.push(event);
        lastExecute = undefined;
        break;
      }
      case 'approval.request':
        items.push({
          kind: 'approval',
          key,
          action: str(data.action),
          reason: str(data.reason),
          args: (data.args as Record<string, unknown>) ?? {},
          events: [event],
        });
        break;
      case 'subagent':
        items.push({ kind: 'subagent', key, name: str(data.name), phase: str(data.phase), summary: str(data.summary), events: [event] });
        break;
      case 'artifact':
        items.push({
          kind: 'artifact',
          key,
          title: str(data.title) || str(data.kind),
          url: typeof data.url === 'string' ? data.url : null,
          artifactKind: str(data.kind),
          events: [event],
        });
        break;
      case 'context.compacted': {
        // ANVIL-93: the agent's older messages were summarized to stay inside its context window.
        const n = typeof data.cutoff_index === 'number' ? data.cutoff_index : 0;
        items.push({
          kind: 'note',
          key,
          text: n > 0 ? `Context compacted: ${n} earlier messages summarized` : 'Context compacted',
          events: [event],
        });
        break;
      }
      case 'run.status': {
        const status = str(data.status);
        if (ENDED.has(status)) closeRunning(items);
        if (!NOTABLE_STATUS.has(status)) break;
        const tail = lastItem(items);
        if (tail?.kind === 'status' && tail.status === status) break;
        items.push({ kind: 'status', key, status, events: [event] });
        break;
      }
      default:
        break;
    }
  }
  return items;
}

/** The events behind an item, for the raw disclosure. */
export function itemEvents(item: ChatItem): StreamEvent[] {
  return item.kind === 'activity' ? item.actions.flatMap((a) => a.events) : item.events;
}
