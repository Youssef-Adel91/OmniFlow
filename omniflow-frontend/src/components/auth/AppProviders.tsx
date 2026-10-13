import { ClerkProvider } from "@clerk/nextjs";
import React from "react";
import { AuthHydrator } from "@/components/auth/AuthHydrator";

/**
 * Clerk + the deferred store hydration, mounted ONLY around the signed-in / auth areas (dashboard, onboarding, sign-in, sign-up, legacy login).
 * The public marketing and legal pages deliberately do not load it: Clerk's client bundle is a large part of first-load JS and those pages
 * never read the session (route protection happens in the middleware, on the server).
 */
export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider>
      <AuthHydrator />
      {children}
    </ClerkProvider>
  );
}
