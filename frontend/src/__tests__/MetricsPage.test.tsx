import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getMetrics } from '../lib/api';
import type { Metrics } from '../types';
import MetricsPage from '../components/MetricsPage';

vi.mock('../lib/api', () => ({
  getMetrics: vi.fn(),
}));

const partialMetrics: Metrics = {
  from: null,
  to: null,
  scenario: null,
  run_count: 5,
  total_wall_time_seconds: 120,
  average_wall_time_seconds: 24,
  model_calls: 3,
  input_tokens: 100,
  output_tokens: 200,
  usd: 1.25,
  cost_usd: 1.25,
  cost_status: 'partial',
  runs_missing_cost: 2,
  human_touches: 1,
  ci_rounds: 2,
  outcomes: {},
};

describe('MetricsPage', () => {
  beforeEach(() => {
    vi.mocked(getMetrics).mockResolvedValue(partialMetrics);
  });

  it('shows the partial cost total and number of runs without cost data', async () => {
    render(<MetricsPage />);
    expect(await screen.findByText('$1.2500 (2 runs without cost data)')).toBeInTheDocument();
  });
});
