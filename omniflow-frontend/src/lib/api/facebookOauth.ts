/**
 * lib/api/facebookOauth.ts — "Connect with Facebook" OAuth Client
 *
 * Talks to gateway/routers/facebook_oauth.py (backend prefix
 * /api/v1/integrations/facebook). The OAuth dance itself happens in a popup
 * window the caller opens on `authorize_url`; this module only starts that
 * flow and listens for the popup's postMessage result -- see
 * startFacebookConnect() below for the full sequence.
 */
import { apiClient, API_BASE_URL } from "@/lib/api/client";

// The popup is served by the BACKEND (facebook_oauth.py's callback page),
// not this Next.js app -- so the postMessage's event.origin is the
// backend's origin, not window.location.origin. Computed once from
// API_BASE_URL (e.g. "http://127.0.0.1:8000/api/v1" -> "http://127.0.0.1:8000").
const BACKEND_ORIGIN = new URL(API_BASE_URL).origin;

export interface FacebookPageOption {
  page_id: string;
  page_name: string;
  instagram_username: string | null;
}

export type FacebookOauthResult =
  | { status: "success"; page_name: string; instagram_username: string | null }
  | { status: "needs_selection"; connection_id: string; pages: FacebookPageOption[] }
  | { status: "error"; message: string }
  | { status: "cancelled" };

/** Step 1: ask the backend to build the Facebook Login dialog URL. */
async function fetchAuthorizeUrl(): Promise<string> {
  const { data } = await apiClient.get<{ authorize_url: string }>(
    "/integrations/facebook/start",
  );
  return data.authorize_url;
}

/** Step 3 (only reached when fetchAuthorizeUrl's flow found >1 Page): finalize the pick. */
export async function selectFacebookPage(
  connectionId: string,
  pageId: string,
): Promise<{ page_id: string; page_name: string; instagram_username: string | null }> {
  const { data } = await apiClient.post("/integrations/facebook/select-page", {
    connection_id: connectionId,
    page_id: pageId,
  });
  return data;
}

/**
 * Step 2: open the popup, wait for the backend's callback page to
 * postMessage the result back, and resolve with it.
 *
 * Opens the authorize_url in a popup (not a full-page redirect) so the
 * onboarding page never navigates away -- the user approves on
 * facebook.com, Meta redirects that popup to our backend's /callback,
 * which renders a tiny HTML page that posts the result back here and
 * closes itself (see facebook_oauth.py's _popup_response()).
 *
 * Resolves with {status: "cancelled"} if the user closes the popup
 * without completing the flow (detected via a polling interval, since
 * a cross-origin popup gives no closure event).
 */
export async function startFacebookConnect(): Promise<FacebookOauthResult> {
  const authorizeUrl = await fetchAuthorizeUrl();

  const width = 600;
  const height = 700;
  const left = window.screenX + (window.outerWidth - width) / 2;
  const top = window.screenY + (window.outerHeight - height) / 2;
  const popup = window.open(
    authorizeUrl,
    "omniflow-facebook-connect",
    `width=${width},height=${height},left=${left},top=${top}`,
  );

  if (!popup) {
    return { status: "error", message: "تم حظر النافذة المنبثقة من المتصفح. فعّل النوافذ المنبثقة لهذا الموقع وحاول مرة أخرى." };
  }

  return new Promise<FacebookOauthResult>((resolve) => {
    let settled = false;

    const cleanup = () => {
      window.removeEventListener("message", onMessage);
      clearInterval(pollClosed);
    };

    const onMessage = (event: MessageEvent) => {
      if (event.origin !== BACKEND_ORIGIN) return;
      const data = event.data;
      if (!data || data.source !== "omniflow-fb-oauth") return;
      settled = true;
      cleanup();
      resolve(data as FacebookOauthResult);
    };

    const pollClosed = setInterval(() => {
      if (popup.closed) {
        cleanup();
        if (!settled) resolve({ status: "cancelled" });
      }
    }, 500);

    window.addEventListener("message", onMessage);
  });
}
