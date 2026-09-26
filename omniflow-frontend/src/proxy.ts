import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
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

export default clerkMiddleware(async (auth, req) => {
  if (isProtectedRoute(req)) {
    await auth.protect();
  }
  return intlMiddleware(req);
});

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/:path*",
  ],
};
