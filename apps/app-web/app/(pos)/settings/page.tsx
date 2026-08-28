'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';

import { pharmacyApi } from '@/lib/api';
import { useSession } from '@/lib/session';

type OrganizationForm = {
  name: string; slug: string; defaultTimezone: string; locale: string;
  requirePinForDiscounts: boolean; expiryAlertDays: string; lowStockThresholdDays: string;
  allowNegativeStock: boolean; receiptFooter: string;
};

type BranchForm = {
  name: string; timezone: string; receiptHeader: string; receiptFooter: string;
  businessDayCutoffHour: string; lowStockAlerts: boolean; allowOfflineSales: boolean; printReceiptByDefault: boolean;
};

const blankOrganization: OrganizationForm = {
  name: '', slug: '', defaultTimezone: 'Asia/Dhaka', locale: 'en-BD', requirePinForDiscounts: true,
  expiryAlertDays: '90', lowStockThresholdDays: '14', allowNegativeStock: false, receiptFooter: '',
};
const blankBranch: BranchForm = {
  name: '', timezone: 'Asia/Dhaka', receiptHeader: '', receiptFooter: '', businessDayCutoffHour: '0',
  lowStockAlerts: true, allowOfflineSales: true, printReceiptByDefault: true,
};

export default function SettingsPage(): ReactNode {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const owner = user?.role === 'owner';
  const hasStore = Boolean(user?.storeId);

  const profileQuery = useQuery({ queryKey: ['settings', 'organization-profile'], queryFn: async () => (await pharmacyApi.organizations.readProfile()).data, enabled: owner });
  const organizationQuery = useQuery({ queryKey: ['settings', 'organization'], queryFn: async () => (await pharmacyApi.organizations.readSettings()).data.settings, enabled: owner });
  const branchQuery = useQuery({ queryKey: ['settings', 'branch', user?.storeId], queryFn: async () => (await pharmacyApi.stores.readCurrent()).data, enabled: hasStore });

  const [organization, setOrganization] = useState(blankOrganization);
  const [organizationSaved, setOrganizationSaved] = useState(blankOrganization);
  const [branch, setBranch] = useState(blankBranch);
  const [branchSaved, setBranchSaved] = useState(blankBranch);
  const [organizationStatus, setOrganizationStatus] = useState<string | null>(null);
  const [branchStatus, setBranchStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState<'organization' | 'branch' | null>(null);

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
      receiptHeader: branchQuery.data.settings.receiptHeader ?? '', receiptFooter: branchQuery.data.settings.receiptFooter ?? '',
      businessDayCutoffHour: String(branchQuery.data.settings.businessDayCutoffHour), lowStockAlerts: branchQuery.data.settings.lowStockAlerts,
      allowOfflineSales: branchQuery.data.settings.allowOfflineSales, printReceiptByDefault: branchQuery.data.settings.printReceiptByDefault,
    };
    setBranch(next); setBranchSaved(next);
  }, [branchQuery.data]);

  const organizationDirty = useMemo(() => JSON.stringify(organization) !== JSON.stringify(organizationSaved), [organization, organizationSaved]);
  const branchDirty = useMemo(() => JSON.stringify(branch) !== JSON.stringify(branchSaved), [branch, branchSaved]);

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
        receiptHeader: branch.receiptHeader.trim() || null, receiptFooter: branch.receiptFooter.trim() || null,
        businessDayCutoffHour: Number(branch.businessDayCutoffHour), lowStockAlerts: branch.lowStockAlerts,
        allowOfflineSales: branch.allowOfflineSales, printReceiptByDefault: branch.printReceiptByDefault,
      });
      setBranchSaved(branch); setBranchStatus('Branch settings saved.');
      await queryClient.invalidateQueries({ queryKey: ['settings', 'branch'] });
    } catch (cause) { setBranchStatus(cause instanceof Error ? cause.message : 'Could not save branch settings.'); }
    finally { setSaving(null); }
  }

  return (
    <main className="page-shell settings-page">
      <header className="page-heading"><div><span className="eyebrow">Administration</span><h1>Settings</h1><p>Control organization defaults and how this branch operates.</p></div></header>

      {owner && (
        <SettingsSection title="Organization" description="Defaults shared by every branch." dirty={organizationDirty} loading={profileQuery.isPending || organizationQuery.isPending}>
          <form className="settings-form" onSubmit={(event) => void saveOrganization(event)}>
            <div className="form-grid form-grid--two">
              <Field label="Organization name"><input className="field" value={organization.name} onChange={(event) => setOrganization({ ...organization, name: event.target.value })} /></Field>
              <Field label="URL slug"><input className="field" value={organization.slug} onChange={(event) => setOrganization({ ...organization, slug: event.target.value.toLowerCase() })} /></Field>
              <Field label="Default timezone"><input className="field" value={organization.defaultTimezone} onChange={(event) => setOrganization({ ...organization, defaultTimezone: event.target.value })} /></Field>
              <Field label="Locale"><input className="field" value={organization.locale} onChange={(event) => setOrganization({ ...organization, locale: event.target.value })} /></Field>
              <Field label="Expiry alert days"><input className="field" type="number" min={1} max={365} value={organization.expiryAlertDays} onChange={(event) => setOrganization({ ...organization, expiryAlertDays: event.target.value.replace(/\D/g, '') })} /></Field>
              <Field label="Low-stock forecast days"><input className="field" type="number" min={1} max={180} value={organization.lowStockThresholdDays} onChange={(event) => setOrganization({ ...organization, lowStockThresholdDays: event.target.value.replace(/\D/g, '') })} /></Field>
            </div>
            <Field label="Organization receipt footer"><textarea className="field field--textarea" value={organization.receiptFooter} onChange={(event) => setOrganization({ ...organization, receiptFooter: event.target.value })} /></Field>
            <div className="toggle-list">
              <Toggle label="Require owner or manager PIN for discounts" checked={organization.requirePinForDiscounts} onChange={(checked) => setOrganization({ ...organization, requirePinForDiscounts: checked })} />
              <Toggle label="Allow negative inventory balances" checked={organization.allowNegativeStock} onChange={(checked) => setOrganization({ ...organization, allowNegativeStock: checked })} />
            </div>
            <FormFooter dirty={organizationDirty} status={organizationStatus} saving={saving === 'organization'} />
          </form>
        </SettingsSection>
      )}

      <SettingsSection title="Current branch" description={user?.storeName ? `Operating preferences for ${user.storeName}.` : 'Choose a branch to edit its preferences.'} dirty={branchDirty} loading={branchQuery.isPending}>
        {hasStore ? <form className="settings-form" onSubmit={(event) => void saveBranch(event)}>
          <div className="form-grid form-grid--two">
            <Field label="Branch name"><input className="field" value={branch.name} onChange={(event) => setBranch({ ...branch, name: event.target.value })} /></Field>
            <Field label="Timezone"><input className="field" value={branch.timezone} onChange={(event) => setBranch({ ...branch, timezone: event.target.value })} /></Field>
            <Field label="Currency"><input className="field" value="BDT" disabled /></Field>
            <Field label="Business-day cutoff"><input className="field" type="number" min={0} max={23} value={branch.businessDayCutoffHour} onChange={(event) => setBranch({ ...branch, businessDayCutoffHour: event.target.value.replace(/\D/g, '') })} /></Field>
          </div>
          <div className="form-grid form-grid--two">
            <Field label="Receipt header"><textarea className="field field--textarea" value={branch.receiptHeader} onChange={(event) => setBranch({ ...branch, receiptHeader: event.target.value })} /></Field>
            <Field label="Receipt footer"><textarea className="field field--textarea" value={branch.receiptFooter} onChange={(event) => setBranch({ ...branch, receiptFooter: event.target.value })} /></Field>
          </div>
          <div className="toggle-list">
            <Toggle label="Show low-stock alerts" checked={branch.lowStockAlerts} onChange={(checked) => setBranch({ ...branch, lowStockAlerts: checked })} />
            <Toggle label="Allow offline sales on registered terminals" checked={branch.allowOfflineSales} onChange={(checked) => setBranch({ ...branch, allowOfflineSales: checked })} />
            <Toggle label="Print receipts by default" checked={branch.printReceiptByDefault} onChange={(checked) => setBranch({ ...branch, printReceiptByDefault: checked })} />
          </div>
          <FormFooter dirty={branchDirty} status={branchStatus} saving={saving === 'branch'} />
        </form> : <p className="empty-copy">No branch is selected in this session.</p>}
      </SettingsSection>
    </main>
  );
}

function SettingsSection({ title, description, dirty, loading, children }: { title: string; description: string; dirty: boolean; loading: boolean; children: ReactNode }): ReactNode {
  return <section className="settings-section"><header className="section-heading"><div><h2>{title}</h2><p>{description}</p></div>{dirty && <span className="dirty-indicator">Unsaved changes</span>}</header>{loading ? <p className="empty-copy">Loading settings…</p> : children}</section>;
}

function Field({ label, children }: { label: string; children: ReactNode }): ReactNode { return <label className="form-field"><span>{label}</span>{children}</label>; }
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }): ReactNode { return <label className="toggle-row"><span>{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /></label>; }
function FormFooter({ dirty, status, saving }: { dirty: boolean; status: string | null; saving: boolean }): ReactNode { return <footer className="form-footer"><span role="status" className="form-status">{status}</span><button className="primary-action" type="submit" disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save changes'}</button></footer>; }
