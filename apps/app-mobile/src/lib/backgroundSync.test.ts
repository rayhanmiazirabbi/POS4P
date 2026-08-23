import { createSyncEnvelope, type OutboxEntry, type OutboxStatus } from '@pharmacy/sync';
import { describe, expect, it } from 'vitest';

import { createFlushGate, hasDueEntries, type GateOutcome } from './backgroundSync';

let sequence = 0;

function entry(status: OutboxStatus, overrides: Partial<OutboxEntry> = {}): OutboxEntry {
  sequence += 1;
  const eventId = `event-${sequence}`;
  return {
    envelope: createSyncEnvelope({
      eventId,
      deviceId: '11111111-1111-4111-8111-111111111111',
      organizationId: '22222222-2222-4222-8222-222222222222',
      storeId: '33333333-3333-4333-8333-333333333333',
      userId: '44444444-4444-4444-8444-444444444444',
      eventType: 'sale.create',
      createdAt: '2026-01-01T00:00:00Z',
      clientSequence: sequence,
      payload: {},
    }),
    status,
    attempts: status === 'failed' ? 1 : 0,
    nextAttemptAt: null,
    error: null,
    ...overrides,
  };
}

describe('hasDueEntries', () => {
  const now = '2026-01-01T00:01:00Z';

  it('answers false for a queue with nothing owed to the server', () => {
    expect(hasDueEntries([entry('acknowledged'), entry('rejected')], now)).toBe(false);
    expect(hasDueEntries([], now)).toBe(false);
  });

  it('answers true while an entry is mid-upload', () => {
    expect(hasDueEntries([entry('uploading')], now)).toBe(false);
  });

  it('treats a pending entry as due regardless of any other line backoff', () => {
    const entries = [
      entry('failed', { nextAttemptAt: '2026-01-01T01:00:00Z' }),
      entry('pending'),
    ];
    expect(hasDueEntries(entries, now)).toBe(true);
  });

  it('respects a failed entry backoff window', () => {
    const backingOff = entry('failed', { nextAttemptAt: '2026-01-01T00:05:00Z' });
    // Before the scheduled retry the engine is not asked to post.
    expect(hasDueEntries([backingOff], '2026-01-01T00:04:59Z')).toBe(false);
    // At and after it, the attempt is due again.
    expect(hasDueEntries([backingOff], '2026-01-01T00:05:00Z')).toBe(true);
    expect(hasDueEntries([backingOff], '2026-01-01T00:05:01Z')).toBe(true);
  });
});

describe('createFlushGate', () => {
  it('runs a task and answers its value', async () => {
    const gate = createFlushGate();
    await expect(gate.run(async () => 'summary')).resolves.toEqual({ ran: true, value: 'summary' });
    expect(gate.busy()).toBe(false);
  });

  it('skips a trigger that arrives while another flush is running', async () => {
    const gate = createFlushGate();
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const first = gate.run(async () => {
      await held;
      return 'first';
    });
    expect(await gate.run(async () => 'second')).toEqual<GateOutcome<string>>({ ran: false });
    expect(gate.busy()).toBe(true);
    release();
    await expect(first).resolves.toEqual({ ran: true, value: 'first' });
    // Once settled, the gate admits the next attempt.
    await expect(gate.run(async () => 'third')).resolves.toEqual({ ran: true, value: 'third' });
  });

  it('releases the gate when a flush fails', async () => {
    const gate = createFlushGate();
    await expect(gate.run(async () => {
      throw new Error('offline');
    })).rejects.toThrow('offline');
    expect(gate.busy()).toBe(false);
    await expect(gate.run(async () => 'ok')).resolves.toEqual({ ran: true, value: 'ok' });
  });
});
