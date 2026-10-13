import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import type { NextFetchEvent, NextRequest } from "next/server";
import createMiddleware from "next-intl/middleware";
import { routing } from "@/i18n/routing";

const intlMiddleware = createMiddleware(routing);

const isProtectedRoute = createRouteMatcher([
  '/([a-z]{2})/dashboard(.*)',
  '/([a-z]{2})/inbox(.*)',
  '/([a-z]{2})/settings(.*)',
  '/([a-z]{2})/broadcasts(.*)',
  '/([a-z]{2})/properties(.*)',
  '/([a-z]{2})/customers(.*)',
  '/([a-z]{2})/reports(.*)',
  '/([a-z]{2})/onboarding(.*)',
  '/([a-z]{2})/knowledge(.*)',
  '/([a-z]{2})/profile(.*)',
  '/([a-z]{2})/support(.*)'
]);

// Only the signed-in app and the auth pages need Clerk. Everything else (marketing, legal, pricing, data-deletion pages and unknown URLs that
// render the 404) never reads the session, so it skips Clerk entirely: no handshake redirect for first-time visitors (in development a new
// browser would otherwise be bounced through Clerk's domain), less work per request, and those pages keep working if Clerk is down.
const needsClerk = createRouteMatcher([
  "/([a-z]{2})/dashboard(.*)", "/([a-z]{2})/inbox(.*)", "/([a-z]{2})/settings(.*)", "/([a-z]{2})/broadcasts(.*)", "/([a-z]{2})/properties(.*)",
  "/([a-z]{2})/customers(.*)", "/([a-z]{2})/reports(.*)", "/([a-z]{2})/onboarding(.*)", "/([a-z]{2})/knowledge(.*)", "/([a-z]{2})/profile(.*)",
  "/([a-z]{2})/support(.*)", "/([a-z]{2})/billing(.*)",
  "/([a-z]{2})/sign-in(.*)", "/([a-z]{2})/sign-up(.*)", "/([a-z]{2})/login(.*)", "/([a-z]{2})/register(.*)",
  "/(api|trpc)(.*)", "/__clerk(.*)",
]);

const clerk = clerkMiddleware(async (auth, req) => {
  if (isProtectedRoute(req)) {
    await auth.protect();
  }
  return intlMiddleware(req);
});

export default function proxy(req: NextRequest, event: NextFetchEvent) {
  if (!needsClerk(req)) return intlMiddleware(req);
  return clerk(req, event);
}

export const config = {
  matcher: [
    // robots.txt / sitemap.xml must be served as-is (a locale redirect would send crawlers to /ar/robots.txt = 404)
    "/((?!_next|robots\\.txt$|sitemap\\.xml$|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/:path*",
  ],
};
