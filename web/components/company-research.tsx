"use client";

import { useEffect, useRef, useState } from "react";

export type ExternalResearch = {
  ticker: string;
  as_of: string;
  items: {
    id: string;
    kind: string;
    title: string;
    source: string;
    url: string;
    published_at: string;
    event_date?: string | null;
    summary?: string | null;
  }[];
  providers: { name: string; status: string; reason?: string | null }[];
  warnings: string[];
};

export type CompanyResearchProps = {
  ticker: string;
  loadResearch: (ticker: string) => Promise<ExternalResearch>;
  enabled?: boolean;
};

const MAX_ITEMS = 20;
const MAX_PROVIDERS = 8;
const clip = (value: string, limit: number) => value.length > limit ? `${value.slice(0, limit)}…` : value;
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function safeUrl(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 2048 || !/^https:\/\//i.test(value) || /[\s\\]/.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !!url.hostname && !url.username && !url.password && !value.split(/[/?#]/)[2]?.includes("@");
  } catch { return false; }
}

function dateLabel(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 40 ||
      !/^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?$/.test(value)) return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  const calendarDate = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(calendarDate.getTime()) || calendarDate.toISOString().slice(0, 10) !== value.slice(0, 10)) return null;
  // Preserve date-only precision; timestamps explicitly use UTC, independent of browser locale.
  return value.length === 10 ? value : `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

function validate(value: unknown, ticker: string) {
  if (!record(value) || value.ticker !== ticker || !dateLabel(value.as_of) ||
      !Array.isArray(value.items) || !Array.isArray(value.providers) || !Array.isArray(value.warnings)) {
    throw new Error("Invalid research response");
  }
  const items: ExternalResearch["items"] = [];
  let omitted = value.items.length > MAX_ITEMS;
  for (const item of value.items.slice(0, MAX_ITEMS)) {
    if (!record(item) || !["id", "kind", "title", "source"].every(key =>
      typeof item[key] === "string" && (item[key] as string).trim().length > 0) ||
      !safeUrl(item.url) || !dateLabel(item.published_at) ||
      (item.summary != null && typeof item.summary !== "string") ||
      (item.event_date != null && !dateLabel(item.event_date))) {
      omitted = true;
      continue;
    }
    items.push({
      id: clip(item.id as string, 100), kind: clip(item.kind as string, 60),
      title: clip(item.title as string, 200), source: clip(item.source as string, 100),
      url: item.url, published_at: item.published_at as string,
      summary: typeof item.summary === "string" ? clip(item.summary, 500) : null,
      event_date: item.event_date as string | null | undefined,
    });
  }
  const providers = value.providers.slice(0, MAX_PROVIDERS).filter(record).map(provider => ({
    name: typeof provider.name === "string" ? clip(provider.name, 80) : "Unknown provider",
    status: typeof provider.status === "string" ? provider.status : "unavailable",
    reason: typeof provider.reason === "string" ? provider.reason : null,
  }));
  return { as_of: value.as_of as string, items, providers, omitted,
    limitedProviders: value.providers.length > MAX_PROVIDERS,
    hasWarnings: value.warnings.length > 0 };
}

function providerLabel(provider: ExternalResearch["providers"][number]) {
  if (["not_configured", "missing_configuration"].includes(provider.status) ||
      ["missing_contact_user_agent", "invalid_contact_user_agent", "runtime_adapter_not_configured", "not_configured"].includes(provider.reason ?? "")) {
    return "Setup required; this source was not checked.";
  }
  if (provider.status === "ok") return "Checked.";
  if (provider.reason === "rate_limited") return "Temporarily unavailable; try again later.";
  return "Unavailable; coverage may be incomplete.";
}

/** Supply loadResearch from a client parent. It must return public, display-safe data only. */
export function CompanyResearch({ ticker, loadResearch, enabled = true }: CompanyResearchProps) {
  // Remount before rendering a new ticker so previous results never flash on screen.
  return <ResearchSession key={`${ticker}:${enabled}`} ticker={ticker} loadResearch={loadResearch} enabled={enabled} />;
}

function ResearchSession({ ticker, loadResearch, enabled }: CompanyResearchProps) {
  const [result, setResult] = useState<ReturnType<typeof validate> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const active = useRef(true);
  const busy = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; generation.current++; busy.current = false; };
  }, []);

  async function check() {
    if (!enabled || !ticker.trim() || busy.current || !active.current) return;
    busy.current = true; // Covers a second click before React commits the disabled button.
    const requestGeneration = generation.current;
    const current = () => active.current && requestGeneration === generation.current;
    setLoading(true);
    setError(false);
    setResult(null);
    try {
      const response = await loadResearch(ticker);
      if (current()) setResult(validate(response, ticker));
    } catch {
      if (current()) setError(true);
    } finally {
      if (current()) { busy.current = false; setLoading(false); }
    }
  }

  return <section className="panel standalone-panel" aria-label="Outside company information">
    <h3>Outside information</h3>
    <p>External facts for {clip(ticker, 24)} are separate from the technical recommendation. They do not change the strategy or suggest a trade.</p>
    <p>This is a read-only check for the selected ticker. Results identify each source, publication date, and check date; provider availability is shown below.</p>
    <button type="button" className="secondary-button" onClick={check}
      disabled={!enabled || !ticker.trim() || loading}>Check outside information</button>
    <div role="status" aria-live="polite" aria-busy={loading}>
      {!enabled ? <p>Outside information is disabled.</p> : loading ? <p>Checking sources…</p> :
        error ? <p>Outside information could not be checked. Please try again.</p> :
          !result ? <p>No check yet. Use the button to request information.</p> : <>
            <p>Checked: <time dateTime={result.as_of}>{dateLabel(result.as_of)}</time></p>
            {result.hasWarnings && <p>Sources reported warnings. Coverage may be incomplete.</p>}
            {result.providers.length ? <ul aria-label="Source availability">
              {result.providers.map((provider, index) => <li key={index}>{provider.name}: {providerLabel(provider)}</li>)}
            </ul> : <p>No source availability details were supplied.</p>}
            {result.limitedProviders && <p>Showing the first {MAX_PROVIDERS} providers.</p>}
            {result.omitted && <p>Showing up to {MAX_ITEMS} items. Additional items or items without a valid source link and dates were omitted.</p>}
            {!result.items.length ? <p>No sourced results were returned for this check. This does not mean there are no relevant events.</p> :
              <details>
                <summary>View {result.items.length} sourced {result.items.length === 1 ? "item" : "items"}</summary>
                <ul>{result.items.map((item, index) => <li key={`${item.id}:${index}`}>
                  <a href={item.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{item.title}</a>
                  <p>{item.source} · {item.kind} · Published: <time dateTime={item.published_at}>{dateLabel(item.published_at)}</time></p>
                  {item.event_date && <p>Event date: <time dateTime={item.event_date}>{dateLabel(item.event_date)}</time></p>}
                  {item.summary && <p>{item.summary}</p>}
                </li>)}</ul>
              </details>}
          </>}
    </div>
  </section>;
}

export default CompanyResearch;
