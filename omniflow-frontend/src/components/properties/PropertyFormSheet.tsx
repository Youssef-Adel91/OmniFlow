"use client";

/**
 * components/properties/PropertyFormSheet.tsx
 *
 * Slide-over Sheet containing the Add/Edit property listing form.
 *
 * Features:
 *   - react-hook-form + zod validation (Arabic error messages)
 *   - Supports both CREATE (no initialData) and EDIT (initialData set) modes
 *   - All labels bilingual (Arabic primary, RTL layout)
 *   - Luxury Dark Theme (Navy #0B1121 / Gold #C9A84C)
 *   - Smooth slide-in animation via CSS transform
 */

import React, { useEffect, useState } from "react";
import { useForm, type SubmitHandler } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  type PropertyListing,
  type PropertyListingCreate,
  type PropertyType,
  type ListingStatus,
  PROPERTY_TYPE_LABELS,
  LISTING_STATUS_LABELS,
  createProperty,
  updateProperty,
} from "@/lib/api/properties";

// ── Zod schema ─────────────────────────────────────────────────────────────

const formSchema = z.object({
  rega_ad_number: z.string().max(30).optional().or(z.literal("")),
  property_type: z.enum([
    "apartment", "villa", "land", "commercial",
    "daily_rental", "office", "warehouse",
  ] as const),
  status: z.enum([
    "PENDING_VERIFICATION", "VERIFIED_ACTIVE", "VERIFICATION_FAILED",
    "SUSPENDED", "SOLD", "RENTED", "WITHDRAWN",
  ] as const),
  city: z.string().max(100).optional().or(z.literal("")),
  district: z.string().max(100).optional().or(z.literal("")),
  price: z.preprocess(
    (v: unknown) => (v === "" || v === null || v === undefined ? null : Number(v)),
    z.number().min(0).nullable().optional()
  ),
  area_sqm: z.preprocess(
    (v: unknown) => (v === "" || v === null || v === undefined ? null : Number(v)),
    z.number().min(0).nullable().optional()
  ),
  bedrooms: z.preprocess(
    (v: unknown) => (v === "" || v === null || v === undefined ? null : Number(v)),
    z.number().int().min(0).max(100).nullable().optional()
  ),
  bathrooms: z.preprocess(
    (v: unknown) => (v === "" || v === null || v === undefined ? null : Number(v)),
    z.number().int().min(0).max(50).nullable().optional()
  ),
  description_ar: z.string().optional().or(z.literal("")),
  description_en: z.string().optional().or(z.literal("")),
});

type FormValues = z.infer<typeof formSchema>;

// ── Props ───────────────────────────────────────────────────────────────────

interface PropertyFormSheetProps {
  open: boolean;
  onClose: () => void;
  onSuccess: (listing: PropertyListing) => void;
  initialData?: PropertyListing | null;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function InputField({
  id,
  label,
  type = "text",
  placeholder,
  error,
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement> & {
  id: string;
  label: string;
  error?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <label htmlFor={id} style={styles.label}>{label}</label>
      <input
        id={id}
        type={type}
        placeholder={placeholder}
        style={{
          ...styles.input,
          ...(error ? styles.inputError : {}),
        }}
        {...rest}
      />
      {error && <span style={styles.errorText}>{error}</span>}
    </div>
  );
}

function SelectField({
  id,
  label,
  children,
  error,
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement> & {
  id: string;
  label: string;
  error?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <label htmlFor={id} style={styles.label}>{label}</label>
      <select
        id={id}
        style={{
          ...styles.input,
          ...(error ? styles.inputError : {}),
          appearance: "none",
          backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23C9A84C' d='M6 8L1 3h10z'/%3E%3C/svg%3E")`,
          backgroundRepeat: "no-repeat",
          backgroundPosition: "left 12px center",
          paddingLeft: "36px",
        }}
        {...rest}
      >
        {children}
      </select>
      {error && <span style={styles.errorText}>{error}</span>}
    </div>
  );
}

function TextAreaField({
  id,
  label,
  error,
  ...rest
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & {
  id: string;
  label: string;
  error?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <label htmlFor={id} style={styles.label}>{label}</label>
      <textarea
        id={id}
        rows={3}
        style={{
          ...styles.input,
          ...(error ? styles.inputError : {}),
          resize: "vertical",
          minHeight: "80px",
        }}
        {...rest}
      />
      {error && <span style={styles.errorText}>{error}</span>}
    </div>
  );
}

// ── Main Component ──────────────────────────────────────────────────────────

export function PropertyFormSheet({
  open,
  onClose,
  onSuccess,
  initialData,
}: PropertyFormSheetProps) {
  const isEdit = Boolean(initialData);
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(formSchema) as any,
    defaultValues: {
      rega_ad_number: initialData?.rega_ad_number ?? "",
      property_type:  (initialData?.property_type as PropertyType) ?? "apartment",
      status:         (initialData?.status as ListingStatus) ?? "PENDING_VERIFICATION",
      city:           initialData?.city ?? "",
      district:       initialData?.district ?? "",
      price:          initialData?.price ?? undefined,
      area_sqm:       initialData?.area_sqm ?? undefined,
      bedrooms:       initialData?.bedrooms ?? undefined,
      bathrooms:      initialData?.bathrooms ?? undefined,
      description_ar: initialData?.description_ar ?? "",
      description_en: initialData?.description_en ?? "",
    },
  });

  // Reset form whenever initialData changes (switching between edit / create)
  useEffect(() => {
    if (open) {
      reset({
        rega_ad_number: initialData?.rega_ad_number ?? "",
        property_type:  (initialData?.property_type as PropertyType) ?? "apartment",
        status:         (initialData?.status as ListingStatus) ?? "PENDING_VERIFICATION",
        city:           initialData?.city ?? "",
        district:       initialData?.district ?? "",
        price:          initialData?.price ?? undefined,
        area_sqm:       initialData?.area_sqm ?? undefined,
        bedrooms:       initialData?.bedrooms ?? undefined,
        bathrooms:      initialData?.bathrooms ?? undefined,
        description_ar: initialData?.description_ar ?? "",
        description_en: initialData?.description_en ?? "",
      });
      setServerError(null);
    }
  }, [open, initialData, reset]);

  const onSubmit: SubmitHandler<FormValues> = async (values) => {
    setServerError(null);
    try {
      const payload: PropertyListingCreate = {
        rega_ad_number: values.rega_ad_number || undefined,
        property_type:  values.property_type,
        status:         values.status,
        city:           values.city || null,
        district:       values.district || null,
        price:          values.price ?? null,
        area_sqm:       values.area_sqm ?? null,
        bedrooms:       values.bedrooms ?? null,
        bathrooms:      values.bathrooms ?? null,
        description_ar: values.description_ar || null,
        description_en: values.description_en || null,
      };

      let result: PropertyListing;
      if (isEdit && initialData) {
        result = await updateProperty(initialData.listing_id, payload);
      } else {
        result = await createProperty(payload);
      }
      reset();
      onSuccess(result);
      onClose();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      const msg =
        typeof detail === "string"
          ? detail
          : detail?.message ?? err?.message ?? "حدث خطأ أثناء الحفظ";
      setServerError(msg);
    }
  };

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={styles.backdrop}
        aria-hidden="true"
      />

      {/* Sheet Panel */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={isEdit ? "تعديل عقار" : "إضافة عقار جديد"}
        dir="rtl"
        style={styles.sheet}
      >
        {/* Header */}
        <div style={styles.sheetHeader}>
          <div>
            <h2 style={styles.sheetTitle}>
              {isEdit ? "✏️ تعديل العقار" : "🏠 إضافة عقار جديد"}
            </h2>
            <p style={styles.sheetSubtitle}>
              {isEdit
                ? "قم بتعديل بيانات العقار وحفظ التغييرات"
                : "أدخل بيانات العقار الجديد — رقم الإعلان اختياري"}
            </p>
          </div>
          <button
            onClick={onClose}
            style={styles.closeBtn}
            aria-label="إغلاق"
          >
            ✕
          </button>
        </div>

        {/* Divider */}
        <div style={styles.divider} />

        {/* Form */}
        <form
          onSubmit={handleSubmit(onSubmit)}
          style={styles.formBody}
          noValidate
        >
          {/* Server error banner */}
          {serverError && (
            <div style={styles.errorBanner} role="alert">
              ⚠️ {serverError}
            </div>
          )}

          {/* Section: Basic Info */}
          <div style={styles.sectionLabel}>المعلومات الأساسية</div>
          <div style={styles.grid2}>
            <SelectField
              id="property_type"
              label="نوع العقار *"
              error={errors.property_type?.message}
              {...register("property_type")}
            >
              {(Object.entries(PROPERTY_TYPE_LABELS) as [PropertyType, { ar: string }][]).map(
                ([val, { ar }]) => (
                  <option key={val} value={val}>{ar}</option>
                )
              )}
            </SelectField>

            <SelectField
              id="status"
              label="حالة الإعلان *"
              error={errors.status?.message}
              {...register("status")}
            >
              {(Object.entries(LISTING_STATUS_LABELS) as [ListingStatus, { ar: string }][]).map(
                ([val, { ar }]) => (
                  <option key={val} value={val}>{ar}</option>
                )
              )}
            </SelectField>
          </div>

          <InputField
            id="rega_ad_number"
            label="رقم إعلان REGA (اختياري)"
            placeholder="سيتم توليده تلقائياً إذا تُرك فارغاً"
            error={errors.rega_ad_number?.message}
            {...register("rega_ad_number")}
          />

          {/* Section: Location */}
          <div style={styles.sectionLabel}>الموقع</div>
          <div style={styles.grid2}>
            <InputField
              id="city"
              label="المدينة"
              placeholder="الرياض"
              error={errors.city?.message}
              {...register("city")}
            />
            <InputField
              id="district"
              label="الحي"
              placeholder="النرجس"
              error={errors.district?.message}
              {...register("district")}
            />
          </div>

          {/* Section: Specs & Pricing */}
          <div style={styles.sectionLabel}>المواصفات والسعر</div>
          <div style={styles.grid2}>
            <InputField
              id="price"
              label="السعر (ريال سعودي)"
              type="number"
              placeholder="850000"
              min={0}
              error={errors.price?.message}
              {...register("price")}
            />
            <InputField
              id="area_sqm"
              label="المساحة (م²)"
              type="number"
              placeholder="180"
              min={0}
              error={errors.area_sqm?.message}
              {...register("area_sqm")}
            />
            <InputField
              id="bedrooms"
              label="غرف النوم"
              type="number"
              placeholder="4"
              min={0}
              max={100}
              error={errors.bedrooms?.message}
              {...register("bedrooms")}
            />
            <InputField
              id="bathrooms"
              label="الحمامات"
              type="number"
              placeholder="3"
              min={0}
              max={50}
              error={errors.bathrooms?.message}
              {...register("bathrooms")}
            />
          </div>

          {/* Section: Description */}
          <div style={styles.sectionLabel}>الوصف (مدخل RAG)</div>
          <TextAreaField
            id="description_ar"
            label="الوصف بالعربي"
            placeholder="شقة فاخرة بإطلالة بانورامية على الحديقة ..."
            error={errors.description_ar?.message}
            {...register("description_ar")}
          />
          <TextAreaField
            id="description_en"
            label="Description in English"
            placeholder="Luxurious apartment with panoramic garden view ..."
            error={errors.description_en?.message}
            style={{ direction: "ltr", textAlign: "left" }}
            {...register("description_en")}
          />

          {/* Footer */}
          <div style={styles.divider} />
          <div style={styles.footer}>
            <button
              type="button"
              onClick={onClose}
              style={styles.cancelBtn}
              disabled={isSubmitting}
            >
              إلغاء
            </button>
            <button
              type="submit"
              style={{
                ...styles.submitBtn,
                ...(isSubmitting ? styles.submitBtnDisabled : {}),
              }}
              disabled={isSubmitting}
              id="property-form-submit"
            >
              {isSubmitting
                ? "⏳ جارٍ الحفظ..."
                : isEdit
                ? "💾 حفظ التغييرات"
                : "✅ إضافة العقار"}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}

// ── Styles ──────────────────────────────────────────────────────────────────

const NAVY     = "#0B1121";
const NAVY_700 = "#111827";
const NAVY_600 = "#1A2540";
const NAVY_500 = "#243058";
const GOLD     = "#C9A84C";
const GOLD_DIM = "#A07C30";
const TEXT     = "#E2E8F0";
const TEXT_DIM = "#8B8FA8";
const BORDER   = "rgba(201, 168, 76, 0.2)";
const RED      = "#E05252";

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.65)",
    backdropFilter: "blur(4px)",
    zIndex: 40,
  },
  sheet: {
    position: "fixed",
    top: 0,
    right: 0,
    height: "100vh",
    width: "min(540px, 95vw)",
    background: NAVY_700,
    borderLeft: `1px solid ${BORDER}`,
    boxShadow: "-8px 0 40px rgba(0,0,0,0.6)",
    zIndex: 50,
    display: "flex",
    flexDirection: "column",
    overflowY: "auto",
    animation: "slideInRight 0.28s cubic-bezier(0.22, 0.61, 0.36, 1)",
  },
  sheetHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    padding: "28px 28px 20px",
    gap: "16px",
  },
  sheetTitle: {
    margin: 0,
    fontSize: "20px",
    fontWeight: 700,
    color: GOLD,
    letterSpacing: "0.02em",
  },
  sheetSubtitle: {
    margin: "6px 0 0",
    fontSize: "13px",
    color: TEXT_DIM,
  },
  closeBtn: {
    background: "transparent",
    border: `1px solid ${BORDER}`,
    color: TEXT_DIM,
    borderRadius: "8px",
    width: "36px",
    height: "36px",
    cursor: "pointer",
    fontSize: "16px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    transition: "all 0.15s ease",
  },
  divider: {
    height: "1px",
    background: BORDER,
    margin: "0 28px",
  },
  formBody: {
    padding: "24px 28px",
    display: "flex",
    flexDirection: "column",
    gap: "18px",
    flex: 1,
  },
  sectionLabel: {
    fontSize: "11px",
    fontWeight: 600,
    color: GOLD,
    letterSpacing: "0.12em",
    textTransform: "uppercase",
    paddingBottom: "4px",
    borderBottom: `1px solid ${BORDER}`,
  },
  grid2: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "14px",
  },
  label: {
    fontSize: "13px",
    color: TEXT_DIM,
    fontWeight: 500,
  },
  input: {
    background: NAVY_500,
    border: `1px solid ${BORDER}`,
    borderRadius: "10px",
    padding: "10px 14px",
    color: TEXT,
    fontSize: "14px",
    outline: "none",
    width: "100%",
    boxSizing: "border-box" as const,
    transition: "border-color 0.15s ease",
    fontFamily: "inherit",
  },
  inputError: {
    borderColor: RED,
  },
  errorText: {
    fontSize: "12px",
    color: RED,
  },
  errorBanner: {
    background: "rgba(224, 82, 82, 0.15)",
    border: `1px solid ${RED}`,
    borderRadius: "10px",
    padding: "12px 16px",
    color: RED,
    fontSize: "13px",
  },
  footer: {
    display: "flex",
    gap: "12px",
    justifyContent: "flex-end",
  },
  cancelBtn: {
    background: "transparent",
    border: `1px solid ${BORDER}`,
    borderRadius: "10px",
    padding: "10px 24px",
    color: TEXT_DIM,
    fontSize: "14px",
    cursor: "pointer",
    fontFamily: "inherit",
  },
  submitBtn: {
    background: `linear-gradient(135deg, ${GOLD}, ${GOLD_DIM})`,
    border: "none",
    borderRadius: "10px",
    padding: "10px 28px",
    color: NAVY,
    fontSize: "14px",
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "inherit",
    transition: "opacity 0.15s ease, transform 0.1s ease",
  },
  submitBtnDisabled: {
    opacity: 0.5,
    cursor: "not-allowed",
  },
};
