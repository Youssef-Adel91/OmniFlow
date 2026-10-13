"use client";

import { useParams } from "next/navigation";
import { ErrorPanel } from "@/components/system/ErrorPanel";
import { asFallbackLocale } from "@/components/system/messages";

/** Inside the dashboard layout: the sidebar and header stay usable when one page crashes. */
export default function DashboardError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  const params = useParams<{ locale?: string }>();
  return <ErrorPanel error={error} retry={retry} locale={asFallbackLocale(params?.locale)} tone="inline" />;
}
