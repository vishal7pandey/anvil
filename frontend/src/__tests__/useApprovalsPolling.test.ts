import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useApprovalsPolling } from '../useApprovalsPolling';

describe('useApprovalsPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('periodically calls onPoll every intervalMs', async () => {
    const onPoll = vi.fn().mockResolvedValue(undefined);
    renderHook(() => useApprovalsPolling(onPoll, 3000));

    expect(onPoll).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(onPoll).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(onPoll).toHaveBeenCalledTimes(2);
  });

  it('stops polling on unmount', async () => {
    const onPoll = vi.fn().mockResolvedValue(undefined);
    const { unmount } = renderHook(() => useApprovalsPolling(onPoll, 3000));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(onPoll).toHaveBeenCalledTimes(1);

    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(onPoll).toHaveBeenCalledTimes(1);
  });

  it('handles and swallows rejected promises without throwing', async () => {
    const onPoll = vi.fn().mockRejectedValue(new Error('Network error'));
    renderHook(() => useApprovalsPolling(onPoll, 3000));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(onPoll).toHaveBeenCalledTimes(1);
  });
});
