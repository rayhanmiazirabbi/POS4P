'use client';

import { create } from 'zustand';

import type { PrintableReceipt } from './receipt';

/** A configured digital tender slug ("bkash", "rocket", ...), from organization settings. */
export type DigitalMethod = string;

type PosUiState = {
  cashReceived: string;
  digitalAmount: string;
  digitalMethod: DigitalMethod;
  redeemPoints: string;
  receipt: PrintableReceipt | null;
  setCashReceived: (value: string) => void;
  setDigitalAmount: (value: string) => void;
  setDigitalMethod: (method: DigitalMethod) => void;
  setRedeemPoints: (value: string) => void;
  setReceipt: (receipt: PrintableReceipt | null) => void;
  resetTender: () => void;
};

/**
 * Tender inputs and the last receipt -- state that exists only while this tab is
 * open on the counter. The cart and the offline outbox stay in the page: they are
 * sale data with recovery rules, not UI preference.
 */
export const usePosUi = create<PosUiState>()((set) => ({
  cashReceived: '',
  digitalAmount: '',
  digitalMethod: '',
  redeemPoints: '',
  receipt: null,
  setCashReceived: (cashReceived) => set({ cashReceived }),
  setDigitalAmount: (digitalAmount) => set({ digitalAmount }),
  setDigitalMethod: (digitalMethod) => set({ digitalMethod }),
  setRedeemPoints: (redeemPoints) => set({ redeemPoints }),
  setReceipt: (receipt) => set({ receipt }),
  resetTender: () => set({ cashReceived: '', digitalAmount: '', redeemPoints: '' }),
}));
