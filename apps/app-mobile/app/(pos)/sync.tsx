import { router } from 'expo-router';
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { ActivityIndicator, Button, Pressable, ScrollView, Text, View } from 'react-native';

import { pharmacyApi } from '../../src/lib/api';
import { RequireCapability } from '../../src/lib/guard';
import { flushQueue, forgetSale, queueStatus, recoverOutbox, type SaleQueueStatus } from '../../src/lib/offlineSales';
import { useSession } from '../../src/lib/session';

const empty: SaleQueueStatus = { pending: 0, retrying: 0, stuck: [], nextRetryAt: null };

// Gated with the counter, not separately: this screen reports on the till's own
// outbox, so a role that cannot ring up a sale has nothing to read here.
export default function SyncScreen(): ReactNode {
  return (
    <RequireCapability capability="sales.create">
      <SyncStatus />
    </RequireCapability>
  );
}

function SyncStatus(): ReactNode {
  const { user, signOut } = useSession();
  const [status, setStatus] = useState<SaleQueueStatus>(empty);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    void queueStatus()
      .then(setStatus)
      // An unreadable queue must not read as "all uploaded".
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : 'Could not read the offline queue'));
  }, []);

  useEffect(() => {
    // A sale left mid-upload by a killed app is invisible to the flush until it is
    // put back in line, so this runs before anything reads the queue.
    void recoverOutbox().then(refresh, refresh);
  }, [refresh]);

  async function upload(): Promise<void> {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      // Straight to `/sync/events`: it answers per event, so a sale the server
      // cannot take yet is held and retried while the rest go through.
      const result = await flushQueue(async (events) => (await pharmacyApi.sync.ingest(events)).data.acks);
      const accepted = result.uploaded + result.duplicates;
      const parts = [`${accepted} uploaded`];
      if (result.retrying > 0) parts.push(`${result.retrying} will retry`);
      if (result.rejected > 0) parts.push(`${result.rejected} need re-entering`);
      setMessage(parts.join(' · '));
      if (result.firstError !== null) setError(result.firstError);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Upload failed');
    } finally {
      refresh();
      setBusy(false);
    }
  }

  const deviceReady = user !== null && user.deviceId !== null && user.deviceId !== undefined;

  return (
    <ScrollView style={{ flex: 1, backgroundColor: '#F8FAFC' }} contentContainerStyle={{ padding: 24, gap: 16 }}>
      <Text style={{ fontSize: 20, fontWeight: '700', color: '#172033' }}>Sync status</Text>
      <Text style={{ color: '#64748B' }}>
        {user?.organizationName ?? ''} · {user?.storeName ?? 'no store'} · {user?.role ?? ''}
      </Text>
      {!deviceReady && (
        // Without a device row nothing can be uploaded, and the counter needs to
        // know that before the shop loses signal, not after.
        <Text style={{ color: '#B91C1C' }}>This phone is not registered for offline sales. Sign out and sign in again while online.</Text>
      )}
      <Text>{status.pending === 0 ? 'All sales uploaded.' : `${status.pending} sale(s) waiting to upload.`}</Text>
      {status.nextRetryAt !== null && (
        <Text style={{ color: '#A16207' }}>Next retry at {new Date(status.nextRetryAt).toLocaleTimeString()}.</Text>
      )}
      {busy ? <ActivityIndicator /> : <Button title="Upload now" onPress={() => void upload()} disabled={status.pending === 0} />}
      {message !== null && <Text style={{ color: '#166534' }}>{message}</Text>}
      {error !== null && <Text style={{ color: '#B91C1C' }}>{error}</Text>}

      {status.stuck.length > 0 && (
        <View style={{ gap: 8, padding: 12, borderRadius: 8, backgroundColor: '#FEF2F2', borderWidth: 1, borderColor: '#FCA5A5' }}>
          <Text style={{ fontWeight: '700', color: '#7F1D1D' }}>{status.stuck.length} sale(s) the server will not accept</Text>
          <Text style={{ color: '#7F1D1D' }}>
            These were taken on this phone and are recorded nowhere else. Re-enter each one, then clear it.
          </Text>
          {status.stuck.map((entry) => (
            <View key={entry.eventId} style={{ gap: 2, paddingTop: 8, borderTopWidth: 1, borderColor: '#FCA5A5' }}>
              <Text style={{ fontWeight: '600' }}>
                ৳{entry.payload.total ?? '—'} · {new Date(entry.createdAt).toLocaleString()}
              </Text>
              <Text style={{ color: '#64748B' }}>{entry.reason ?? 'Rejected'}</Text>
              <Pressable
                onPress={() => {
                  void forgetSale(entry.eventId).then(refresh);
                }}
              >
                <Text style={{ color: '#0F766E' }}>Re-entered — clear this</Text>
              </Pressable>
            </View>
          ))}
        </View>
      )}

      <View style={{ height: 24 }} />
      <Button
        title="Sign out"
        onPress={() => {
          void signOut().then(() => router.replace('/(auth)/login'));
        }}
      />
    </ScrollView>
  );
}
