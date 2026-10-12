import React from "react";

export interface DocSection { h: string; p: string[] }

/** A legal / policy document page body: draft banner + headings + paragraphs. All legal text on the site is a placeholder pending lawyer review. */
export function Doc({ title, draft, sections, children }: { title: string; draft: string; sections: DocSection[]; children?: React.ReactNode }) {
  return (
    <article className="max-w-3xl mx-auto px-4 py-12 space-y-6 leading-8 text-gray-100">
      <p role="note" className="rounded-xl border border-amber-300/50 bg-amber-500/10 text-amber-100 p-4 text-sm">{draft}</p>
      <h1 className="text-3xl font-bold text-white">{title}</h1>
      {sections.map((s) => (
        <section key={s.h} className="space-y-2">
          <h2 className="text-xl font-bold text-white pt-4">{s.h}</h2>
          {s.p.map((t, i) => <p key={i}>{t}</p>)}
        </section>
      ))}
      {children}
    </article>
  );
}
