import { notFound } from "next/navigation";

/** Any unknown path under a locale renders the localized 404 (with the real layout) instead of the bare framework page. */
export default function CatchAll() {
  notFound();
}
