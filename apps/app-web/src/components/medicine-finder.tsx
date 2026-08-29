'use client';

import { useQuery } from '@tanstack/react-query';
import type { CatalogSearchItem } from '@pharmacy/api';
import { matchShelf, submitShelfEntry, type ShelfProduct } from '@pharmacy/sync';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { pharmacyApi } from '@/lib/api';

export type MedicineSelection =
  | { kind: 'local'; item: ShelfProduct }
  | { kind: 'catalog'; item: CatalogSearchItem }
  | { kind: 'custom'; name: string };
type SearchSelection = Exclude<MedicineSelection, { kind: 'custom' }>;

/** Recalled searches live on the terminal, like shell history on a machine. */
const SEARCH_HISTORY_KEY = 'medicine-search-history';
const SEARCH_HISTORY_LIMIT = 50;

function loadSearchHistory(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(SEARCH_HISTORY_KEY) ?? '[]');
    return Array.isArray(parsed) ? parsed.filter((entry): entry is string => typeof entry === 'string') : [];
  } catch {
    return [];
  }
}

function saveSearchHistory(entries: readonly string[]): void {
  try {
    window.localStorage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(entries.slice(0, SEARCH_HISTORY_LIMIT)));
  } catch {
    // Private browsing or no quota: this session's recall still works from state.
  }
}

export function MedicineFinder({
  products,
  onSelect,
  actionLabel,
  autoFocus = false,
}: {
  products: readonly ShelfProduct[];
  onSelect: (selection: MedicineSelection) => void;
  actionLabel: string;
  autoFocus?: boolean;
}): ReactNode {
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [manualGlobal, setManualGlobal] = useState<boolean | null>(null);
  const [pendingSubmit, setPendingSubmit] = useState(false);
  // Newest first. `historyIndex === null` means live typing; a number means the
  // box is showing that entry, and `draft` holds the abandoned text to restore.
  const [history, setHistory] = useState<string[]>(() => loadSearchHistory());
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 220);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setManualGlobal(null), [debounced]);

  const localMatches = useMemo(() => (query.trim() === '' ? [] : matchShelf(products, query).map((match) => match.product)), [products, query]);
  const autoGlobal = query.trim() !== '' && (products.length === 0 || localMatches.length === 0);
  const globalEnabled = manualGlobal ?? autoGlobal;
  const globalQuery = useQuery({
    queryKey: ['medicine-finder', debounced],
    enabled: globalEnabled && debounced !== '' && (typeof navigator === 'undefined' || navigator.onLine),
    queryFn: async () => await pharmacyApi.products.search({ q: debounced }, { limit: 30 }),
    staleTime: 15_000,
  });
  const globalRows = globalQuery.data?.items ?? [];
  const rows: SearchSelection[] = [
    ...localMatches.map((item): SearchSelection => ({ kind: 'local', item })),
    ...globalRows
      .filter((row) => !localMatches.some((local) => local.id === row.storeProductId))
      .map((item): SearchSelection => ({ kind: 'catalog', item })),
  ];

  useEffect(() => {
    if (!pendingSubmit || globalQuery.isFetching || debounced === '') return;
    const exact = globalRows.find((row) => row.matchQuality === 'exact' && (row.matchedField === 'barcode' || row.matchedField === 'sku'));
    if (exact) {
      setPendingSubmit(false);
      rememberSearch(debounced);
      onSelect({ kind: 'catalog', item: exact });
      setQuery('');
      setHistoryIndex(null);
      inputRef.current?.focus();
      return;
    }
    setPendingSubmit(false);
  }, [debounced, globalQuery.isFetching, globalRows, onSelect, pendingSubmit]);

  /** Only a search that rang something up enters history, like an executed command. */
  function rememberSearch(term: string): void {
    const trimmed = term.trim();
    if (trimmed === '') return;
    setHistory((current) => {
      if (current[0] === trimmed) return current;
      const next = [trimmed, ...current].slice(0, SEARCH_HISTORY_LIMIT);
      saveSearchHistory(next);
      return next;
    });
  }

  function recallOlder(): void {
    if (history.length === 0) return;
    if (historyIndex === null) {
      setDraft(query);
      setHistoryIndex(0);
      setQuery(history[0]!);
      return;
    }
    const older = historyIndex + 1;
    if (older >= history.length) return; // already the oldest entry
    setHistoryIndex(older);
    setQuery(history[older]!);
  }

  /** Returns true when the press was consumed walking history, so ArrowDown can fall through to the rows otherwise. */
  function recallNewer(): boolean {
    if (historyIndex === null) return false;
    if (historyIndex === 0) {
      setHistoryIndex(null);
      setQuery(draft); // past the newest entry: back to what was being typed
      return true;
    }
    const newer = historyIndex - 1;
    setHistoryIndex(newer);
    setQuery(history[newer]!);
    return true;
  }

  function choose(selection: MedicineSelection): void {
    setPendingSubmit(false);
    rememberSearch(query);
    onSelect(selection);
    setQuery('');
    setHistoryIndex(null);
    // Choosing from a focused row unmounts the rows (query clears), which drops
    // focus to the page body. Park it back in the box so the next scan just types.
    inputRef.current?.focus();
  }

  function submit(): void {
    const local = submitShelfEntry(products, query);
    if (local.status === 'product') {
      choose({ kind: 'local', item: local.product });
      return;
    }
    const exact = globalRows.find((row) => row.matchQuality === 'exact' && (row.matchedField === 'barcode' || row.matchedField === 'sku'));
    if (exact) choose({ kind: 'catalog', item: exact });
    else if (rows.length === 1 && rows[0]) choose(rows[0]);
    else if (globalEnabled && query.trim() !== '') {
      setDebounced(query.trim());
      setPendingSubmit(true);
    }
  }

  return (
    <div className="medicine-finder">
      <div className="medicine-search-row">
        <input
          ref={inputRef}
          className="field medicine-search"
          value={query}
          autoFocus={autoFocus}
          placeholder="Scan barcode or search medicine, generic, strength, or SKU…"
          aria-label="Search medicines"
          onChange={(event) => { setQuery(event.target.value); setHistoryIndex(null); }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') { event.preventDefault(); submit(); }
            else if (event.key === 'Escape') { setPendingSubmit(false); setQuery(''); setHistoryIndex(null); }
            else if (event.key === 'ArrowUp') {
              // Terminal recall: each press walks one entry older; typing resets it.
              event.preventDefault();
              recallOlder();
            } else if (event.key === 'ArrowDown') {
              if (recallNewer()) { event.preventDefault(); return; }
              if (rows.length > 0) {
                event.preventDefault();
                rowRefs.current[0]?.focus();
              }
            }
          }}
        />
        {query !== '' && <button type="button" tabIndex={-1} className="quiet-action" aria-label="Clear search" onClick={() => setQuery('')}>Clear</button>}
      </div>
      <label className="global-toggle">
        <input
          tabIndex={-1}
          type="checkbox"
          checked={globalEnabled}
          onChange={(event) => setManualGlobal(event.target.checked)}
        />
        Global search
        {autoGlobal && manualGlobal === null ? <span>Enabled because no local medicine matched</span> : null}
      </label>

      {query.trim() !== '' && (
        <div className="medicine-results" role="listbox" aria-label="Medicine results">
          {rows.map((selection, index) => {
            const row = selection.item;
            const name = row.name;
            const identity = [row.genericName, row.strength, row.dosageForm, row.manufacturer].filter(Boolean).join(' · ');
            const status = selection.kind === 'local'
              ? selection.item.availableQuantity === undefined ? 'On shelf' : Number(selection.item.availableQuantity) > 0 ? `${selection.item.availableQuantity} in stock` : 'Out of stock'
              : selection.item.shopStatus === 'on_shelf' ? `${selection.item.availableQuantity ?? 0} in stock` : selection.item.shopStatus === 'in_org' ? 'In organization' : 'Global catalogue';
            return (
              <button
                key={selection.kind === 'local' ? `local:${selection.item.id}` : `catalog:${selection.item.catalogProductId ?? selection.item.pharmacyProductId ?? name}`}
                ref={(node) => { rowRefs.current[index] = node; }}
                type="button"
                role="option"
                tabIndex={-1}
                className="medicine-result"
                onClick={() => choose(selection)}
                onKeyDown={(event) => {
                  if (event.key === 'ArrowDown') { event.preventDefault(); rowRefs.current[(index + 1) % rows.length]?.focus(); }
                  else if (event.key === 'ArrowUp') { event.preventDefault(); index === 0 ? inputRef.current?.focus() : rowRefs.current[index - 1]?.focus(); }
                  else if (event.key === 'Escape') inputRef.current?.focus();
                }}
              >
                <span><strong>{name}</strong>{identity ? <small>{identity}</small> : null}</span>
                <span className={`medicine-status medicine-status--${selection.kind === 'local' && (row.availableQuantity === undefined || Number(row.availableQuantity) > 0) ? 'stock' : 'catalog'}`}>
                  {status}<small>{actionLabel}</small>
                </span>
              </button>
            );
          })}
          {globalQuery.isFetching && <p className="finder-note" role="status">Searching the global catalogue…</p>}
          {!globalQuery.isFetching && rows.length === 0 && globalEnabled && debounced !== '' && (
            <button type="button" tabIndex={-1} className="create-local" onClick={() => choose({ kind: 'custom', name: query.trim() })}>
              <strong>Create “{query.trim()}” for this store</strong>
              <span>This stays private to your organization.</span>
            </button>
          )}
          {globalEnabled && typeof navigator !== 'undefined' && !navigator.onLine && <p className="finder-note" role="alert">Connect to search or add medicines from the global catalogue.</p>}
        </div>
      )}
    </div>
  );
}
