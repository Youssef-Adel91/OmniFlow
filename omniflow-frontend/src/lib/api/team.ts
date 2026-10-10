/** lib/api/team.ts — members, invitations, seats (backend: routers/team.py). */
import { apiClient } from "@/lib/api/client";

export type Role = "admin" | "agent" | "auditor";
export interface Member { user_id: string; full_name: string; email: string; role: Role; is_active: boolean; last_login_at: string | null }
export interface Invitation { invitation_id: string; email: string; role: Role; status: string; delivery: "clerk" | "email" | "none" | null; created_at: string | null; expires_at: string }
export interface Seats { active: number; pending: number; used: number; limit: number | null }
export interface TeamResponse { members: Member[]; invitations: Invitation[]; seats: Seats; me: string; can_edit: boolean }

export const ROLE_LABEL: Record<Role, string> = { admin: "مدير", agent: "موظف مبيعات", auditor: "مدقّق (قراءة فقط)" };
export const ROLE_HELP: Record<Role, string> = {
  admin: "كل الصلاحيات: الفريق، الفوترة، الإعدادات، البيانات.",
  agent: "يرد على العملاء ويدير العقارات والعملاء والحملات، دون الإعدادات والفوترة.",
  auditor: "يطّلع فقط على البيانات والتقارير دون أي تعديل.",
};
export const DELIVERY_LABEL: Record<string, string> = { clerk: "أُرسلت دعوة عبر نظام الدخول", email: "أُرسل بريد دعوة", none: "لم يُرسل بريد — شارك رابط التسجيل مع الزميل" };

/** "3 / 10" or "3 / ∞" — pending invitations count as used seats. */
export function seatsText(s: Seats): string { return `${s.used} / ${s.limit ?? "∞"}`; }
export function seatsFull(s: Seats): boolean { return s.limit != null && s.used >= s.limit; }
export function validEmail(v: string): boolean { return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v.trim()); }
/** The only reason a row's controls are locked: you cannot edit yourself. */
export function canEditMember(me: string, m: Member, canEdit: boolean): boolean { return canEdit && m.user_id !== me; }

export function teamError(err: unknown, fallback: string): string {
  const e = err as { response?: { data?: { detail?: unknown } } };
  const d = e?.response?.data?.detail as { message?: string } | string | undefined;
  return typeof d === "string" ? d : d && typeof d === "object" && typeof d.message === "string" ? d.message : fallback;
}

export async function fetchTeam(): Promise<TeamResponse> { return (await apiClient.get<TeamResponse>("/team")).data; }
export async function inviteMember(email: string, role: Role): Promise<Invitation> { return (await apiClient.post<Invitation>("/team/invitations", { email: email.trim(), role })).data; }
export async function revokeInvitation(id: string): Promise<void> { await apiClient.delete(`/team/invitations/${id}`); }
export async function updateMember(id: string, patch: { role?: Role; is_active?: boolean }): Promise<Member> { return (await apiClient.patch<Member>(`/team/members/${id}`, patch)).data; }
