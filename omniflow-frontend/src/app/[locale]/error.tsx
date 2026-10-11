"use client";

import { useParams } from "next/navigation";
import { ErrorPanel } from "@/components/system/ErrorPanel";
import { asFallbackLocale } from "@/components/system/messages";

export default function LocaleError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  const params = useParams<{ locale?: string }>();
  return <ErrorPanel error={error} retry={retry} locale={asFallbackLocale(params?.locale)} tone="page" />;
}
