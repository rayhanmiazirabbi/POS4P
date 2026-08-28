'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { Customer } from '@pharmacy/api';
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';

import { pharmacyApi } from '@/lib/api';

/** What the draft and the sale need from a picked customer: an id and a display label. */
export type CustomerPick = { id: string; label: string };

/** The digit shapes `normalize_phone` on the server canonicalises to +8801XXXXXXXXX. */
const BD_MOBILE = /^(?:\+?880|0)?1[3-9]\d{8}$/;

/**
 * The cart's customer field as one combobox: typing searches existing customers,
 * and the same typed text can be kept as a new customer instead.
 *
 * Enter takes the first row -- the best existing match when there is one, the
 * "add as new" row when there is not -- so a cashier never has to reach for the
 * mouse mid-sale. Arrow keys walk the rows, Escape returns to the input.
 *
 * With a customer attached, their recent purchases from this branch fold out
 * under the chip: "what did you buy last time" is the question the counter
 * actually asks, and it should not cost a walk to the reports page.
 */
export function CustomerCombobox({
  selectedId,
  selectedLabel,
  onSelect,
  onClear,
  onError,
}: {
  selectedId: string | null;
  selectedLabel: string | null;
  onSelect: (pick: CustomerPick) => void;
  onClear: () => void;
  onError: (message: string) => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [creating, setCreating] = useState(false);
  const [pendingSubmit, setPendingSubmit] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 220);
    return () => window.clearTimeout(timer);
  }, [query]);

  const online = typeof navigator === 'undefined' || navigator.onLine;
  const searchQuery = useQuery({
    queryKey: ['pos', 'customer-suggest', debounced],
    enabled: debounced !== '' && online,
    queryFn: async () => await pharmacyApi.customers.search({ q: debounced }, { limit: 6 }),
    staleTime: 15_000,
  });
  const matches = searchQuery.data?.items ?? [];
  // This branch's sales to the attached customer, newest first -- the same
  // staff-scoped list the counter already sells against, not the owner-only
  // cross-store history, so a cashier sees exactly what they may act on.
  const historyQuery = useQuery({
    queryKey: ['pos', 'customer-history', selectedId],
    enabled: selectedId !== null && online,
    staleTime: 30_000,
    queryFn: async () => (await pharmacyApi.sales.list({ customerId: selectedId as string }, { limit: 8 })).items,
  });
  // The rows a keyboard walks: every match, then the new-customer row last, so the
  // list always has one way forward even when nothing matched.
  const rowCount = matches.length + 1;
  const newRowRefIndex = matches.length;

  useEffect(() => {
    if (!pendingSubmit || searchQuery.isFetching || debounced === '' || debounced !== query.trim()) return;
    setPendingSubmit(false);
    const first = matches[0];
    if (first) choose(first);
    else void addNew(query.trim());
  }, [pendingSubmit, searchQuery.isFetching, debounced, query, matches]);

  function label(customer: Customer): string {
    return `${customer.name}${customer.normalizedPhone ? ` · ${customer.normalizedPhone}` : ''}`;
  }

  function choose(customer: Customer): void {
    setPendingSubmit(false);
    setQuery('');
    onSelect({ id: customer.id, label: label(customer) });
  }

  /**
   * Keep the typed text as a new customer and attach it to the sale in one step.
   *
   * The term doubles as the phone when it is shaped like a BD mobile in any
   * dialing form; anything else is a name, because the server rejects a phone it
   * cannot canonicalise rather than storing a bad one.
   */
  async function addNew(term: string): Promise<void> {
    if (term.trim() === '') return;
    if (!online) {
      onError('Adding a new customer needs a connection. Pick an existing one, or finish the sale without a customer.');
      return;
    }
    const phone = BD_MOBILE.test(term.replace(/\D/g, '')) ? term.trim() : undefined;
    setCreating(true);
    try {
      const response = await pharmacyApi.customers.create({ name: term.trim(), ...(phone ? { normalizedPhone: phone } : {}) });
      setPendingSubmit(false);
      setQuery('');
      onSelect({ id: response.data.id, label: label(response.data) });
      void queryClient.invalidateQueries({ queryKey: ['pos', 'customer-suggest'] });
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : 'Could not add that customer');
    } finally {
      setCreating(false);
    }
  }

  /**
   * Enter never acts on a stale list. If the search for what is on screen has not
   * landed yet, it waits for it, so "add as new" cannot fire while an exact match
   * is still on its way back.
   */
  function submit(): void {
    if (debounced === query.trim() && !searchQuery.isFetching) {
      const first = matches[0];
      if (first) choose(first);
      else void addNew(query.trim());
      return;
    }
    setDebounced(query.trim());
    setPendingSubmit(true);
  }

  function rowKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number): void {
    if (event.key === 'ArrowDown') { event.preventDefault(); rowRefs.current[(index + 1) % rowCount]?.focus(); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); index === 0 ? inputRef.current?.focus() : rowRefs.current[index - 1]?.focus(); }
    else if (event.key === 'Escape') { event.preventDefault(); inputRef.current?.focus(); }
  }

  const term = query.trim();

  return (
    <div className="customer-combobox">
      <div className="customer-lookup">
        <span aria-hidden="true"><PosUserIcon /></span>
        <input
          ref={inputRef}
          placeholder="Customer name or phone"
          aria-label="Customer name or phone"
          aria-expanded={term !== ''}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') { event.preventDefault(); submit(); }
            else if (event.key === 'ArrowDown' && term !== '') { event.preventDefault(); rowRefs.current[0]?.focus(); }
            else if (event.key === 'Escape') { setPendingSubmit(false); setQuery(''); }
          }}
        />
        <button type="button" aria-label="Find customer" disabled={term === ''} onClick={() => inputRef.current?.select()}><PosSearchIcon /></button>
        {selectedLabel !== null && (
          <button type="button" className="selected-customer" onClick={onClear}>
            {selectedLabel} <span aria-hidden="true">×</span>
          </button>
        )}
      </div>
      {selectedId !== null && term === '' && (
        <div className="customer-history">
          <button
            type="button"
            className="customer-history-toggle"
            aria-expanded={historyOpen}
            onClick={() => { setHistoryOpen((open) => !open); if (!historyOpen) void historyQuery.refetch(); }}
          >
            Recent purchases{historyQuery.data !== undefined ? ` (${historyQuery.data.length})` : ''}
            <span aria-hidden="true">{historyOpen ? '▴' : '▾'}</span>
          </button>
          {historyOpen && (
            <div className="customer-history-list">
              {historyQuery.isLoading && <p className="finder-note" role="status">Loading purchases…</p>}
              {historyQuery.isError && <p className="finder-note" role="alert">Could not load purchases. Retry in a moment.</p>}
              {!online && <p className="finder-note" role="alert">Connect to see this customer&rsquo;s purchases.</p>}
              {online && !historyQuery.isLoading && !historyQuery.isError && (historyQuery.data ?? []).length === 0 && (
                <p className="finder-note">No purchases at this branch yet.</p>
              )}
              {(historyQuery.data ?? []).map((sale) => (
                <div key={sale.id} className="customer-history-row">
                  <span className="customer-history-head">
                    <strong>{sale.receiptNumber ?? 'Sale'}</strong>
                    <small>{new Date(sale.createdAt).toLocaleDateString()} · ৳{sale.total}{sale.status !== 'completed' ? ` · ${sale.status}` : ''}</small>
                  </span>
                  <small className="customer-history-items">
                    {sale.items.map((item) => `${Number(item.quantity) % 1 === 0 ? Number(item.quantity) : item.quantity}× ${item.productName}`).join(', ')}
                  </small>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {term !== '' && (
        <div className="customer-suggest" role="listbox" aria-label="Customer matches">
          {matches.map((match, index) => (
            <button
              key={match.id}
              ref={(node) => { rowRefs.current[index] = node; }}
              type="button"
              role="option"
              className="customer-suggest-row"
              onClick={() => choose(match)}
              onKeyDown={(event) => rowKeyDown(event, index)}
            >
              <span><strong>{match.name}</strong>{match.normalizedPhone ? <small>{match.normalizedPhone}</small> : null}</span>
              {Number(match.dueBalance) > 0 && <small className="customer-suggest-due">Due ৳{match.dueBalance}</small>}
            </button>
          ))}
          <button
            ref={(node) => { rowRefs.current[newRowRefIndex] = node; }}
            type="button"
            role="option"
            className="customer-suggest-new"
            disabled={creating}
            onClick={() => void addNew(term)}
            onKeyDown={(event) => rowKeyDown(event, newRowRefIndex)}
          >
            <strong>{creating ? 'Adding customer…' : `Add “${term}” as new customer`}</strong>
            <span>{BD_MOBILE.test(term.replace(/\D/g, '')) ? `Saved with phone ${term}` : 'Saved to your customers for future sales'}</span>
          </button>
          {searchQuery.isFetching && <p className="finder-note" role="status">Searching customers…</p>}
          {!online && <p className="finder-note" role="alert">Connect to search or add customers.</p>}
        </div>
      )}
    </div>
  );
}

function PosUserIcon(): ReactNode {
  return <svg className="pos-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="8" r="3.5" /><path d="M5 20a7 7 0 0 1 14 0" /></svg>;
}

function PosSearchIcon(): ReactNode {
  return <svg className="pos-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round"><circle cx="10.5" cy="10.5" r="5.5" /><path d="m15 15 4 4" /></svg>;
}
