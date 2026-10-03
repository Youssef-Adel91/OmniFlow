"use client";

import { useAuth } from "@clerk/nextjs";

/**
 * True only once Clerk has loaded AND the user is signed in. Pages gate
 * their first API call on this (`enabled`/early-return in effects) so no
 * request leaves before a token can be attached.
 */
export function useAuthReady(): boolean {
  const { isLoaded, isSignedIn } = useAuth();
  return Boolean(isLoaded && isSignedIn);
}
