/**
 * app/[locale]/(auth)/layout.tsx — Auth Group Layout
 *
 * Minimal wrapper for unauthenticated pages (login, forgot-password, etc.)
 * Strips out the main dashboard shell (sidebar, header) so auth pages
 * render clean and full-screen.
 */
import { AppProviders } from "@/components/auth/AppProviders";

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppProviders>{children}</AppProviders>;
}
