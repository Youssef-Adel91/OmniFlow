import { redirect } from "next/navigation";

/**
 * app/test/page.tsx — retired scratch route.
 *
 * This used to render a bare "TEST PAGE WORKS" heading. It is not part of the
 * product and sits outside the [locale] tree (so it has no i18n or dashboard
 * chrome). The file cannot be deleted from this environment, so the route now
 * simply redirects to the default-locale dashboard.
 */
export default function TestPage(): never {
  redirect("/ar/dashboard");
}
