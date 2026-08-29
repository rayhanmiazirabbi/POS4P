'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { CashSession } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

import { pharmacyApi } from '@/lib/api';
import { decimalEntry } from '@/lib/numeric-input';

/**
 * The tab strip carries `backdrop-filter`, which makes it the containing block
 * for fixed-position descendants -- a dialog rendered inside it would fix to the
 * strip, not the viewport. Dialogs go through a portal to `document.body` so
 * they center on the page no matter where their trigger lives.
 */
function dialogLayer(node: ReactNode): ReactNode {
  return typeof document === 'undefined' ? node : createPortal(node, document.body);
}

/**
 * The till's shift: who opened the drawer, when, and what the ledger says is in it.
 *
 * Opening and closing are server actions by design -- the expected-cash figure
 * is summed from the payments ledger, which no terminal can compute alone, so
 * the honest offline answer is to wait, exactly as with returns and voids. The
 * live figures refresh with the page's queries; a drawer count only ever runs
 * against what the server has already taken.
 */
export function ShiftPanel({ onError }: { onError: (message: string | null) => void }): ReactNode {
  const queryClient = useQueryClient();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [dialog, setDialog] = useState<'open' | 'close' | null>(null);
  const offline = typeof navigator !== 'undefined' && !navigator.onLine;

  const sessionQuery = useQuery({
    queryKey: ['pos', 'cash-session'],
    queryFn: async () => (await pharmacyApi.cashSessions.current()).data,
    staleTime: 0,
  });

  function refresh(): void {
    void queryClient.invalidateQueries({ queryKey: ['pos', 'cash-session'] });
  }

  const session = sessionQuery.data ?? null;
  const expected = session === null ? null : (Number(session.openingCash) + Number(session.cashIn) - Number(session.cashOut)).toFixed(2);

  return (
    <>
      <button
        type="button"
        className={`cash-drawer-toggle${session !== null ? ' cash-drawer-toggle--open' : ''}`}
        onClick={() => setDetailsOpen(true)}
      >
        <span className="cash-drawer-dot" aria-hidden="true" />
        Cash drawer
        {expected !== null && <small>৳{expected}</small>}
      </button>
      {detailsOpen && dialogLayer(
        <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setDetailsOpen(false); }}>
          <section className="dialog-panel" role="dialog" aria-modal="true" aria-labelledby="shift-title" onKeyDown={(event) => { if (event.key === 'Escape') setDetailsOpen(false); }}>
            <header className="dialog-header">
              <div>
                <span className="eyebrow">Cash drawer</span>
                <h2 id="shift-title">{session === null ? 'Shift closed' : 'Shift open'}</h2>
              </div>
              <button type="button" className="icon-action" onClick={() => setDetailsOpen(false)} aria-label="Close cash drawer details">×</button>
            </header>
            <div style={{ display: 'grid', gap: spacing.md, paddingTop: spacing.md }}>
              {sessionQuery.isLoading && <p className="status-message status-message--muted">Checking the drawer…</p>}
              {sessionQuery.isError && (
                <p role="alert" className="status-message status-message--error">
                  Could not reach the server for the shift. {offline ? 'Offline.' : 'Retry in a moment.'}
                </p>
              )}
              {session === null && !sessionQuery.isLoading && !sessionQuery.isError && (
                <>
                  <p className="empty-copy">Open the drawer with its starting cash to start the shift.</p>
                  <button type="button" className="primary-action" disabled={offline} onClick={() => setDialog('open')}>
                    Open shift
                  </button>
                </>
              )}
              {session !== null && (
                <>
                  <ShiftFigures session={session} />
                  <button type="button" className="quiet-action" disabled={offline} onClick={() => setDialog('close')}>
                    Close shift and count the drawer
                  </button>
                </>
              )}
            </div>
          </section>
        </div>
      )}
      {dialog === 'open' && dialogLayer(
        <OpenShiftDialog
          busy={sessionQuery.isFetching}
          onClose={() => setDialog(null)}
          onDone={() => { setDialog(null); refresh(); }}
          onError={onError}
        />
      )}
      {dialog === 'close' && session !== null && dialogLayer(
        <CloseShiftDialog
          session={session}
          onClose={() => setDialog(null)}
          onDone={() => { setDialog(null); refresh(); }}
          onError={onError}
        />
      )}
    </>
  );
}

function ShiftFigures({ session }: { session: CashSession }): ReactNode {
  const expected = (Number(session.openingCash) + Number(session.cashIn) - Number(session.cashOut)).toFixed(2);
  return (
    <dl style={{ margin: 0, display: 'flex', flexDirection: 'column', gap: spacing.xs, fontSize: tokens.typography.sizes.sm }}>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <dt style={{ color: colors.muted }}>Opened by</dt>
        <dd style={{ margin: 0 }}>{session.openedByName} · {new Date(session.openedAt).toLocaleTimeString()}</dd>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <dt style={{ color: colors.muted }}>Opening float</dt>
        <dd style={{ margin: 0 }}>৳{session.openingCash}</dd>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <dt style={{ color: colors.muted }}>Cash in · out</dt>
        <dd style={{ margin: 0 }}>৳{session.cashIn} · ৳{session.cashOut}</dd>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: tokens.typography.weights.semibold }}>
        <dt>Expected in drawer</dt>
        <dd style={{ margin: 0 }}>৳{expected}</dd>
      </div>
    </dl>
  );
}

function OpenShiftDialog({
  busy,
  onClose,
  onDone,
  onError,
}: {
  busy: boolean;
  onClose: () => void;
  onDone: () => void;
  onError: (message: string | null) => void;
}): ReactNode {
  const [openingCash, setOpeningCash] = useState('');
  const [working, setWorking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function submit(): Promise<void> {
    if (openingCash.trim() === '') { setProblem('Count the drawer and enter what is in it.'); return; }
    setWorking(true); setProblem(null); onError(null);
    try {
      await pharmacyApi.cashSessions.open({ openingCash });
      onDone();
    } catch (cause) {
      setProblem(cause instanceof Error ? cause.message : 'Could not open the shift');
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="dialog-panel" role="dialog" aria-modal="true" aria-labelledby="open-shift-title" onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}>
        <header className="dialog-header">
          <div>
            <span className="eyebrow">Cash drawer</span>
            <h2 id="open-shift-title">Open shift</h2>
            <p style={{ margin: '4px 0 0', color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
              Count the bills in the drawer first; this becomes the float every close is measured against.
            </p>
          </div>
        </header>
        <label style={{ margin: `${spacing.md} 0 ${spacing.sm}`, display: 'flex', flexDirection: 'column', gap: spacing.xs, fontSize: tokens.typography.sizes.sm }}>
          Cash in drawer
          <span className="money-input">
            <span>৳</span>
            <input className="field" autoFocus inputMode="decimal" placeholder="0.00" value={openingCash} onChange={(event) => setOpeningCash(decimalEntry(event.target.value))} />
          </span>
        </label>
        {problem !== null && <p role="alert" className="form-error" style={{ margin: 0 }}>{problem}</p>}
        <footer style={{ display: 'flex', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.md }}>
          <button type="button" className="quiet-action" disabled={working || busy} onClick={onClose}>Cancel</button>
          <button type="button" className="primary-action" disabled={working} onClick={() => void submit()}>
            {working ? 'Opening…' : 'Open shift'}
          </button>
        </footer>
      </section>
    </div>
  );
}

function CloseShiftDialog({
  session,
  onClose,
  onDone,
  onError,
}: {
  session: CashSession;
  onClose: () => void;
  onDone: () => void;
  onError: (message: string | null) => void;
}): ReactNode {
  const [countedCash, setCountedCash] = useState('');
  const [note, setNote] = useState('');
  const [armed, setArmed] = useState(false);
  const [working, setWorking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const expected = (Number(session.openingCash) + Number(session.cashIn) - Number(session.cashOut)).toFixed(2);
  const counted = countedCash.trim() === '' ? null : Number(countedCash);
  const difference = counted === null ? null : (counted - Number(expected)).toFixed(2);

  async function submit(): Promise<void> {
    if (counted === null) { setProblem('Count the drawer before closing.'); return; }
    if (!armed) { setArmed(true); return; }
    setWorking(true); setProblem(null); onError(null);
    try {
      await pharmacyApi.cashSessions.close(session.id, {
        countedCash,
        ...(note.trim() === '' ? {} : { note: note.trim() }),
      });
      onDone();
    } catch (cause) {
      setArmed(false);
      setProblem(cause instanceof Error ? cause.message : 'Could not close the shift');
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="dialog-panel" role="dialog" aria-modal="true" aria-labelledby="close-shift-title" onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); setArmed(false); onClose(); } }}>
        <header className="dialog-header">
          <div>
            <span className="eyebrow">Cash drawer</span>
            <h2 id="close-shift-title">Close shift</h2>
            <p style={{ margin: '4px 0 0', color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
              Opened by {session.openedByName} at {new Date(session.openedAt).toLocaleTimeString()} · the figures recompute at close.
            </p>
          </div>
        </header>
        <dl style={{ margin: `${spacing.md} 0`, display: 'flex', flexDirection: 'column', gap: spacing.xs, fontSize: tokens.typography.sizes.sm }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}><dt style={{ color: colors.muted }}>Opening float</dt><dd style={{ margin: 0 }}>৳{session.openingCash}</dd></div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}><dt style={{ color: colors.muted }}>Cash in</dt><dd style={{ margin: 0 }}>৳{session.cashIn}</dd></div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}><dt style={{ color: colors.muted }}>Cash out (refunds)</dt><dd style={{ margin: 0 }}>৳{session.cashOut}</dd></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: tokens.typography.weights.semibold }}><dt>Expected in drawer</dt><dd style={{ margin: 0 }}>৳{expected}</dd></div>
        </dl>
        <label style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs, marginBottom: spacing.sm, fontSize: tokens.typography.sizes.sm }}>
          Counted cash
          <span className="money-input">
            <span>৳</span>
            <input className="field" autoFocus inputMode="decimal" placeholder="0.00" value={countedCash} onChange={(event) => { setCountedCash(decimalEntry(event.target.value)); setArmed(false); }} />
          </span>
        </label>
        {difference !== null && (
          <p style={{ margin: `0 0 ${spacing.sm}`, fontSize: tokens.typography.sizes.sm, color: Number(difference) === 0 ? colors.muted : (Number(difference) < 0 ? colors.danger : colors.warning) }}>
            {Number(difference) === 0
              ? 'The count matches the ledger exactly.'
              : `${Number(difference) > 0 ? 'Over' : 'Short'} by ৳${Math.abs(Number(difference)).toFixed(2)}`}
          </p>
        )}
        <label style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs, marginBottom: spacing.sm, fontSize: tokens.typography.sizes.sm }}>
          Note (optional)
          <input className="field" placeholder="Anything the next shift should know" maxLength={400} value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
        {problem !== null && <p role="alert" className="form-error" style={{ margin: 0 }}>{problem}</p>}
        <footer style={{ display: 'flex', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.md }}>
          <button type="button" className="quiet-action" disabled={working} onClick={onClose}>Cancel</button>
          <button type="button" className={armed ? 'quiet-action danger-action' : 'primary-action'} disabled={working} onClick={() => void submit()}>
            {working ? 'Closing…' : armed ? 'Confirm — close the shift' : 'Close shift'}
          </button>
        </footer>
      </section>
    </div>
  );
}
