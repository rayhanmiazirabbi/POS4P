"""create catalogue, stock, POS, and sync domain tables

Revision ID: 0003_domain_tables
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import enum_column
from app.models.identity import RecordStatus, Role

revision: str = '0003_domain_tables'
down_revision: str | None = '0002_auth'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 0001 wrote these as bare VARCHAR; the models declare ``enum_column`` (VARCHAR(40)
#: + CHECK). Reusing ``enum_column`` keeps the migration in step with the models.
_WIDENED_ENUMS: tuple[tuple[str, str, sa.Enum], ...] = (
    ("organization_users", "role", enum_column(Role)),
    ("store_users", "role", enum_column(Role)),
    ("organizations", "status", enum_column(RecordStatus)),
    ("stores", "status", enum_column(RecordStatus)),
    ("users", "status", enum_column(RecordStatus)),
)


def upgrade() -> None:
    op.create_table('active_ingredients',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_active_ingredients')),
    sa.UniqueConstraint('name', name=op.f('uq_active_ingredients_name'))
    )
    op.create_table('billing_plans',
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('monthly_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('entitlements', sa.JSON(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_billing_plans')),
    sa.UniqueConstraint('code', name=op.f('uq_billing_plans_code'))
    )
    op.create_table('dosage_forms',
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dosage_forms')),
    sa.UniqueConstraint('name', name=op.f('uq_dosage_forms_name'))
    )
    op.create_table('manufacturers',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('country_code', sa.String(length=2), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manufacturers')),
    sa.UniqueConstraint('name', name=op.f('uq_manufacturers_name'))
    )
    op.create_table('ai_jobs',
    sa.Column('job_type', sa.String(length=100), nullable=False),
    sa.Column('status', sa.Enum('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'NEEDS_REVIEW', name='aijobstatus'), nullable=False),
    sa.Column('input_reference', sa.JSON(), nullable=False),
    sa.Column('provider', sa.String(length=80), nullable=True),
    sa.Column('model_version', sa.String(length=120), nullable=True),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_ai_jobs_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_jobs')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_ai_jobs_idempotency_key'))
    )
    op.create_table('catalog_products',
    sa.Column('manufacturer_id', sa.Uuid(), nullable=True),
    sa.Column('dosage_form_id', sa.Uuid(), nullable=True),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('strength', sa.String(length=100), nullable=True),
    sa.Column('package_size', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('package_unit', sa.String(length=40), nullable=False),
    sa.Column('prescription_required', sa.Boolean(), nullable=False),
    sa.Column('country_code', sa.String(length=2), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['dosage_form_id'], ['dosage_forms.id'], name=op.f('fk_catalog_products_dosage_form_id_dosage_forms')),
    sa.ForeignKeyConstraint(['manufacturer_id'], ['manufacturers.id'], name=op.f('fk_catalog_products_manufacturer_id_manufacturers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_catalog_products'))
    )
    op.create_index('ix_catalog_product_name', 'catalog_products', ['name'], unique=False)
    op.create_table('customers',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('normalized_phone', sa.String(length=32), nullable=True),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('due_balance', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('preferences', sa.JSON(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_customers_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customers')),
    sa.UniqueConstraint('organization_id', 'normalized_phone', name=op.f('uq_customers_organization_id'))
    )
    op.create_index('ix_customers_org_name', 'customers', ['organization_id', 'name'], unique=False)
    op.create_table('organization_subscriptions',
    sa.Column('plan_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.Enum('TRIAL', 'ACTIVE', 'PAST_DUE', 'CANCELLED', name='subscriptionstatus'), nullable=False),
    sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('grace_period_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('provider_subscription_id', sa.String(length=160), nullable=True),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_organization_subscriptions_organization_id_organizations')),
    sa.ForeignKeyConstraint(['plan_id'], ['billing_plans.id'], name=op.f('fk_organization_subscriptions_plan_id_billing_plans')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organization_subscriptions')),
    sa.UniqueConstraint('provider_subscription_id', name=op.f('uq_organization_subscriptions_provider_subscription_id'))
    )
    op.create_table('suppliers',
    sa.Column('name', sa.String(length=180), nullable=False),
    sa.Column('phone', sa.String(length=32), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('ACTIVE', 'INACTIVE', name='supplierstatus'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_suppliers_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_suppliers')),
    sa.UniqueConstraint('organization_id', 'name', name=op.f('uq_suppliers_organization_id'))
    )
    op.create_table('ai_confirmations',
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('confirmed_by_user_id', sa.Uuid(), nullable=False),
    sa.Column('decision', sa.String(length=40), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['confirmed_by_user_id'], ['users.id'], name=op.f('fk_ai_confirmations_confirmed_by_user_id_users')),
    sa.ForeignKeyConstraint(['job_id'], ['ai_jobs.id'], name=op.f('fk_ai_confirmations_job_id_ai_jobs')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_ai_confirmations_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_confirmations'))
    )
    op.create_table('billing_invoices',
    sa.Column('subscription_id', sa.Uuid(), nullable=False),
    sa.Column('invoice_number', sa.String(length=80), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_billing_invoices_organization_id_organizations')),
    sa.ForeignKeyConstraint(['subscription_id'], ['organization_subscriptions.id'], name=op.f('fk_billing_invoices_subscription_id_organization_subscriptions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_billing_invoices')),
    sa.UniqueConstraint('organization_id', 'invoice_number', name=op.f('uq_billing_invoices_organization_id'))
    )
    op.create_table('catalog_aliases',
    sa.Column('catalog_product_id', sa.Uuid(), nullable=False),
    sa.Column('alias', sa.String(length=240), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['catalog_product_id'], ['catalog_products.id'], name=op.f('fk_catalog_aliases_catalog_product_id_catalog_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_catalog_aliases')),
    sa.UniqueConstraint('catalog_product_id', 'alias', name=op.f('uq_catalog_aliases_catalog_product_id'))
    )
    op.create_table('catalog_barcodes',
    sa.Column('catalog_product_id', sa.Uuid(), nullable=False),
    sa.Column('barcode', sa.String(length=64), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['catalog_product_id'], ['catalog_products.id'], name=op.f('fk_catalog_barcodes_catalog_product_id_catalog_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_catalog_barcodes')),
    sa.UniqueConstraint('barcode', name=op.f('uq_catalog_barcodes_barcode'))
    )
    op.create_table('catalog_product_ingredients',
    sa.Column('catalog_product_id', sa.Uuid(), nullable=False),
    sa.Column('active_ingredient_id', sa.Uuid(), nullable=False),
    sa.Column('strength', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('unit', sa.String(length=30), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['active_ingredient_id'], ['active_ingredients.id'], name=op.f('fk_catalog_product_ingredients_active_ingredient_id_active_ingredients')),
    sa.ForeignKeyConstraint(['catalog_product_id'], ['catalog_products.id'], name=op.f('fk_catalog_product_ingredients_catalog_product_id_catalog_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_catalog_product_ingredients')),
    sa.UniqueConstraint('catalog_product_id', 'active_ingredient_id', name=op.f('uq_catalog_product_ingredients_catalog_product_id'))
    )
    op.create_table('catalog_revisions',
    sa.Column('catalog_product_id', sa.Uuid(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('changed_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['catalog_product_id'], ['catalog_products.id'], name=op.f('fk_catalog_revisions_catalog_product_id_catalog_products')),
    sa.ForeignKeyConstraint(['changed_by_user_id'], ['users.id'], name=op.f('fk_catalog_revisions_changed_by_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_catalog_revisions')),
    sa.UniqueConstraint('catalog_product_id', 'revision', name=op.f('uq_catalog_revisions_catalog_product_id'))
    )
    op.create_table('customer_addresses',
    sa.Column('customer_id', sa.Uuid(), nullable=False),
    sa.Column('label', sa.String(length=40), nullable=False),
    sa.Column('address_line', sa.String(length=300), nullable=False),
    sa.Column('city', sa.String(length=100), nullable=True),
    sa.Column('postal_code', sa.String(length=20), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_customer_addresses_customer_id_customers')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_customer_addresses_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customer_addresses'))
    )
    op.create_table('daily_store_metrics',
    sa.Column('metric_date', sa.Date(), nullable=False),
    sa.Column('sales_total', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('refund_total', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('cost_total', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('payment_breakdown', sa.JSON(), nullable=False),
    sa.Column('rebuilt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_daily_store_metrics_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_daily_store_metrics_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_daily_store_metrics')),
    sa.UniqueConstraint('store_id', 'metric_date', name=op.f('uq_daily_store_metrics_store_id'))
    )
    op.create_index('ix_daily_metrics_scope_date', 'daily_store_metrics', ['organization_id', 'metric_date'], unique=False)
    op.create_table('loyalty_accounts',
    sa.Column('customer_id', sa.Uuid(), nullable=False),
    sa.Column('balance', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_loyalty_accounts_customer_id_customers')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_loyalty_accounts_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_loyalty_accounts')),
    sa.UniqueConstraint('organization_id', 'customer_id', name=op.f('uq_loyalty_accounts_organization_id'))
    )
    op.create_table('orders',
    sa.Column('customer_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.Enum('PENDING', 'RESERVED', 'ACCEPTED', 'PREPARING', 'READY', 'COMPLETED', 'CANCELLED', name='orderstatus'), nullable=False),
    sa.Column('subtotal', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('total', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('delivery_address', sa.JSON(), nullable=True),
    sa.Column('prescription_required', sa.Boolean(), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_orders_customer_id_customers')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_orders_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_orders_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders')),
    sa.UniqueConstraint('organization_id', 'idempotency_key', name=op.f('uq_orders_organization_id'))
    )
    op.create_table('payments',
    sa.Column('reference_type', sa.String(length=40), nullable=False),
    sa.Column('reference_id', sa.Uuid(), nullable=False),
    sa.Column('customer_id', sa.Uuid(), nullable=True),
    sa.Column('method', sa.Enum('CASH', 'BKASH', 'NAGAD', 'DUE', name='paymentmethod'), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('received_amount', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('status', sa.Enum('PENDING', 'CAPTURED', 'FAILED', 'REFUNDED', name='paymentstatus'), nullable=False),
    sa.Column('provider_reference', sa.String(length=160), nullable=True),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_payments_customer_id_customers')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_payments_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_payments_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payments')),
    sa.UniqueConstraint('organization_id', 'idempotency_key', name=op.f('uq_payments_organization_id'))
    )
    op.create_index('ix_payments_reference', 'payments', ['reference_type', 'reference_id'], unique=False)
    op.create_table('pharmacy_products',
    sa.Column('catalog_product_id', sa.Uuid(), nullable=True),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('barcode', sa.String(length=64), nullable=True),
    sa.Column('unit', sa.String(length=40), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['catalog_product_id'], ['catalog_products.id'], name=op.f('fk_pharmacy_products_catalog_product_id_catalog_products')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_pharmacy_products_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pharmacy_products')),
    sa.UniqueConstraint('organization_id', 'barcode', name=op.f('uq_pharmacy_products_organization_id'))
    )
    op.create_index('ix_pharmacy_products_org_name', 'pharmacy_products', ['organization_id', 'name'], unique=False)
    op.create_table('purchases',
    sa.Column('supplier_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'CONFIRMED', 'CANCELLED', 'RETURNED', name='purchasestatus'), nullable=False),
    sa.Column('invoice_number', sa.String(length=100), nullable=True),
    sa.Column('total_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('purchased_at', sa.Date(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_purchases_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_purchases_store_id_stores')),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], name=op.f('fk_purchases_supplier_id_suppliers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_purchases')),
    sa.UniqueConstraint('organization_id', 'idempotency_key', name=op.f('uq_purchases_organization_id'))
    )
    op.create_table('sales',
    sa.Column('customer_id', sa.Uuid(), nullable=True),
    sa.Column('order_id', sa.Uuid(), nullable=True),
    sa.Column('channel', sa.Enum('POS', 'ONLINE', name='salechannel'), nullable=False),
    sa.Column('status', sa.Enum('COMPLETED', 'VOIDED', 'REFUNDED', name='salestatus'), nullable=False),
    sa.Column('subtotal', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('discount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('total', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('receipt_number', sa.String(length=80), nullable=True),
    sa.Column('void_reason', sa.Text(), nullable=True),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_sales_customer_id_customers')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_sales_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_sales_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sales')),
    sa.UniqueConstraint('organization_id', 'idempotency_key', name=op.f('uq_sales_organization_id'))
    )
    op.create_table('store_expenses',
    sa.Column('category', sa.String(length=100), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('expense_date', sa.Date(), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], name=op.f('fk_store_expenses_created_by_user_id_users')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_store_expenses_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_store_expenses_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_store_expenses'))
    )
    op.create_table('store_sequences',
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('last_sequence', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_store_sequences_store_id_stores')),
    sa.PrimaryKeyConstraint('store_id', name=op.f('pk_store_sequences'))
    )
    op.create_table('storefronts',
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('display_name', sa.String(length=160), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('custom_domain', sa.String(length=255), nullable=True),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_storefronts_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_storefronts_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_storefronts')),
    sa.UniqueConstraint('custom_domain', name=op.f('uq_storefronts_custom_domain')),
    sa.UniqueConstraint('organization_id', 'slug', name=op.f('uq_storefronts_organization_id')),
    sa.UniqueConstraint('store_id', 'slug', name=op.f('uq_storefronts_store_id'))
    )
    op.create_table('supplier_ledger_entries',
    sa.Column('supplier_id', sa.Uuid(), nullable=False),
    sa.Column('entry_type', sa.String(length=40), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('reference_type', sa.String(length=80), nullable=True),
    sa.Column('reference_id', sa.Uuid(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_supplier_ledger_entries_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_supplier_ledger_entries_store_id_stores')),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], name=op.f('fk_supplier_ledger_entries_supplier_id_suppliers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_supplier_ledger_entries'))
    )
    op.create_index('ix_supplier_ledger_scope_supplier', 'supplier_ledger_entries', ['organization_id', 'supplier_id', 'created_at'], unique=False)
    op.create_table('loyalty_transactions',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('transaction_type', sa.Enum('EARN', 'REDEEM', 'REFUND', 'BONUS', 'ADJUST', 'EXPIRE', name='loyaltytransactiontype'), nullable=False),
    sa.Column('points', sa.Integer(), nullable=False),
    sa.Column('source_type', sa.String(length=80), nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['loyalty_accounts.id'], name=op.f('fk_loyalty_transactions_account_id_loyalty_accounts')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_loyalty_transactions_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_loyalty_transactions')),
    sa.UniqueConstraint('organization_id', 'idempotency_key', name=op.f('uq_loyalty_transactions_organization_id'))
    )
    op.create_table('order_status_history',
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('from_status', sa.Enum('PENDING', 'RESERVED', 'ACCEPTED', 'PREPARING', 'READY', 'COMPLETED', 'CANCELLED', name='orderstatus'), nullable=True),
    sa.Column('to_status', sa.Enum('PENDING', 'RESERVED', 'ACCEPTED', 'PREPARING', 'READY', 'COMPLETED', 'CANCELLED', name='orderstatus'), nullable=False),
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_order_status_history_actor_user_id_users')),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_order_status_history_order_id_orders')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_order_status_history_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_order_status_history_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_status_history'))
    )
    op.create_table('payment_refunds',
    sa.Column('payment_id', sa.Uuid(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('provider_reference', sa.String(length=160), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_payment_refunds_organization_id_organizations')),
    sa.ForeignKeyConstraint(['payment_id'], ['payments.id'], name=op.f('fk_payment_refunds_payment_id_payments')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_payment_refunds_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payment_refunds'))
    )
    op.create_table('prescriptions',
    sa.Column('customer_id', sa.Uuid(), nullable=True),
    sa.Column('order_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', 'NEEDS_CLARIFICATION', name='prescriptionstatus'), nullable=False),
    sa.Column('prescriber_name', sa.String(length=160), nullable=True),
    sa.Column('prescription_number', sa.String(length=100), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_prescriptions_customer_id_customers')),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_prescriptions_order_id_orders')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_prescriptions_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_prescriptions'))
    )
    op.create_table('sale_returns',
    sa.Column('sale_id', sa.Uuid(), nullable=False),
    sa.Column('reason', sa.String(length=240), nullable=False),
    sa.Column('total', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_sale_returns_organization_id_organizations')),
    sa.ForeignKeyConstraint(['sale_id'], ['sales.id'], name=op.f('fk_sale_returns_sale_id_sales')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_sale_returns_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sale_returns'))
    )
    op.create_table('store_products',
    sa.Column('pharmacy_product_id', sa.Uuid(), nullable=False),
    sa.Column('sku', sa.String(length=64), nullable=False),
    sa.Column('sale_price', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('minimum_stock', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('rack', sa.String(length=80), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_store_products_organization_id_organizations')),
    sa.ForeignKeyConstraint(['pharmacy_product_id'], ['pharmacy_products.id'], name=op.f('fk_store_products_pharmacy_product_id_pharmacy_products')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_store_products_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_store_products')),
    sa.UniqueConstraint('store_id', 'pharmacy_product_id', name=op.f('uq_store_products_store_id')),
    sa.UniqueConstraint('store_id', 'sku', name=op.f('uq_store_products_store_id'))
    )
    op.create_index('ix_store_products_scope', 'store_products', ['organization_id', 'store_id'], unique=False)
    op.create_table('supplier_products',
    sa.Column('supplier_id', sa.Uuid(), nullable=False),
    sa.Column('pharmacy_product_id', sa.Uuid(), nullable=False),
    sa.Column('supplier_sku', sa.String(length=80), nullable=True),
    sa.Column('preferred', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['pharmacy_product_id'], ['pharmacy_products.id'], name=op.f('fk_supplier_products_pharmacy_product_id_pharmacy_products')),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], name=op.f('fk_supplier_products_supplier_id_suppliers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_supplier_products')),
    sa.UniqueConstraint('supplier_id', 'pharmacy_product_id', name=op.f('uq_supplier_products_supplier_id'))
    )
    op.create_table('sync_checkpoints',
    sa.Column('device_id', sa.Uuid(), nullable=False),
    sa.Column('last_server_sequence', sa.BigInteger(), nullable=False),
    sa.Column('last_client_sequence', sa.BigInteger(), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], name=op.f('fk_sync_checkpoints_device_id_devices')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_sync_checkpoints_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_sync_checkpoints_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sync_checkpoints')),
    sa.UniqueConstraint('store_id', 'device_id', name=op.f('uq_sync_checkpoints_store_id'))
    )
    op.create_table('sync_events',
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('device_id', sa.Uuid(), nullable=False),
    sa.Column('event_id', sa.Uuid(), nullable=False),
    sa.Column('event_type', sa.String(length=120), nullable=False),
    sa.Column('client_sequence', sa.BigInteger(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('applied', sa.Boolean(), nullable=False),
    sa.Column('error_code', sa.String(length=80), nullable=True),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], name=op.f('fk_sync_events_device_id_devices')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_sync_events_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_sync_events_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sync_events')),
    sa.UniqueConstraint('organization_id', 'event_id', name=op.f('uq_sync_events_organization_id'))
    )
    op.create_table('ecommerce_product_settings',
    sa.Column('store_product_id', sa.Uuid(), nullable=False),
    sa.Column('online_name', sa.String(length=240), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('online_price', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('listed', sa.Boolean(), nullable=False),
    sa.Column('pickup_enabled', sa.Boolean(), nullable=False),
    sa.Column('delivery_enabled', sa.Boolean(), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_ecommerce_product_settings_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_ecommerce_product_settings_store_id_stores')),
    sa.ForeignKeyConstraint(['store_product_id'], ['store_products.id'], name=op.f('fk_ecommerce_product_settings_store_product_id_store_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ecommerce_product_settings')),
    sa.UniqueConstraint('store_id', 'store_product_id', name=op.f('uq_ecommerce_product_settings_store_id'))
    )
    op.create_table('inventory_balances',
    sa.Column('store_product_id', sa.Uuid(), nullable=False),
    sa.Column('on_hand', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('reserved', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_inventory_balances_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_inventory_balances_store_id_stores')),
    sa.ForeignKeyConstraint(['store_product_id'], ['store_products.id'], name=op.f('fk_inventory_balances_store_product_id_store_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_inventory_balances')),
    sa.UniqueConstraint('store_id', 'store_product_id', name=op.f('uq_inventory_balances_store_id'))
    )
    op.create_table('inventory_batches',
    sa.Column('store_product_id', sa.Uuid(), nullable=False),
    sa.Column('batch_number', sa.String(length=100), nullable=False),
    sa.Column('expiry_date', sa.Date(), nullable=True),
    sa.Column('unit_cost', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_inventory_batches_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_inventory_batches_store_id_stores')),
    sa.ForeignKeyConstraint(['store_product_id'], ['store_products.id'], name=op.f('fk_inventory_batches_store_product_id_store_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_inventory_batches'))
    )
    op.create_index('ix_inventory_batches_fefo', 'inventory_batches', ['store_id', 'store_product_id', 'expiry_date'], unique=False)
    op.create_table('order_items',
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('store_product_id', sa.Uuid(), nullable=False),
    sa.Column('product_name', sa.String(length=240), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('line_total', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_order_items_order_id_orders')),
    sa.ForeignKeyConstraint(['store_product_id'], ['store_products.id'], name=op.f('fk_order_items_store_product_id_store_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_items'))
    )
    op.create_table('prescription_files',
    sa.Column('prescription_id', sa.Uuid(), nullable=False),
    sa.Column('object_key', sa.String(length=500), nullable=False),
    sa.Column('content_type', sa.String(length=100), nullable=False),
    sa.Column('checksum', sa.String(length=128), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_prescription_files_organization_id_organizations')),
    sa.ForeignKeyConstraint(['prescription_id'], ['prescriptions.id'], name=op.f('fk_prescription_files_prescription_id_prescriptions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_prescription_files'))
    )
    op.create_table('prescription_reviews',
    sa.Column('prescription_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', 'NEEDS_CLARIFICATION', name='prescriptionstatus'), nullable=False),
    sa.Column('pharmacist_user_id', sa.Uuid(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_prescription_reviews_organization_id_organizations')),
    sa.ForeignKeyConstraint(['pharmacist_user_id'], ['users.id'], name=op.f('fk_prescription_reviews_pharmacist_user_id_users')),
    sa.ForeignKeyConstraint(['prescription_id'], ['prescriptions.id'], name=op.f('fk_prescription_reviews_prescription_id_prescriptions')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_prescription_reviews_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_prescription_reviews'))
    )
    op.create_table('purchase_items',
    sa.Column('purchase_id', sa.Uuid(), nullable=False),
    sa.Column('store_product_id', sa.Uuid(), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('unit_cost', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('batch_number', sa.String(length=100), nullable=False),
    sa.Column('expiry_date', sa.Date(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['purchase_id'], ['purchases.id'], name=op.f('fk_purchase_items_purchase_id_purchases')),
    sa.ForeignKeyConstraint(['store_product_id'], ['store_products.id'], name=op.f('fk_purchase_items_store_product_id_store_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_purchase_items'))
    )
    op.create_table('sale_items',
    sa.Column('sale_id', sa.Uuid(), nullable=False),
    sa.Column('store_product_id', sa.Uuid(), nullable=False),
    sa.Column('product_name', sa.String(length=240), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('line_total', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['sale_id'], ['sales.id'], name=op.f('fk_sale_items_sale_id_sales')),
    sa.ForeignKeyConstraint(['store_product_id'], ['store_products.id'], name=op.f('fk_sale_items_store_product_id_store_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sale_items'))
    )
    op.create_table('store_product_prices',
    sa.Column('store_product_id', sa.Uuid(), nullable=False),
    sa.Column('price', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('effective_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_store_product_prices_actor_user_id_users')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_store_product_prices_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_store_product_prices_store_id_stores')),
    sa.ForeignKeyConstraint(['store_product_id'], ['store_products.id'], name=op.f('fk_store_product_prices_store_product_id_store_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_store_product_prices'))
    )
    op.create_index('ix_product_price_history', 'store_product_prices', ['store_product_id', 'effective_at'], unique=False)
    op.create_table('sync_feed_items',
    sa.Column('device_id', sa.Uuid(), nullable=True),
    sa.Column('sync_event_id', sa.Uuid(), nullable=True),
    sa.Column('event_type', sa.String(length=120), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('server_sequence', sa.BigInteger(), nullable=False),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], name=op.f('fk_sync_feed_items_device_id_devices')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_sync_feed_items_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_sync_feed_items_store_id_stores')),
    sa.ForeignKeyConstraint(['sync_event_id'], ['sync_events.id'], name=op.f('fk_sync_feed_items_sync_event_id_sync_events')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sync_feed_items')),
    sa.UniqueConstraint('store_id', 'server_sequence', name=op.f('uq_sync_feed_items_store_id'))
    )
    op.create_table('inventory_movements',
    sa.Column('store_product_id', sa.Uuid(), nullable=False),
    sa.Column('batch_id', sa.Uuid(), nullable=True),
    sa.Column('movement_type', sa.Enum('RECEIPT', 'SALE', 'RETURN', 'ADJUSTMENT', 'DAMAGE', 'TRANSFER', name='inventorymovementtype'), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('reference_type', sa.String(length=80), nullable=True),
    sa.Column('reference_id', sa.Uuid(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_inventory_movements_actor_user_id_users')),
    sa.ForeignKeyConstraint(['batch_id'], ['inventory_batches.id'], name=op.f('fk_inventory_movements_batch_id_inventory_batches')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_inventory_movements_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_inventory_movements_store_id_stores')),
    sa.ForeignKeyConstraint(['store_product_id'], ['store_products.id'], name=op.f('fk_inventory_movements_store_product_id_store_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_inventory_movements'))
    )
    op.create_index('ix_inventory_movement_product_time', 'inventory_movements', ['store_id', 'store_product_id', 'occurred_at'], unique=False)
    op.create_table('sale_item_batch_allocations',
    sa.Column('sale_item_id', sa.Uuid(), nullable=False),
    sa.Column('batch_id', sa.Uuid(), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['inventory_batches.id'], name=op.f('fk_sale_item_batch_allocations_batch_id_inventory_batches')),
    sa.ForeignKeyConstraint(['sale_item_id'], ['sale_items.id'], name=op.f('fk_sale_item_batch_allocations_sale_item_id_sale_items')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sale_item_batch_allocations'))
    )
    op.create_table('stock_reservations',
    sa.Column('store_product_id', sa.Uuid(), nullable=False),
    sa.Column('batch_id', sa.Uuid(), nullable=False),
    sa.Column('reference_type', sa.String(length=80), nullable=False),
    sa.Column('reference_id', sa.Uuid(), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('store_id', sa.Uuid(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['inventory_batches.id'], name=op.f('fk_stock_reservations_batch_id_inventory_batches')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_stock_reservations_organization_id_organizations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_stock_reservations_store_id_stores')),
    sa.ForeignKeyConstraint(['store_product_id'], ['store_products.id'], name=op.f('fk_stock_reservations_store_product_id_store_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stock_reservations')),
    sa.UniqueConstraint('store_id', 'reference_type', 'reference_id', 'batch_id', name=op.f('uq_stock_reservations_store_id'))
    )
    op.create_index('ix_audit_org_created', 'audit_logs', ['organization_id', 'created_at'], unique=False)
    # 0001 created these as bare VARCHAR(20)/(30); the models declare enum_column()
    # (VARCHAR(40) + CHECK). batch_alter_table emits a plain ALTER on PostgreSQL and a
    # copy-and-recreate on SQLite, which cannot alter a column type in place.
    for table, column, enum_type in _WIDENED_ENUMS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, type_=enum_type, existing_nullable=False)
    op.create_index('ix_org_users_org', 'organization_users', ['organization_id'], unique=False)
    op.create_index('ix_outbox_pending', 'outbox_events', ['published_at', 'created_at'], unique=False)
    op.create_index('ix_store_users_store', 'store_users', ['store_id'], unique=False)


def downgrade() -> None:
    for table, column, _enum_type in reversed(_WIDENED_ENUMS):
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column, type_=sa.VARCHAR(length=30 if column == "role" else 20), existing_nullable=False
            )
    op.drop_index('ix_store_users_store', table_name='store_users')
    op.drop_index('ix_outbox_pending', table_name='outbox_events')
    op.drop_index('ix_org_users_org', table_name='organization_users')
    op.drop_index('ix_audit_org_created', table_name='audit_logs')
    op.drop_table('stock_reservations')
    op.drop_table('sale_item_batch_allocations')
    op.drop_index('ix_inventory_movement_product_time', table_name='inventory_movements')
    op.drop_table('inventory_movements')
    op.drop_table('sync_feed_items')
    op.drop_index('ix_product_price_history', table_name='store_product_prices')
    op.drop_table('store_product_prices')
    op.drop_table('sale_items')
    op.drop_table('purchase_items')
    op.drop_table('prescription_reviews')
    op.drop_table('prescription_files')
    op.drop_table('order_items')
    op.drop_index('ix_inventory_batches_fefo', table_name='inventory_batches')
    op.drop_table('inventory_batches')
    op.drop_table('inventory_balances')
    op.drop_table('ecommerce_product_settings')
    op.drop_table('sync_events')
    op.drop_table('sync_checkpoints')
    op.drop_table('supplier_products')
    op.drop_index('ix_store_products_scope', table_name='store_products')
    op.drop_table('store_products')
    op.drop_table('sale_returns')
    op.drop_table('prescriptions')
    op.drop_table('payment_refunds')
    op.drop_table('order_status_history')
    op.drop_table('loyalty_transactions')
    op.drop_index('ix_supplier_ledger_scope_supplier', table_name='supplier_ledger_entries')
    op.drop_table('supplier_ledger_entries')
    op.drop_table('storefronts')
    op.drop_table('store_sequences')
    op.drop_table('store_expenses')
    op.drop_table('sales')
    op.drop_table('purchases')
    op.drop_index('ix_pharmacy_products_org_name', table_name='pharmacy_products')
    op.drop_table('pharmacy_products')
    op.drop_index('ix_payments_reference', table_name='payments')
    op.drop_table('payments')
    op.drop_table('orders')
    op.drop_table('loyalty_accounts')
    op.drop_index('ix_daily_metrics_scope_date', table_name='daily_store_metrics')
    op.drop_table('daily_store_metrics')
    op.drop_table('customer_addresses')
    op.drop_table('catalog_revisions')
    op.drop_table('catalog_product_ingredients')
    op.drop_table('catalog_barcodes')
    op.drop_table('catalog_aliases')
    op.drop_table('billing_invoices')
    op.drop_table('ai_confirmations')
    op.drop_table('suppliers')
    op.drop_table('organization_subscriptions')
    op.drop_index('ix_customers_org_name', table_name='customers')
    op.drop_table('customers')
    op.drop_index('ix_catalog_product_name', table_name='catalog_products')
    op.drop_table('catalog_products')
    op.drop_table('ai_jobs')
    op.drop_table('manufacturers')
    op.drop_table('dosage_forms')
    op.drop_table('billing_plans')
    op.drop_table('active_ingredients')
