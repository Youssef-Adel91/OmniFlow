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


// ── Clerk token helper ─────────────────────────────────────────────────────

type ClerkLike = {
  loaded?: boolean;
  session?: { getToken: (opts?: { skipCache?: boolean }) => Promise<string | null> } | null;
};

const CLERK_WAIT_MS = 5_000;
const CLERK_POLL_MS = 50;

/**
 * Resolve a Clerk session token, waiting (bounded) for Clerk to load first.
 * Returns null when there is no signed-in session (or it never becomes ready).
 */
export async function getClerkToken(opts?: { skipCache?: boolean }): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const deadline = Date.now() + CLERK_WAIT_MS;
  let clerk = (window as unknown as { Clerk?: ClerkLike }).Clerk;
  while ((!clerk || !clerk.loaded) && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, CLERK_POLL_MS));
    clerk = (window as unknown as { Clerk?: ClerkLike }).Clerk;
  }
  if (!clerk?.loaded || !clerk.session) return null;
  try {
    return await clerk.session.getToken(opts);
  } catch (e) {
    console.warn("[OmniFlow API] Failed to fetch Clerk token", e);
    return null;
  }
}

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
        // Wait for Clerk to finish loading BEFORE sending: a request fired
        // during hydration would otherwise go out without a token and 401.
        const token = await getClerkToken();
        if (token) {
          config.headers.Authorization = `Bearer ${token}`;
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

      // ── 401 Unauthorized — retry ONCE with a freshly minted Clerk token ──
      // (a stale/not-yet-ready token is the common cause during startup).
      const original = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined;
      if (httpStatus === 401 && original && !original._retried && typeof window !== "undefined") {
        original._retried = true;
        const fresh = await getClerkToken({ skipCache: true });
        if (fresh) {
          original.headers.Authorization = `Bearer ${fresh}`;
          return client.request(original);
        }
      }
      if (httpStatus === 401) {
        // Final failure only (a warn, not an error: it must not trigger the
        // Next dev error overlay). Clerk's <ClerkProvider> owns UI auth state.
        console.warn("[OmniFlow API] Unauthorized request", error.config?.url);
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
