import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import EventBubble from '../components/EventBubble';

describe('EventBubble', () => {
  it('renders a run.status event with icon and type label', () => {
    render(
      <EventBubble
        event={{
          run_id: 'r1',
          seq: 1,
          ts: '2026-11-02T20:00:00Z',
          type: 'run.status',
          agent: 'claude',
          data: { status: 'running' },
        }}
      />
    );
    expect(screen.getByText('🔄')).toBeInTheDocument();
    expect(screen.getByText('run.status')).toBeInTheDocument();
    expect(screen.getByText('claude')).toBeInTheDocument();
  });

  it('renders a message event', () => {
    render(
      <EventBubble
        event={{
          run_id: 'r1',
          seq: 2,
          ts: '2026-11-02T20:00:01Z',
          type: 'message',
          agent: 'cline',
          data: { role: 'assistant', text: 'Starting the fix' },
        }}
      />
    );
    expect(screen.getByText('💬')).toBeInTheDocument();
    expect(screen.getByText('message')).toBeInTheDocument();
  });

  it('renders an approval.request event', () => {
    render(
      <EventBubble
        event={{
          run_id: 'r1',
          seq: 3,
          ts: '2026-11-02T20:00:02Z',
          type: 'approval.request',
          agent: 'runtime',
          data: { approval_id: 'a1', action: 'github_open_pr', reason: 'Ready for review', args: {} },
        }}
      />
    );
    expect(screen.getByText('⏳')).toBeInTheDocument();
    expect(screen.getByText('approval.request')).toBeInTheDocument();
  });
});