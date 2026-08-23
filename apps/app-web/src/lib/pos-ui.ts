'use client';

import { create } from 'zustand';

import type { Receipt } from '@pharmacy/sales';

export type DigitalMethod = 'bkash' | 'nagad';

type PosUiState = {
  cashReceived: string;
  digitalAmount: string;
  digitalMethod: DigitalMethod;
  receipt: Receipt | null;
  setCashReceived: (value: string) => void;
  setDigitalAmount: (value: string) => void;
  setDigitalMethod: (method: DigitalMethod) => void;
  setReceipt: (receipt: Receipt | null) => void;
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
  digitalMethod: 'bkash',
  receipt: null,
  setCashReceived: (cashReceived) => set({ cashReceived }),
  setDigitalAmount: (digitalAmount) => set({ digitalAmount }),
  setDigitalMethod: (digitalMethod) => set({ digitalMethod }),
  setReceipt: (receipt) => set({ receipt }),
  resetTender: () => set({ cashReceived: '', digitalAmount: '' }),
}));
