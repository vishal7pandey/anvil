import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import App from '../App';

beforeEach(() => {
  vi.spyOn(globalThis, 'fetch');
  if (!(Element.prototype as any).scrollIntoView) {
    (Element.prototype as any).scrollIntoView = vi.fn();
  }
  vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

const session = {
  id: '6f0c1a52-0000-4000-8000-000000000001',
  repo: 'vishal7pandey/rtems',
  environment: 'local',
  mode: 'autonomous',
  merge_policy: 'human',
  model_profile: 'anvil-main',
  title: 'RTEMS-1 mutex defect',
  created_at: '2026-11-03T08:00:00Z',
  updated_at: '2026-11-03T08:00:00Z',
};

function mockSessions(list: unknown[]) {
  (globalThis.fetch as ReturnType<typeof vi.spyOn>).mockResolvedValueOnce({
    ok: true,
    json: async () => list,
  } as Response);
}

describe('App', () => {
  it('shows sessions loaded from the API, never a made-up one', async () => {
    mockSessions([session]);
    render(<App />);
    expect(screen.getByText('Anvil')).toBeInTheDocument();
    expect(await screen.findByText('RTEMS-1 mutex defect')).toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/sessions', expect.anything());
    expect(screen.queryByText('RTEMS pthread_mutex_init defect fix')).not.toBeInTheDocument();
  });

  it('with no sessions, offers New Session and the dialog opens', async () => {
    mockSessions([]);
    render(<App />);
    expect(await screen.findByText('No sessions yet.')).toBeInTheDocument();
    fireEvent.click(screen.getAllByText('New Session')[0]);
    expect(screen.getByRole('heading', { name: 'New Session' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('e.g. Fix pthread_mutex_init defect')).toBeInTheDocument();
  });

  it('shows the error when sessions cannot be loaded', async () => {
    (globalThis.fetch as ReturnType<typeof vi.spyOn>).mockResolvedValueOnce({
      ok: false,
      statusText: 'Unauthorized',
    } as Response);
    render(<App />);
    expect(await screen.findByText('Could not load sessions: Failed to list sessions: Unauthorized')).toBeInTheDocument();
  });
});
