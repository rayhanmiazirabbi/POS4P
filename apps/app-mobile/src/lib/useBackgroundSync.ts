import { useCallback, useEffect, useMemo, useRef } from 'react';
import { AppState } from 'react-native';

import { pharmacyApi } from './api';
import { createFlushGate } from './backgroundSync';
import { flushQueue, hasDueUploads, type FlushResult, type Ingest } from './offlineSales';

const FLUSH_INTERVAL_MS = 30_000;

const ingest: Ingest = async (events) => (await pharmacyApi.sync.ingest(events)).data.acks;

/**
 * Automatic upload triggers for the counter.
 *
 * Two triggers -- a slow interval and the app returning to the foreground --
 * funnel through one decision: read the queue and flush only when the outbox
 * engine says something is actually due (`hasDueUploads`), so backoff is never
 * bypassed by a background tick. All attempts share one gate, so a timer tick
 * landing during the manual upload is skipped rather than doubled. `onSettled`
 * fires after every attempt -- ran or skipped -- which is what keeps the queue
 * badge on screen live instead of static.
 */
export function useBackgroundSync(onSettled: () => void): { flushNow: () => Promise<FlushResult | null> } {
  const gate = useMemo(() => createFlushGate(), []);
  const settledRef = useRef(onSettled);
  settledRef.current = onSettled;

  const flushNow = useCallback(async (): Promise<FlushResult | null> => {
    try {
      const outcome = await gate.run(() => flushQueue(ingest));
      return outcome.ran ? outcome.value : null;
    } finally {
      settledRef.current();
    }
  }, [gate]);

  useEffect(() => {
    async function attemptIfDue(): Promise<void> {
      try {
        // Ask the engine first: nothing due, no request. An unreadable queue
        // stays the sync screen's problem rather than an hourly toast here.
        if (!(await hasDueUploads())) return;
      } catch {
        return;
      }
      await flushNow();
    }

    void attemptIfDue();
    const timer = setInterval(() => void attemptIfDue(), FLUSH_INTERVAL_MS);
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') void attemptIfDue();
    });
    return () => {
      clearInterval(timer);
      subscription.remove();
    };
  }, [flushNow]);

  return { flushNow };
}
