import { useEffect, useRef } from 'react';

/**
 * Periodically polls for approval updates while the component is mounted.
 *
 * @param onPoll Callback invoked periodically to refresh approvals (e.g. refreshApprovals).
 * @param intervalMs Polling interval in milliseconds (default: 3000ms).
 */
export function useApprovalsPolling(
  onPoll: () => Promise<void> | void,
  intervalMs = 3000,
): void {
  const onPollRef = useRef(onPoll);
  onPollRef.current = onPoll;

  useEffect(() => {
    let active = true;

    const tick = () => {
      if (active) {
        Promise.resolve(onPollRef.current()).catch(() => {
          // Polling errors are swallowed or handled by the callback itself.
        });
      }
    };

    const intervalId = setInterval(tick, intervalMs);

    return () => {
      active = false;
      clearInterval(intervalId);
    };
  }, [intervalMs]);
}

export default useApprovalsPolling;
