import { dueForUpload, type OutboxEntry } from '@pharmacy/sync';

/**
 * Whether the outbox engine has anything it would send right now.
 *
 * The decision is delegated to `dueForUpload` rather than derived from the
 * queue summary, because `nextRetryAt` alone cannot tell a sale waiting on
 * backoff from one that was never tried: keying off the summary would strand a
 * fresh sale behind another line's retry clock. Pending entries are always
 * due; failed entries only once their scheduled `nextAttemptAt` has passed --
 * which is how the engine's backoff is honoured instead of re-implemented.
 */
export function hasDueEntries<T>(entries: readonly OutboxEntry<T>[], nowUtcIso: string): boolean {
  return dueForUpload(entries, nowUtcIso).length > 0;
}

export type GateOutcome<T> = { ran: true; value: T } | { ran: false };

export type FlushGate = {
  /** True while a gated task is mid-flight. */
  busy(): boolean;
  /**
   * Run `task` unless another gated task is still running, in which case the
   * trigger is skipped and `{ ran: false }` answered immediately.
   */
  run<T>(task: () => Promise<T>): Promise<GateOutcome<T>>;
};

/**
 * Single-flight guard shared by every flush trigger.
 *
 * `flushOutbox` serializes its own writes, but two concurrent loops would each
 * claim and post the same events -- wasted requests that cost each entry an
 * extra backoff step. The interval timer, the foreground listener and the
 * manual button all pass through one gate, so overlapping triggers collapse
 * into whichever attempt is already running. A rejected task releases the gate:
 * one failed upload must not wedge automatic syncing for the rest of the session.
 */
export function createFlushGate(): FlushGate {
  let inFlight = false;
  return {
    busy: () => inFlight,
    async run<T>(task: () => Promise<T>): Promise<GateOutcome<T>> {
      if (inFlight) return { ran: false };
      inFlight = true;
      try {
        return { ran: true, value: await task() };
      } finally {
        inFlight = false;
      }
    },
  };
}
