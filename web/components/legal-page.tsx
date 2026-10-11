import { LegalLinks } from "./legal-links";

export function LegalPage({ title, summary, children }: { title: string; summary: string; children: React.ReactNode }) {
  return (
    <main className="legal-shell">
      <article className="legal-card">
        <a className="legal-home" href="/">← Stock Competition</a>
        <span className="eyebrow">Effective September 29, 2026</span>
        <h1>{title}</h1>
        <p className="legal-summary">{summary}</p>
        <div className="legal-content">{children}</div>
        <LegalLinks />
      </article>
    </main>
  );
}
