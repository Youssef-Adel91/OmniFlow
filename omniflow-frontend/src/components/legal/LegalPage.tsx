import React from "react";

/** Public legal page shell. The TEXT of the pages is a placeholder that a lawyer must replace (see the banner). */
export function LegalPage({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main dir="rtl" className="min-h-screen bg-[#0A0F1C] text-gray-100 px-5 py-12">
      <article className="max-w-3xl mx-auto space-y-6 leading-8">
        <p role="note" className="rounded-xl border border-amber-300/50 bg-amber-500/10 text-amber-100 p-4 text-sm">
          نص مؤقت: هذه الصفحة قالب أوّلي لم تُراجعه جهة قانونية بعد، ويجب استبداله بالنص المعتمد قبل الإطلاق التجاري.
        </p>
        <h1 className="text-3xl font-bold text-white">{title}</h1>
        {children}
      </article>
    </main>
  );
}

export function H2({ children }: { children: React.ReactNode }) { return <h2 className="text-xl font-bold text-white pt-4">{children}</h2>; }
