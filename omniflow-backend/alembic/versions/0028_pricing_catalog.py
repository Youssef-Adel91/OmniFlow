"""Pricing catalogue (P10-A): Starter / Pro / Business / Enterprise plans, seat + WhatsApp-number add-ons.

Numbers come from the owner's PRICING_STUDY (VAT-exclusive, SAR; yearly = 10 x monthly). The legacy placeholder plans
(economic / professional) are kept — existing subscriptions point at them — but deactivated so they are not sellable.
`enterprise` becomes a "contact us" plan (never purchasable online).

Revision ID: 0028_pricing_catalog
Revises: 0027_launch_gaps
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0028_pricing_catalog"
down_revision = "0027_launch_gaps"
branch_labels = None
depends_on = None

_PLANS = [
    # code, name_ar, description, monthly, yearly (halalas, VAT-exclusive), limits, sort
    ("starter", "الأساسية", "للمكاتب الصغيرة: ردود ذكاء اصطناعي ومقعدان ورقم واتساب.", 29900, 299000,
     {"messages_per_month": 1500, "seats": 2, "whatsapp_numbers": 1, "properties": 200, "contacts": 1000, "campaigns": False}, 1),
    ("pro", "الاحترافية", "للمكاتب النشطة: حملات تسويقية وحدود أعلى.", 79900, 799000,
     {"messages_per_month": 8000, "seats": 5, "whatsapp_numbers": 1, "properties": 1000, "contacts": 10000, "campaigns": True}, 2),
    ("business", "الأعمال", "للشركات المتوسطة: رقمان للواتساب وفريق أكبر.", 179900, 1799000,
     {"messages_per_month": 25000, "seats": 15, "whatsapp_numbers": 2, "properties": 10000, "contacts": 50000, "campaigns": True}, 3),
]


def upgrade() -> None:
    uuid_pg = sa.dialects.postgresql.UUID(as_uuid=True)
    now = sa.func.now()

    op.add_column("plans", sa.Column("contact_sales", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("subscriptions", sa.Column("addon_seats", sa.Integer, nullable=False, server_default="0"))
    op.add_column("subscriptions", sa.Column("addon_whatsapp_numbers", sa.Integer, nullable=False, server_default="0"))

    op.create_table(
        "plan_addons",
        sa.Column("code", sa.String(30), primary_key=True),                       # seat | whatsapp_number
        sa.Column("name_ar", sa.String(100), nullable=False),
        sa.Column("price_monthly_halalas", sa.Integer, nullable=False),          # VAT-exclusive; yearly = 10 x monthly
        sa.Column("limit_key", sa.String(30), nullable=False),                   # which plan limit it raises
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_available", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("unavailable_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    bind = op.get_bind()
    ins = sa.text("INSERT INTO plans (code, name_ar, description_ar, price_monthly_halalas, price_yearly_halalas, limits, sort_order, is_placeholder) "
                  "VALUES (:c, :n, :d, :m, :y, CAST(:l AS jsonb), :s, false)")
    for code, name, desc, monthly, yearly, limits, sort in _PLANS:
        bind.execute(ins, {"c": code, "n": name, "d": desc, "m": monthly, "y": yearly, "l": json.dumps(limits), "s": sort})

    op.execute("UPDATE plans SET is_active = false WHERE code IN ('economic', 'professional')")
    op.execute("UPDATE plans SET name_ar = 'المؤسسات', description_ar = 'للمؤسسات الكبيرة — تواصل معنا لعرض مخصص.', "
               "price_monthly_halalas = 0, price_yearly_halalas = 0, limits = '{}'::jsonb, contact_sales = true, "
               "is_placeholder = false, sort_order = 4 WHERE code = 'enterprise'")

    bind.execute(sa.text(
        "INSERT INTO plan_addons (code, name_ar, price_monthly_halalas, limit_key, is_available, unavailable_reason) VALUES "
        "('seat', 'مقعد إضافي', 4900, 'seats', true, NULL), "
        "('whatsapp_number', 'رقم واتساب إضافي', 9900, 'whatsapp_numbers', false, "
        "'النظام يدعم حاليًا رقم واتساب واحدًا لكل منشأة؛ تفعيل الأرقام المتعددة يحتاج تطويرًا مستقلًا.')"))


def downgrade() -> None:
    op.drop_table("plan_addons")
    op.drop_column("subscriptions", "addon_whatsapp_numbers")
    op.drop_column("subscriptions", "addon_seats")
    op.drop_column("plans", "contact_sales")
    op.execute("DELETE FROM plans WHERE code IN ('starter', 'pro', 'business')")
    op.execute("UPDATE plans SET is_active = true WHERE code IN ('economic', 'professional')")
    op.execute("UPDATE plans SET name_ar = 'المؤسسات', description_ar = 'خطة تجريبية — السعر والحدود غير نهائية', "
               "price_monthly_halalas = 79900, price_yearly_halalas = 799000, "
               "limits = '{\"seats\": 50, \"messages_per_month\": 20000, \"channels\": 5, \"properties\": 10000}'::jsonb, "
               "is_placeholder = true, sort_order = 3 WHERE code = 'enterprise'")
