'use client';

import { useQuery } from '@tanstack/react-query';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { formatMoney, isZero, money } from '@pharmacy/money';
import type { CSSProperties, ReactNode } from 'react';

import { pharmacyApi } from '@/lib/api';
import { useSession } from '@/lib/session';

const card: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };

/** The API sends money as a decimal string; render it through the shared formatter
 *  so grouping and the currency sign match every other surface. */
const taka = (amount: string): string => formatMoney(money(amount));

/** Owner numbers move all day; a dashboard that needed a reload to say so was a
 *  report of this morning. Focus refetch is on by default and kept explicit here
 *  because it is part of the contract, not an accident of defaults. */
const REFETCH_INTERVAL_MS = 60_000;

export default function DashboardPage(): ReactNode {
  const { user } = useSession();

  // Four reads, four fates: one endpoint refusing must not blank the other three,
  // which the single Promise.all it used to be could not help doing.
  const metrics = useQuery({
    queryKey: ['reports', 'today'],
    queryFn: async () => (await pharmacyApi.reports.today()).data,
    refetchInterval: REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: true,
  });
  const lowStock = useQuery({
    queryKey: ['reports', 'low-stock'],
    queryFn: async () => (await pharmacyApi.reports.lowStock()).data,
    refetchInterval: REFETCH_INTERVAL_MS,
  });
  const expiry = useQuery({
    queryKey: ['reports', 'expiry', 30],
    queryFn: async () => (await pharmacyApi.reports.expiry(30)).data,
    refetchInterval: REFETCH_INTERVAL_MS,
  });
  const recentSales = useQuery({
    queryKey: ['sales', 'recent'],
    queryFn: async () => (await pharmacyApi.sales.list({}, { limit: 10 })).items,
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  return (
    <main style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: spacing.lg, padding: spacing.lg }}>
      <section style={card}>
        <h2 style={{ marginTop: 0, fontSize: tokens.typography.sizes.lg }}>Today</h2>
        {metrics.isPending ? (
          <p style={{ color: colors.muted }}>Loading…</p>
        ) : metrics.isError ? (
          <p role="alert" style={{ margin: 0, color: colors.danger }}>{queryMessage(metrics.error)}</p>
        ) : (
          <>
            <p style={{ fontSize: tokens.typography.sizes.xl, margin: `0 0 ${spacing.sm}` }}>{taka(metrics.data.netSalesTotal)}</p>
            <p style={{ color: colors.muted, margin: 0 }}>
              {metrics.data.transactionCount} transactions · {metrics.data.businessDate}
            </p>
            {!isZero(money(metrics.data.refundTotal)) && (
              <p style={{ color: colors.warning, margin: `${spacing.xs} 0 0` }}>
                {taka(metrics.data.salesTotal)} gross less {taka(metrics.data.refundTotal)} refunded
              </p>
            )}
            <ul style={{ listStyle: 'none', padding: 0, margin: `${spacing.md} 0 0` }}>
              {Object.entries(metrics.data.paymentBreakdown).map(([method, amount]) => (
                <li key={method} style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: colors.muted }}>{method}</span>
                  <span>{taka(amount)}</span>
                </li>
              ))}
            </ul>
            <dl style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: spacing.xs, margin: `${spacing.md} 0 0` }}>
              {/* Collected is what the till should hold; due is credit extended. */}
              <dt style={{ color: colors.muted }}>Collected</dt>
              <dd style={{ margin: 0, textAlign: 'right' }}>{taka(metrics.data.collectedTotal)}</dd>
              <dt style={{ color: colors.muted }}>On credit</dt>
              <dd style={{ margin: 0, textAlign: 'right' }}>{taka(metrics.data.dueTotal)}</dd>
              <dt style={{ color: colors.muted }}>Expenses</dt>
              <dd style={{ margin: 0, textAlign: 'right' }}>{taka(metrics.data.expenseTotal)}</dd>
            </dl>
            <p style={{ margin: `${spacing.md} 0 0`, color: metrics.data.profit == null ? colors.muted : colors.foreground }}>
              {metrics.data.profit == null ? `Profit hidden for ${user?.role ?? 'this role'}` : `Profit ${taka(metrics.data.profit)}`}
            </p>
          </>
        )}
      </section>

      <section style={card}>
        <h2 style={{ marginTop: 0, fontSize: tokens.typography.sizes.lg }}>Low stock ({lowStock.data?.length ?? 0})</h2>
        {lowStock.isError ? (
          <p role="alert" style={{ margin: 0, color: colors.danger }}>{queryMessage(lowStock.error)}</p>
        ) : (
          <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {(lowStock.data ?? []).map((item) => (
              <li key={item.storeProductId} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: spacing.xs }}>
                <span>{item.sku}</span>
                <span style={{ color: colors.warning }}>{item.available} left</span>
              </li>
            ))}
            {lowStock.isPending && <li style={{ color: colors.muted }}>Loading…</li>}
            {!lowStock.isPending && (lowStock.data ?? []).length === 0 && <li style={{ color: colors.muted }}>Nothing under minimum.</li>}
          </ul>
        )}
      </section>

      <section style={card}>
        <h2 style={{ marginTop: 0, fontSize: tokens.typography.sizes.lg }}>Expiring ≤ 30 days ({expiry.data?.length ?? 0})</h2>
        {expiry.isError ? (
          <p role="alert" style={{ margin: 0, color: colors.danger }}>{queryMessage(expiry.error)}</p>
        ) : (
          <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {(expiry.data ?? []).map((warning) => (
              <li key={warning.batchId} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: spacing.xs }}>
                <span>{warning.sku} · {warning.batchNumber}</span>
                <span style={{ color: warning.daysUntilExpiry <= 7 ? colors.danger : colors.warning }}>{warning.expiryDate} ({warning.daysUntilExpiry}d)</span>
              </li>
            ))}
            {expiry.isPending && <li style={{ color: colors.muted }}>Loading…</li>}
            {!expiry.isPending && (expiry.data ?? []).length === 0 && <li style={{ color: colors.muted }}>No batches expiring soon.</li>}
          </ul>
        )}
      </section>

      <section style={card}>
        <h2 style={{ marginTop: 0, fontSize: tokens.typography.sizes.lg }}>Recent sales</h2>
        {recentSales.isError ? (
          <p role="alert" style={{ margin: 0, color: colors.danger }}>{queryMessage(recentSales.error)}</p>
        ) : (
          <>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: tokens.typography.sizes.sm }}>
              <thead>
                <tr style={{ textAlign: 'left', color: colors.muted }}>
                  <th>Receipt</th><th>Total</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(recentSales.data ?? []).map((sale) => (
                  <tr key={sale.id}>
                    <td style={{ padding: `${spacing.xs} 0` }}>{sale.receiptNumber}</td>
                    <td>{taka(sale.total)}</td>
                    <td style={{ color: sale.status === 'completed' ? colors.success : colors.danger }}>{sale.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {recentSales.isPending && <p style={{ color: colors.muted }}>Loading…</p>}
            {!recentSales.isPending && (recentSales.data ?? []).length === 0 && <p style={{ color: colors.muted }}>No sales yet.</p>}
          </>
        )}
      </section>
    </main>
  );
}

function queryMessage(error: Error | null): string {
  return error instanceof Error ? error.message : 'Could not load this panel';
}
