'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { money } from '@pharmacy/money';
import { provisionalReceipt } from '@pharmacy/sales';
import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from 'react';

import { ReceiptDocument } from '@/components/receipt-document';
import { pharmacyApi } from '@/lib/api';
import { compressReceiptLogo, validReceiptLogoUrl } from '@/lib/receipt-logo';
import {
  cacheReceiptConfig,
  defaultReceiptConfig,
  MAX_RECEIPT_WIDTH_MM,
  MIN_RECEIPT_WIDTH_MM,
  receiptConfigFromSettings,
  receiptSettingsPatch,
  validReceiptWidth,
  type ReceiptConfig,
} from '@/lib/receipt';
import { useSession } from '@/lib/session';

type OrganizationForm = {
  name: string; slug: string; defaultTimezone: string; locale: string;
  requirePinForDiscounts: boolean; expiryAlertDays: string; lowStockThresholdDays: string;
  allowNegativeStock: boolean; receiptFooter: string;
};

type BranchForm = {
  name: string; timezone: string; businessDayCutoffHour: string;
  lowStockAlerts: boolean; allowOfflineSales: boolean;
};

const blankOrganization: OrganizationForm = {
  name: '', slug: '', defaultTimezone: 'Asia/Dhaka', locale: 'en-BD', requirePinForDiscounts: true,
  expiryAlertDays: '90', lowStockThresholdDays: '14', allowNegativeStock: false, receiptFooter: '',
};
const blankBranch: BranchForm = {
  name: '', timezone: 'Asia/Dhaka', businessDayCutoffHour: '0', lowStockAlerts: true, allowOfflineSales: true,
};

const visibilityOptions: readonly { key: keyof ReceiptConfig; label: string }[] = [
  { key: 'showLogo', label: 'Logo' },
  { key: 'showBusinessName', label: 'Business name' },
  { key: 'showStoreName', label: 'Branch name' },
  { key: 'showContactDetails', label: 'Contact details' },
  { key: 'showHeader', label: 'Custom header' },
  { key: 'showReceiptNumber', label: 'Receipt number' },
  { key: 'showDateTime', label: 'Date and time' },
  { key: 'showCustomer', label: 'Customer' },
  { key: 'showCashier', label: 'Cashier' },
  { key: 'showItems', label: 'Item list' },
  { key: 'showItemQuantity', label: 'Item quantity' },
  { key: 'showUnitPrice', label: 'Unit price' },
  { key: 'showLineTotal', label: 'Line total' },
  { key: 'showSubtotal', label: 'Subtotal' },
  { key: 'showDiscounts', label: 'Discounts' },
  { key: 'showCharges', label: 'Delivery and other charges' },
  { key: 'showTotal', label: 'Grand total' },
  { key: 'showPayments', label: 'Payment breakdown' },
  { key: 'showCashReceived', label: 'Cash received' },
  { key: 'showChangeDue', label: 'Change and due' },
  { key: 'showFooter', label: 'Footer' },
];

export default function SettingsPage(): ReactNode {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const owner = user?.role === 'owner';
  const hasStore = Boolean(user?.storeId);

  const profileQuery = useQuery({ queryKey: ['settings', 'organization-profile'], queryFn: async () => (await pharmacyApi.organizations.readProfile()).data, enabled: owner });
  const organizationQuery = useQuery({ queryKey: ['settings', 'organization'], queryFn: async () => (await pharmacyApi.organizations.readSettings()).data.settings, enabled: Boolean(user) });
  const branchQuery = useQuery({ queryKey: ['settings', 'branch', user?.storeId], queryFn: async () => (await pharmacyApi.stores.readCurrent()).data, enabled: hasStore });

  const [organization, setOrganization] = useState(blankOrganization);
  const [organizationSaved, setOrganizationSaved] = useState(blankOrganization);
  const [branch, setBranch] = useState(blankBranch);
  const [branchSaved, setBranchSaved] = useState(blankBranch);
  const [receipt, setReceipt] = useState<ReceiptConfig>(defaultReceiptConfig);
  const [receiptSaved, setReceiptSaved] = useState<ReceiptConfig>(defaultReceiptConfig);
  const [branchFooter, setBranchFooter] = useState('');
  const [branchFooterSaved, setBranchFooterSaved] = useState('');
  const [logoMode, setLogoMode] = useState<'url' | 'upload'>('url');
  const [organizationStatus, setOrganizationStatus] = useState<string | null>(null);
  const [branchStatus, setBranchStatus] = useState<string | null>(null);
  const [receiptStatus, setReceiptStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState<'organization' | 'branch' | 'receipt' | null>(null);

  useEffect(() => {
    if (!profileQuery.data || !organizationQuery.data) return;
    const next: OrganizationForm = {
      name: profileQuery.data.name, slug: profileQuery.data.slug,
      defaultTimezone: organizationQuery.data.defaultTimezone, locale: organizationQuery.data.locale,
      requirePinForDiscounts: organizationQuery.data.requirePinForDiscounts,
      expiryAlertDays: String(organizationQuery.data.expiryAlertDays), lowStockThresholdDays: String(organizationQuery.data.lowStockThresholdDays),
      allowNegativeStock: organizationQuery.data.allowNegativeStock, receiptFooter: organizationQuery.data.receiptFooter ?? '',
    };
    setOrganization(next); setOrganizationSaved(next);
  }, [profileQuery.data, organizationQuery.data]);

  useEffect(() => {
    if (!branchQuery.data) return;
    const next: BranchForm = {
      name: branchQuery.data.name, timezone: branchQuery.data.timezone,
      businessDayCutoffHour: String(branchQuery.data.settings.businessDayCutoffHour), lowStockAlerts: branchQuery.data.settings.lowStockAlerts,
      allowOfflineSales: branchQuery.data.settings.allowOfflineSales,
    };
    setBranch(next); setBranchSaved(next);
    const nextReceipt = receiptConfigFromSettings(branchQuery.data.settings, organizationQuery.data?.receiptFooter);
    setReceipt(nextReceipt); setReceiptSaved(nextReceipt);
    const nextFooter = branchQuery.data.settings.receiptFooter ?? '';
    setBranchFooter(nextFooter); setBranchFooterSaved(nextFooter);
    setLogoMode(nextReceipt.logo?.startsWith('data:') ? 'upload' : 'url');
  }, [branchQuery.data, organizationQuery.data?.receiptFooter]);

  const organizationDirty = useMemo(() => JSON.stringify(organization) !== JSON.stringify(organizationSaved), [organization, organizationSaved]);
  const branchDirty = useMemo(() => JSON.stringify(branch) !== JSON.stringify(branchSaved), [branch, branchSaved]);
  const receiptDirty = useMemo(() => JSON.stringify(receipt) !== JSON.stringify(receiptSaved) || branchFooter !== branchFooterSaved, [branchFooter, branchFooterSaved, receipt, receiptSaved]);
  const effectiveFooter = branchFooter.trim() || organization.receiptFooter.trim() || organizationQuery.data?.receiptFooter?.trim() || null;
  const previewConfig = useMemo(() => ({ ...receipt, footer: effectiveFooter }), [effectiveFooter, receipt]);
  const preview = useMemo(() => ({
    receipt: provisionalReceipt({
      organizationName: user?.organizationName ?? 'Pharmacy', storeName: branch.name || user?.storeName || 'Main branch', customerName: 'Ayesha · 01711000000',
      issuedAt: '2026-08-28T10:30:00Z', lines: [
        { id: 'sample-line-1', productId: 'sample-1', name: 'Paracetamol 500mg', quantity: 2, unitPrice: money('12.50'), discount: money('0.00'), tax: money('0.00') },
        { id: 'sample-line-2', productId: 'sample-2', name: 'Oral saline sachet', quantity: 1, unitPrice: money('20.00'), discount: money('0.00'), tax: money('0.00') },
      ], payments: [{ method: 'cash', amount: money('45.00'), receivedAmount: money('50.00') }],
    }),
    config: previewConfig,
    cashierName: user?.user.displayName ?? 'Cashier',
    locale: organization.locale || organizationQuery.data?.locale || 'en-BD',
    timezone: branch.timezone || 'Asia/Dhaka',
  }), [branch.name, branch.timezone, organization.locale, organizationQuery.data?.locale, previewConfig, user]);

  async function saveOrganization(event: FormEvent): Promise<void> {
    event.preventDefault(); setOrganizationStatus(null);
    if (organization.name.trim().length < 2 || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(organization.slug)) { setOrganizationStatus('Enter a valid organization name and lowercase URL slug.'); return; }
    setSaving('organization');
    try {
      await pharmacyApi.organizations.updateProfile({ name: organization.name.trim(), slug: organization.slug.trim() });
      await pharmacyApi.organizations.updateSettings({
        defaultTimezone: organization.defaultTimezone.trim(), defaultCurrency: 'BDT', locale: organization.locale.trim(),
        requirePinForDiscounts: organization.requirePinForDiscounts, expiryAlertDays: Number(organization.expiryAlertDays),
        lowStockThresholdDays: Number(organization.lowStockThresholdDays), allowNegativeStock: organization.allowNegativeStock,
        receiptFooter: organization.receiptFooter.trim() || null,
      });
      setOrganizationSaved(organization); setOrganizationStatus('Organization settings saved.');
      await queryClient.invalidateQueries({ queryKey: ['settings', 'organization'] });
      if (user?.organizationId && user.storeId && branchQuery.data) {
        const effective = receiptConfigFromSettings(branchQuery.data.settings, organization.receiptFooter);
        await cacheReceiptConfig(user.organizationId, user.storeId, effective);
        await queryClient.invalidateQueries({ queryKey: ['receipt-config', user.organizationId, user.storeId] });
      }
    } catch (cause) { setOrganizationStatus(cause instanceof Error ? cause.message : 'Could not save organization settings.'); }
    finally { setSaving(null); }
  }

  async function saveBranch(event: FormEvent): Promise<void> {
    event.preventDefault(); setBranchStatus(null);
    if (!user?.storeId || branch.name.trim().length < 2) { setBranchStatus('Enter a valid branch name.'); return; }
    setSaving('branch');
    try {
      await pharmacyApi.stores.update(user.storeId, { name: branch.name.trim(), timezone: branch.timezone.trim(), currency: 'BDT' });
      await pharmacyApi.stores.updateSettings(user.storeId, {
        businessDayCutoffHour: Number(branch.businessDayCutoffHour), lowStockAlerts: branch.lowStockAlerts, allowOfflineSales: branch.allowOfflineSales,
      });
      setBranchSaved(branch); setBranchStatus('Branch settings saved.');
      await queryClient.invalidateQueries({ queryKey: ['settings', 'branch'] });
      if (user.organizationId) await queryClient.invalidateQueries({ queryKey: ['receipt-config', user.organizationId, user.storeId] });
    } catch (cause) { setBranchStatus(cause instanceof Error ? cause.message : 'Could not save branch settings.'); }
    finally { setSaving(null); }
  }

  async function saveReceipt(event: FormEvent): Promise<void> {
    event.preventDefault(); setReceiptStatus(null);
    if (!user?.storeId || !user.organizationId) return;
    if (!validReceiptWidth(receipt.paperWidthMm)) { setReceiptStatus(`Paper width must be a whole number from ${MIN_RECEIPT_WIDTH_MM} to ${MAX_RECEIPT_WIDTH_MM} mm.`); return; }
    if (receipt.logo && !receipt.logo.startsWith('data:') && !validReceiptLogoUrl(receipt.logo)) { setReceiptStatus('Logo URL must begin with https://'); return; }
    setSaving('receipt');
    try {
      const response = await pharmacyApi.stores.updateSettings(user.storeId, receiptSettingsPatch(receipt, branchFooter));
      const savedConfig = receiptConfigFromSettings(response.data.settings, organization.receiptFooter || organizationQuery.data?.receiptFooter);
      setReceipt(savedConfig); setReceiptSaved(savedConfig);
      setBranchFooter(response.data.settings.receiptFooter ?? ''); setBranchFooterSaved(response.data.settings.receiptFooter ?? '');
      await cacheReceiptConfig(user.organizationId, user.storeId, savedConfig);
      setReceiptStatus('Receipt settings saved.');
      await queryClient.invalidateQueries({ queryKey: ['settings', 'branch'] });
      await queryClient.invalidateQueries({ queryKey: ['receipt-config', user.organizationId, user.storeId] });
    } catch (cause) { setReceiptStatus(cause instanceof Error ? cause.message : 'Could not save receipt settings.'); }
    finally { setSaving(null); }
  }

  async function uploadLogo(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setReceiptStatus('Processing logo…');
    try {
      const logo = await compressReceiptLogo(file);
      setReceipt((current) => ({ ...current, logo }));
      setLogoMode('upload'); setReceiptStatus(null);
    } catch (cause) { setReceiptStatus(cause instanceof Error ? cause.message : 'Could not process the logo.'); }
  }

  function setVisibility(key: keyof ReceiptConfig, checked: boolean): void {
    setReceipt((current) => ({ ...current, [key]: checked }));
  }

  return (
    <main className="page-shell settings-page">
      <header className="page-heading"><div><span className="eyebrow">Administration</span><h1>Settings</h1><p>Control organization defaults and how this branch operates.</p></div></header>

      {owner && <SettingsSection title="Organization" description="Defaults shared by every branch." dirty={organizationDirty} loading={profileQuery.isPending || organizationQuery.isPending}>
        <form className="settings-form" onSubmit={(event) => void saveOrganization(event)}>
          <div className="form-grid form-grid--two">
            <Field label="Organization name"><input className="field" value={organization.name} onChange={(event) => setOrganization({ ...organization, name: event.target.value })} /></Field>
            <Field label="URL slug"><input className="field" value={organization.slug} onChange={(event) => setOrganization({ ...organization, slug: event.target.value.toLowerCase() })} /></Field>
            <Field label="Default timezone"><input className="field" value={organization.defaultTimezone} onChange={(event) => setOrganization({ ...organization, defaultTimezone: event.target.value })} /></Field>
            <Field label="Locale"><input className="field" value={organization.locale} onChange={(event) => setOrganization({ ...organization, locale: event.target.value })} /></Field>
            <Field label="Expiry alert days"><input className="field" type="number" min={1} max={365} value={organization.expiryAlertDays} onChange={(event) => setOrganization({ ...organization, expiryAlertDays: event.target.value.replace(/\D/g, '') })} /></Field>
            <Field label="Low-stock forecast days"><input className="field" type="number" min={1} max={180} value={organization.lowStockThresholdDays} onChange={(event) => setOrganization({ ...organization, lowStockThresholdDays: event.target.value.replace(/\D/g, '') })} /></Field>
          </div>
          <Field label="Organization receipt footer"><textarea className="field field--textarea" maxLength={1000} value={organization.receiptFooter} onChange={(event) => setOrganization({ ...organization, receiptFooter: event.target.value })} /></Field>
          <div className="toggle-list">
            <Toggle label="Require owner or manager PIN for discounts" checked={organization.requirePinForDiscounts} onChange={(checked) => setOrganization({ ...organization, requirePinForDiscounts: checked })} />
            <Toggle label="Allow negative inventory balances" checked={organization.allowNegativeStock} onChange={(checked) => setOrganization({ ...organization, allowNegativeStock: checked })} />
          </div>
          <FormFooter dirty={organizationDirty} status={organizationStatus} saving={saving === 'organization'} />
        </form>
      </SettingsSection>}

      <SettingsSection title="Current branch" description={user?.storeName ? `Operating preferences for ${user.storeName}.` : 'Choose a branch to edit its preferences.'} dirty={branchDirty} loading={branchQuery.isPending}>
        {hasStore ? <form className="settings-form" onSubmit={(event) => void saveBranch(event)}>
          <div className="form-grid form-grid--two">
            <Field label="Branch name"><input className="field" value={branch.name} onChange={(event) => setBranch({ ...branch, name: event.target.value })} /></Field>
            <Field label="Timezone"><input className="field" value={branch.timezone} onChange={(event) => setBranch({ ...branch, timezone: event.target.value })} /></Field>
            <Field label="Currency"><input className="field" value="BDT" disabled /></Field>
            <Field label="Business-day cutoff"><input className="field" type="number" min={0} max={23} value={branch.businessDayCutoffHour} onChange={(event) => setBranch({ ...branch, businessDayCutoffHour: event.target.value.replace(/\D/g, '') })} /></Field>
          </div>
          <div className="toggle-list">
            <Toggle label="Show low-stock alerts" checked={branch.lowStockAlerts} onChange={(checked) => setBranch({ ...branch, lowStockAlerts: checked })} />
            <Toggle label="Allow offline sales on registered terminals" checked={branch.allowOfflineSales} onChange={(checked) => setBranch({ ...branch, allowOfflineSales: checked })} />
          </div>
          <FormFooter dirty={branchDirty} status={branchStatus} saving={saving === 'branch'} />
        </form> : <p className="empty-copy">No branch is selected in this session.</p>}
      </SettingsSection>

      {hasStore && <SettingsSection title="Receipt" description="Design the receipt used by every web counter in this branch." dirty={receiptDirty} loading={branchQuery.isPending || organizationQuery.isPending}>
        <form className="receipt-settings-layout" onSubmit={(event) => void saveReceipt(event)}>
          <div className="receipt-settings-controls">
            <div className="settings-group"><h3>Brand and contact</h3><div className="form-grid form-grid--two">
              <Field label="Display name"><input className="field" maxLength={320} placeholder={user?.organizationName ?? 'Organization name'} value={receipt.businessName ?? ''} onChange={(event) => setReceipt({ ...receipt, businessName: event.target.value || null })} /></Field>
              <Field label="Tax / VAT ID"><input className="field" maxLength={320} value={receipt.taxId ?? ''} onChange={(event) => setReceipt({ ...receipt, taxId: event.target.value || null })} /></Field>
              <Field label="Phone"><input className="field" maxLength={320} value={receipt.phone ?? ''} onChange={(event) => setReceipt({ ...receipt, phone: event.target.value || null })} /></Field>
              <Field label="Email"><input className="field" type="email" maxLength={320} value={receipt.email ?? ''} onChange={(event) => setReceipt({ ...receipt, email: event.target.value || null })} /></Field>
            </div><Field label="Address"><textarea className="field field--textarea" maxLength={1000} value={receipt.address ?? ''} onChange={(event) => setReceipt({ ...receipt, address: event.target.value || null })} /></Field></div>

            <div className="settings-group"><div className="settings-group-heading"><h3>Logo</h3><div className="segmented-control" aria-label="Logo source"><button type="button" className={logoMode === 'url' ? 'is-active' : ''} onClick={() => { setLogoMode('url'); if (receipt.logo?.startsWith('data:')) setReceipt({ ...receipt, logo: null }); }}>Image URL</button><button type="button" className={logoMode === 'upload' ? 'is-active' : ''} onClick={() => { setLogoMode('upload'); if (receipt.logo && !receipt.logo.startsWith('data:')) setReceipt({ ...receipt, logo: null }); }}>Upload</button></div></div>
              {logoMode === 'url' ? <Field label="HTTPS image URL"><input className="field" type="url" placeholder="https://example.com/logo.png" value={receipt.logo ?? ''} onChange={(event) => setReceipt({ ...receipt, logo: event.target.value || null })} /></Field> : <div className="logo-upload-row"><label className="quiet-action">Choose image<input className="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void uploadLogo(event)} /></label><span>{receipt.logo?.startsWith('data:') ? 'Image ready' : 'PNG, JPEG or WebP · max 5 MB'}</span></div>}
              {receipt.logo && <button type="button" className="disclosure danger-action" onClick={() => setReceipt({ ...receipt, logo: null })}>Remove logo</button>}
            </div>

            <div className="settings-group"><h3>Paper and behavior</h3><div className="paper-presets">{[{ label: '58 mm', width: 58 }, { label: '80 mm', width: 80 }, { label: 'A4', width: 210 }].map((option) => <button key={option.width} type="button" className={receipt.paperWidthMm === option.width ? 'quiet-action is-active' : 'quiet-action'} onClick={() => setReceipt({ ...receipt, paperWidthMm: option.width })}>{option.label}</button>)}</div>
              <Field label="Custom width (mm)"><input className="field" type="number" min={MIN_RECEIPT_WIDTH_MM} max={MAX_RECEIPT_WIDTH_MM} step={1} value={receipt.paperWidthMm} onChange={(event) => setReceipt({ ...receipt, paperWidthMm: Number(event.target.value) })} /></Field>
              <div className="toggle-list"><Toggle label="Open the print dialog after each completed sale" checked={receipt.printByDefault} onChange={(checked) => setReceipt({ ...receipt, printByDefault: checked })} /></div>
            </div>

            <div className="settings-group"><h3>Custom copy</h3><Field label="Receipt header"><textarea className="field field--textarea" maxLength={1000} value={receipt.header ?? ''} onChange={(event) => setReceipt({ ...receipt, header: event.target.value || null })} /></Field><Field label="Branch footer"><textarea className="field field--textarea" maxLength={1000} placeholder={organization.receiptFooter || organizationQuery.data?.receiptFooter || 'Uses the organization footer when blank'} value={branchFooter} onChange={(event) => setBranchFooter(event.target.value)} /></Field>{!branchFooter.trim() && (organization.receiptFooter || organizationQuery.data?.receiptFooter) && <p className="settings-help">Using organization footer: {organization.receiptFooter || organizationQuery.data?.receiptFooter}</p>}</div>

            <div className="settings-group"><h3>Visible content</h3><div className="receipt-toggle-grid">{visibilityOptions.map((option) => <Toggle key={option.key} label={option.label} checked={Boolean(receipt[option.key])} onChange={(checked) => setVisibility(option.key, checked)} />)}</div></div>
            <FormFooter dirty={receiptDirty} status={receiptStatus} saving={saving === 'receipt'} />
          </div>
          <aside className="receipt-live-preview"><div><span className="eyebrow">Live preview</span><p>The printed receipt uses this same layout.</p></div><div className="receipt-preview-canvas"><ReceiptDocument printable={preview} /></div></aside>
        </form>
      </SettingsSection>}
    </main>
  );
}

function SettingsSection({ title, description, dirty, loading, children }: { title: string; description: string; dirty: boolean; loading: boolean; children: ReactNode }): ReactNode {
  return <section className="settings-section"><header className="section-heading"><div><h2>{title}</h2><p>{description}</p></div>{dirty && <span className="dirty-indicator">Unsaved changes</span>}</header>{loading ? <p className="empty-copy">Loading settings…</p> : children}</section>;
}

function Field({ label, children }: { label: string; children: ReactNode }): ReactNode { return <label className="form-field"><span>{label}</span>{children}</label>; }
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }): ReactNode { return <label className="toggle-row"><span>{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /></label>; }
function FormFooter({ dirty, status, saving }: { dirty: boolean; status: string | null; saving: boolean }): ReactNode { return <footer className="form-footer"><span role="status" className="form-status">{status}</span><button className="primary-action" type="submit" disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save changes'}</button></footer>; }
