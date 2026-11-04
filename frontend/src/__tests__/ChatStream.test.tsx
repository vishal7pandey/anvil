import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import ChatStream from '../components/ChatStream';
import type { Run, Session, StreamEvent } from '../types';
import tc3Run from './fixtures/tc3-run-f9181d40.json';

const session = { id: 's1', repo: 'rtems-lwip', title: 'TC3' } as Session;
// jsdom has no layout, so no scrollIntoView.
Element.prototype.scrollIntoView = vi.fn();
afterEach(cleanup);

describe('ChatStream', () => {
  it('shows a run as a conversation: no event-type labels, no raw JSON in view', () => {
    const { container } = render(
      <ChatStream session={session} run={null} events={tc3Run as unknown as StreamEvent[]} onSendMessage={vi.fn()} />,
    );
    expect(screen.getByText('You')).toBeInTheDocument();
    expect(screen.getByText('Run failed')).toBeInTheDocument();
    expect(screen.getByText(/relation "kb_documents" does not exist/)).toBeInTheDocument();
    expect(screen.queryByText('tool.call')).not.toBeInTheDocument();
    expect(screen.queryByText('message.delta')).not.toBeInTheDocument();
    // Actions are grouped: a handful of groups, not one card per event.
    expect(screen.getAllByText(/\d+ actions?/).length).toBeLessThan(6);
    // Raw JSON and tool output exist only inside disclosures that start closed.
    const pres = Array.from(container.querySelectorAll('pre'));
    expect(pres.length).toBeGreaterThan(0);
    expect(pres.every((pre) => pre.closest('details:not([open])'))).toBe(true);
  });

  it('shows the approval a person must decide, with its arguments', () => {
    const events: StreamEvent[] = [
      { run_id: 'r', seq: 1, ts: 't', type: 'approval.request', agent: 'main',
        data: { approval_id: 'a1', action: 'jira_create', reason: 'Remediation story for a gap', args: { summary: 'Disable telnet by default' } } },
    ];
    render(<ChatStream session={session} run={null} events={events} onSendMessage={vi.fn()} />);
    expect(screen.getByText('jira_create')).toBeInTheDocument();
    expect(screen.getByText('Remediation story for a gap')).toBeInTheDocument();
    expect(screen.getByText('Disable telnet by default')).toBeInTheDocument();
  });

  it('sends on Enter and keeps Shift+Enter as a new line', async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    render(<ChatStream session={session} run={null} events={[]} onSendMessage={send} />);
    const box = screen.getByPlaceholderText('Ask the agent to build, fix, or upgrade…');
    fireEvent.change(box, { target: { value: 'line one' } });
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
    expect(send).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: 'Enter' });
    await vi.waitFor(() => expect(send).toHaveBeenCalledWith('line one'));
  });
});

describe('ChatStream while a run is active', () => {
  it('disables sending and says why (one active run per session, ANVIL-71)', () => {
    const running = { id: 'r', status: 'running' } as Run;
    const { rerender } = render(<ChatStream session={session} run={running} events={[]} onSendMessage={vi.fn()} />);
    expect(screen.getByPlaceholderText('A run is in progress: wait for it or cancel it')).toBeDisabled();
    rerender(<ChatStream session={session} run={{ ...running, status: 'waiting_approval' }} events={[]} onSendMessage={vi.fn()} />);
    expect(screen.getByPlaceholderText('Waiting for your approval: decide it in Approvals')).toBeDisabled();
    rerender(<ChatStream session={session} run={{ ...running, status: 'succeeded' }} events={[]} onSendMessage={vi.fn()} />);
    expect(screen.getByPlaceholderText('Ask the agent to build, fix, or upgrade…')).not.toBeDisabled();
  });
});
