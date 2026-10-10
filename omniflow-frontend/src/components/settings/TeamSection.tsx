"use client";

/** الفريق والصلاحيات — members, roles, invitations, seat usage. Admin edits; auditor reads. */
import React, { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Mail, Trash2, UserPlus, Users } from "lucide-react";
import {
  canEditMember, DELIVERY_LABEL, fetchTeam, inviteMember, revokeInvitation, ROLE_HELP, ROLE_LABEL, seatsFull, seatsText, teamError, updateMember,
  validEmail, type Member, type Role, type TeamResponse,
} from "@/lib/api/team";
import { LoadError, SectionHeader, SectionSkeleton, cardClass, inputClass } from "./common";

const fmtDate = (iso: string | null) => (iso ? new Intl.DateTimeFormat("ar-SA", { dateStyle: "medium", timeZone: "Asia/Riyadh" }).format(new Date(iso)) : "لم يدخل بعد");

export default function TeamSection() {
  const [data, setData] = useState<TeamResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("agent");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { setData(await fetchTeam()); } catch (e) { setError(teamError(e, "تعذّر تحميل الفريق.")); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const run = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key); setNotice(null);
    try { await fn(); setNotice({ ok: true, text: ok }); await load(); } catch (e) { setNotice({ ok: false, text: teamError(e, "تعذّر تنفيذ العملية.") }); } finally { setBusy(null); }
  };

  if (loading && !data) return <div className={cardClass}><SectionSkeleton /></div>;
  if (error && !data) return <LoadError message={error} onRetry={() => void load()} />;
  if (!data) return null;
  const full = seatsFull(data.seats);
  const emailOk = validEmail(email);

  return (
    <div className="space-y-6" data-testid="team-section">
      <div className={cardClass}>
        <SectionHeader icon={Users} title="الفريق والصلاحيات" description="ادعُ زملاءك، حدّد أدوارهم، وعطّل من لا يعمل معكم." />
        <p className="text-sm text-gray-100" data-testid="seats">المقاعد المستخدمة (تشمل الدعوات المعلّقة): <strong dir="ltr">{seatsText(data.seats)}</strong>{full && <span className="text-amber-200"> — بلغت الحد، رقِّ الباقة لإضافة أعضاء.</span>}</p>
        {notice && <p role={notice.ok ? "status" : "alert"} className={`text-sm flex items-center gap-2 ${notice.ok ? "text-emerald-300" : "text-red-300"}`}>{notice.ok ? <CheckCircle2 className="w-4 h-4" aria-hidden /> : <AlertTriangle className="w-4 h-4" aria-hidden />}{notice.text}</p>}

        {data.can_edit && (
          <form className="grid grid-cols-1 md:grid-cols-[1fr_14rem_auto] gap-3 items-end" onSubmit={(e) => { e.preventDefault(); if (emailOk && !full) void run("invite", async () => { await inviteMember(email, role); setEmail(""); }, "تم إنشاء الدعوة."); }}>
            <div><label htmlFor="inv-email" className="block text-sm font-medium text-gray-100 mb-1.5">بريد الزميل</label>
              <input id="inv-email" type="email" dir="ltr" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.sa" className={inputClass} /></div>
            <div><label htmlFor="inv-role" className="block text-sm font-medium text-gray-100 mb-1.5">الدور</label>
              <select id="inv-role" value={role} onChange={(e) => setRole(e.target.value as Role)} className={inputClass}>
                {(Object.keys(ROLE_LABEL) as Role[]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select></div>
            <button type="submit" disabled={!emailOk || full || busy !== null} className="bg-[#C9A84C] text-[#0A0F1C] px-5 py-2.5 rounded-lg font-bold inline-flex items-center gap-2 disabled:opacity-50">
              {busy === "invite" ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <UserPlus className="w-4 h-4" aria-hidden />}إرسال الدعوة</button>
          </form>
        )}
        {data.can_edit && <p className="text-xs text-gray-300">{ROLE_HELP[role]} يجب أن ينشئ الزميل حسابه بنفس البريد ليُضاف تلقائيًا.</p>}
      </div>

      <div className={cardClass}>
        <h3 className="text-lg font-bold text-white">الأعضاء</h3>
        <ul className="divide-y divide-white/10">
          {data.members.map((m: Member) => {
            const editable = canEditMember(data.me, m, data.can_edit);
            return (
              <li key={m.user_id} className="py-3 flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-white truncate" dir="auto">{m.full_name}{m.user_id === data.me && <span className="text-xs text-gray-300"> (أنت)</span>}</p>
                  <p className="text-xs text-gray-300 truncate" dir="ltr">{m.email}</p>
                  <p className="text-xs text-gray-300">آخر دخول: {fmtDate(m.last_login_at)}</p>
                </div>
                {editable ? (
                  <select aria-label={`دور ${m.full_name}`} value={m.role} disabled={busy !== null || !m.is_active} className={`${inputClass} !w-44`}
                    onChange={(e) => void run(m.user_id, () => updateMember(m.user_id, { role: e.target.value as Role }), "تم تغيير الدور.")}>
                    {(Object.keys(ROLE_LABEL) as Role[]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select>
                ) : <span className="text-sm text-gray-100">{ROLE_LABEL[m.role]}</span>}
                <span className={`text-xs rounded-full border px-2.5 py-1 ${m.is_active ? "border-emerald-300/40 text-emerald-200" : "border-white/25 text-gray-300"}`}>{m.is_active ? "نشط" : "معطّل"}</span>
                {editable && <button disabled={busy !== null} onClick={() => void run(m.user_id, () => updateMember(m.user_id, { is_active: !m.is_active }), m.is_active ? "تم التعطيل." : "تمت إعادة التفعيل.")}
                  className="text-sm text-gray-100 border border-white/25 rounded-lg px-3 py-1.5 hover:bg-white/10 disabled:opacity-50">{m.is_active ? "تعطيل" : "إعادة تفعيل"}</button>}
              </li>
            );
          })}
        </ul>
      </div>

      <div className={cardClass}>
        <h3 className="text-lg font-bold text-white flex items-center gap-2"><Mail className="w-5 h-5 text-[#C9A84C]" aria-hidden />دعوات معلّقة</h3>
        {data.invitations.length === 0 ? <p className="text-sm text-gray-300">لا توجد دعوات معلّقة.</p> : (
          <ul className="space-y-2">{data.invitations.map((i) => (
            <li key={i.invitation_id} className="flex flex-wrap items-center gap-3 border border-white/10 rounded-lg px-3 py-2 text-sm text-gray-100">
              <span className="flex-1 min-w-0 truncate" dir="ltr">{i.email}</span><span>{ROLE_LABEL[i.role]}</span>
              <span className="text-xs text-gray-300">{i.delivery ? DELIVERY_LABEL[i.delivery] : ""} · تنتهي {fmtDate(i.expires_at)}</span>
              {data.can_edit && <button aria-label={`إلغاء دعوة ${i.email}`} disabled={busy !== null} onClick={() => void run(i.invitation_id, () => revokeInvitation(i.invitation_id), "أُلغيت الدعوة.")} className="p-2 rounded hover:bg-white/10"><Trash2 className="w-4 h-4" /></button>}
            </li>))}</ul>
        )}
      </div>
    </div>
  );
}
