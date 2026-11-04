import { describe, it, expect } from 'vitest';
import { toChatItems, summarizeCall, itemEvents, type ChatItem } from '../chat/chatItems';
import type { StreamEvent } from '../types';
import tc3Run from './fixtures/tc3-run-f9181d40.json';

let seq = 0;
function ev(type: string, data: Record<string, unknown>, agent: string | null = 'main'): StreamEvent {
  seq += 1;
  return { run_id: 'r1', seq, ts: '2026-11-04T12:00:00Z', type: type as StreamEvent['type'], agent, data };
}

describe('toChatItems', () => {
  it('folds token deltas into one assistant message and lets the final message win', () => {
    const items = toChatItems([
      ev('message.delta', { role: 'assistant', text: 'Rea' }),
      ev('message.delta', { role: 'assistant', text: 'ding the ' }),
      ev('message.delta', { role: 'assistant', text: 'checklist' }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: 'assistant', text: 'Reading the checklist', streaming: true });

    const done = toChatItems([
      ev('message.delta', { role: 'assistant', text: 'Reading' }),
      ev('message', { role: 'assistant', text: 'Reading the checklist.' }),
    ]);
    expect(done).toHaveLength(1);
    expect(done[0]).toMatchObject({ kind: 'assistant', text: 'Reading the checklist.', streaming: false });
  });

  it('keeps deltas from different agents apart', () => {
    const items = toChatItems([
      ev('message.delta', { text: 'a' }, 'main'),
      ev('message.delta', { text: 'b' }, 'subagent'),
    ]);
    expect(items.map((i) => i.kind)).toEqual(['assistant', 'assistant']);
  });

  it('pairs tool.call, tool.result and sandbox.exec into one action, and groups consecutive actions', () => {
    const items = toChatItems([
      ev('tool.call', { call_id: 'c1', name: 'read_file', args: { file_path: '/workspace/x/annex1.yaml' } }),
      ev('tool.call', { call_id: 'c2', name: 'execute', args: { command: 'syft scan dir:.' } }),
      ev('tool.result', { call_id: 'c1', name: 'read_file', ok: true, output: 'yaml...' }),
      ev('tool.result', { call_id: 'c2', name: 'execute', ok: true, output: 'boom' }),
      ev('sandbox.exec', { cmd: 'syft scan dir:.', exit_code: 2, output_tail: 'boom' }),
    ]);
    expect(items).toHaveLength(1);
    const group = items[0] as Extract<ChatItem, { kind: 'activity' }>;
    expect(group.actions.map((a) => [a.name, a.summary, a.status, a.exitCode])).toEqual([
      ['read_file', '/workspace/x/annex1.yaml', 'ok', null],
      ['execute', 'syft scan dir:.', 'failed', 2],
    ]);
    expect(itemEvents(group)).toHaveLength(5);
  });

  it('marks a failed tool result and leaves an unanswered call running', () => {
    const items = toChatItems([
      ev('tool.call', { call_id: 'c1', name: 'kb_search', args: { query: 'telnetd' } }),
      ev('tool.result', { call_id: 'c1', ok: false, output: 'knowledge base unavailable' }),
      ev('tool.call', { call_id: 'c2', name: 'jira_get', args: { key: 'RTEMS-3' } }),
    ]);
    const group = items[0] as Extract<ChatItem, { kind: 'activity' }>;
    expect(group.actions.map((a) => a.status)).toEqual(['failed', 'running']);
  });

  it('hides telemetry and routine statuses, keeps what needs a person', () => {
    const items = toChatItems([
      ev('run.status', { status: 'running' }),
      ev('run.record', { mode: 'autonomous' }),
      ev('usage', { input_tokens: 10, output_tokens: 2 }),
      ev('plan', { todos: [] }),
      ev('approval.request', { approval_id: 'a1', action: 'jira_create', reason: 'gap story', args: { summary: 's' } }),
      ev('run.status', { status: 'waiting_approval' }),
      ev('run.status', { status: 'waiting_approval' }),
      ev('message', { role: 'system', text: 'Run failed: boom' }),
      ev('run.status', { status: 'failed' }),
    ]);
    expect(items.map((i) => i.kind)).toEqual(['approval', 'status', 'error', 'status']);
  });

  it('turns a real 80-event run into a short conversation (TC3 run f9181d40)', () => {
    const events = tc3Run as unknown as StreamEvent[];
    const items = toChatItems(events);
    expect(events).toHaveLength(80);
    expect(items.length).toBeLessThan(15);
    expect(items[0].kind).toBe('user');
    expect(items.at(-1)).toMatchObject({ kind: 'status', status: 'failed' });
    expect(items.some((i) => i.kind === 'error' && i.text.includes('kb_documents'))).toBe(true);
    // The run crashed inside kb_search: that call did not succeed and is not "still running".
    const actions = items.flatMap((i) => (i.kind === 'activity' ? i.actions : []));
    expect(actions.at(-1)).toMatchObject({ name: 'kb_search', status: 'failed' });
    expect(actions.some((a) => a.status === 'running')).toBe(false);
    // Every event that carries meaning is still reachable from some item.
    const shown = new Set(items.flatMap(itemEvents).map((e) => e.seq));
    const meaningful = events.filter((e) => ['tool.call', 'tool.result', 'sandbox.exec', 'message'].includes(e.type));
    expect(meaningful.every((e) => shown.has(e.seq))).toBe(true);
  });
});

describe('summarizeCall', () => {
  it('says what a call did in one line', () => {
    expect(summarizeCall('execute', { command: 'cd /w &&\n  ./waf build' })).toBe('cd /w && ./waf build');
    expect(summarizeCall('grep', { pattern: 'telnet', path: '/w' })).toBe('telnet');
    expect(summarizeCall('kb_search', { query: 'telnetd auth', k: 5 })).toBe('telnetd auth');
    expect(summarizeCall('github_checks', { pr: 7 })).toBe('PR #7');
    expect(summarizeCall('write_todos', { todos: [] })).toBe('updated the plan');
    expect(summarizeCall('github_code_scanning_alerts', {})).toBe('');
  });
});
