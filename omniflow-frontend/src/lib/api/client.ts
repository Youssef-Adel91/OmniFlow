/**
 * lib/api/client.ts — OmniFlow Axios API Client
 *
 * Singleton Axios instance pointing to the FastAPI backend.
 *
 * Updated for Clerk Authentication:
 *   - Request interceptor reads the JWT access token directly from Clerk via
 *     `window.Clerk.session.getToken()` and attaches it as `Authorization: Bearer <token>`.
 *   - Removed legacy refresh logic as Clerk handles session rotation automatically.
 */
import axios, {
  type AxiosInstance,
  type InternalAxiosRequestConfig,
  type AxiosError,
  type AxiosResponse,
} from "axios";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000/api/v1";

// ── Singleton factory ──────────────────────────────────────────────────────

function createApiClient(): AxiosInstance {
  const client = axios.create({
    baseURL: API_BASE_URL,
    timeout: 30_000,
    headers: {
      "Content-Type": "application/json",
      "Accept":       "application/json",
    },
  });

  // ── Request interceptor ─────────────────────────────────────────────────
  client.interceptors.request.use(
    async (config: InternalAxiosRequestConfig) => {
      if (typeof window !== "undefined") {
        // ── Clerk JWT Bearer token ─────────────────────────────────────────
        const clerk = (window as any).Clerk;
        if (clerk?.session) {
          try {
            const token = await clerk.session.getToken();
            if (token) {
              config.headers.Authorization = `Bearer ${token}`;
            }
          } catch (e) {
            console.error("[OmniFlow API] Failed to fetch Clerk token", e);
          }
        }
      }
      return config;
    },
    (error) => Promise.reject(error)
  );

  // ── Response interceptor ────────────────────────────────────────────────
  client.interceptors.response.use(
    (response: AxiosResponse) => response,
    async (error: AxiosError) => {
      const httpStatus = error.response?.status;

      // ── 401 Unauthorized — Handle Clerk re-authentication if necessary ───
      if (httpStatus === 401) {
        console.error("[OmniFlow API] Unauthorized request", error.config?.url);
        // Note: We don't manually clear auth here because Clerk's `<ClerkProvider>`
        // keeps the UI state in sync. If the user is truly logged out, Clerk
        // will naturally reflect that.
      }

      // ── 422 — log validation errors for debugging ────────────────────
      if (httpStatus === 422) {
        const detail = (error.response?.data as any)?.detail;
        console.error("[OmniFlow API] Validation error:", detail);
      }

      // ── 5xx — log with request ID for tracing ────────────────────────
      if (httpStatus && httpStatus >= 500) {
        const requestId = error.response?.headers?.["x-request-id"];
        console.error("[OmniFlow API] Server error", { status: httpStatus, requestId });
      }

      return Promise.reject(error);
    }
  );

  return client;
}

// ── Singleton export ───────────────────────────────────────────────────────
export const apiClient = createApiClient();

// ── Typed resource helpers ────────────────────────────────────────────────

/** Attach tenant ID to a specific request (overrides global header). */
export function withTenant(tenantId: string) {
  return { headers: { "X-Tenant-ID": tenantId } };
}
