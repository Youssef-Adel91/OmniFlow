"use client";

import { RedirectToSignIn, useAuth } from "@clerk/nextjs";
import { useLocale } from "next-intl";

/** Wait for Clerk before mounting pages whose effects request protected data. */
export function DashboardAuth({ children }: { children: React.ReactNode }) {
  const { isLoaded, isSignedIn } = useAuth();
  const locale = useLocale();

  if (!isLoaded) {
    return (
      <div role="status" className="flex min-h-screen items-center justify-center text-[var(--text-secondary)]">
        {locale === "ar" ? "جارٍ تحميل حسابك…" : "Loading your account…"}
      </div>
    );
  }

  if (!isSignedIn) return <RedirectToSignIn />;
  return <>{children}</>;
}
