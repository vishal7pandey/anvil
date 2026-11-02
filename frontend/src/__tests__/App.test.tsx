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

describe('App', () => {
  it('renders the header and a demo session', () => {
    render(<App />);
    expect(screen.getByText('Anvil')).toBeInTheDocument();
    expect(screen.getByText('RTEMS pthread_mutex_init defect fix')).toBeInTheDocument();
    expect(screen.getByText('Sessions')).toBeInTheDocument();
  });

  it('opens the new session dialog from the header button', () => {
    render(<App />);
    const newSessionBtns = screen.getAllByText('New Session');
    expect(newSessionBtns.length).toBeGreaterThan(0);
    fireEvent.click(newSessionBtns[0]);
    // Dialog heading should appear
    expect(screen.getByRole('heading', { name: 'New Session' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('e.g. Fix pthread_mutex_init defect')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('rtems')).toBeInTheDocument();
  });
});