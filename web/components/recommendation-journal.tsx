"use client";

import { useEffect, useRef, useState } from "react";
import type { RecommendationJournalResponse } from "../lib/api";

type Props = { loadJournal: () => Promise<RecommendationJournalResponse>; enabled: boolean };
const text = (value: string | null | undefined) => value ? value.slice(0, 160) : "Unavailable";
const percent = (value: number | null) => typeof value === "number" && Number.isFinite(value)
  ? `${(value * 100).toFixed(2)}%` : "Unavailable";

function priceLoadExplanation(status: RecommendationJournalResponse["price_load_status"]) {
  switch (status) {
    case "loaded": return "Price history was loaded. Individual comparisons may still be maturing or missing required dates.";
    case "partial": return "Only some requested price history was loaded. Comparisons with missing prices remain unavailable.";
    case "busy": return "The price service is busy. Saved recommendations are shown; use Load journal to try again later.";
    case "timed_out": return "The price request timed out. Saved recommendations are shown without new price outcomes; use Load journal to try again later.";
    case "unavailable": return "The price service is unavailable. Saved recommendations are shown; use Load journal to try again later.";
    case "not_needed": return "No price lookup was needed for this history window.";
    default: return "Price-loading status was not supplied. Missing outcomes remain unavailable.";
  }
}

function reason(value: string | null) {
  switch (value?.split(":")[0]) {
    case "maturing": return "The observation period is not complete yet.";
    case "missing_history": case "missing_session": return "Required price history is missing.";
    case "symbol_limit": return "This symbol is outside the current price lookup limit.";
    case "calendar_unavailable": return "The required market calendar is unavailable.";
    case "invalid_recorded_timing": case "signal_date_after_generation": return "Recorded timing cannot support this comparison.";
    case "generation_not_yet_observable": return "The saved recommendation is newer than the observation cutoff.";
    default: return "A reliable comparison is unavailable.";
  }
}

export function RecommendationJournal({ enabled, loadJournal }: Props) {
  return <JournalSession key={String(enabled)} enabled={enabled} loadJournal={loadJournal} />;
}

function JournalSession({ enabled, loadJournal }: Props) {
  const [journal, setJournal] = useState<RecommendationJournalResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [visibleCount, setVisibleCount] = useState(100);
  const lifecycle = useRef(0);
  const active = useRef(true);
  const busy = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; lifecycle.current++; busy.current = false; };
  }, []);

  async function load() {
    if (!enabled || busy.current || !active.current) return;
    busy.current = true;
    const version = lifecycle.current;
    const current = () => active.current && lifecycle.current === version;
    setLoading(true); setError(false); setJournal(null); setVisibleCount(100);
    try {
      const response = await loadJournal();
      if (current()) setJournal(response);
    } catch {
      if (current()) setError(true);
    } finally {
      if (current()) { busy.current = false; setLoading(false); }
    }
  }

  return <section className="panel standalone-panel" aria-label="Recommendation journal">
    <h2>Recommendation journal</h2>
    <p>Read-only research on saved recommendations. Returns use adjusted daily closes, starting at the first market session close after both the signal and publication dates.</p>
    <p>Load journal requests prices for the saved tickers and date ranges from the configured market-data provider. This read-only request does not change stored plans or recommendation statuses. Unavailable prices remain unavailable.</p>
    <p>These are underlying stock returns for every action, including SELL and AVOID. They are not trade profits, fills, or strategy performance. Missing prices and limited saved history can bias the results.</p>
    <button type="button" className="secondary-button" disabled={!enabled || loading} onClick={load}>Load journal</button>
    <div role="status" aria-live="polite" aria-busy={loading}>
      {!enabled ? <p>Sign in to load your saved recommendation journal. Demo results are not supplied.</p> :
        loading ? <p>Loading saved recommendations and dated outcomes…</p> :
          error ? <p>The journal could not be loaded. Please try again later.</p> :
            !journal ? <p>The journal has not been loaded. Use Load journal to request it.</p> : <>
              <p>{priceLoadExplanation(journal.price_load_status)}</p>
              {!!journal.warnings?.length && <p>The journal reported coverage warnings. Some saved history or price comparisons may be incomplete.</p>}
              <p>Observation cutoff: {text(journal.asof)}. Coverage: {journal.source_history.length} supplied saved plans, {journal.entries.length} recommendations. This may not include your complete history.</p>
              <p>{journal.needed_symbols.length} symbols needed; {journal.deferred_symbols.length} outside the lookup limit. Missing outcomes remain unavailable.</p>
              {journal.entries.length === 0 ? <p>No saved recommendations were returned for this history window.</p> : <>
                <p>All stored statuses in the returned history are included, including dismissed and recorded recommendations.</p>
                {journal.entries.length > visibleCount && <p>Showing the first {visibleCount} of {journal.entries.length} recommendations.</p>}
                <ol>{journal.entries.slice(0, visibleCount).map((entry, index) => <li key={index}>
                  <details>
                    <summary>{text(entry.ticker)} · {text(entry.action)} · {text(entry.generated_at)}</summary>
                    <p>Strategy: {text(entry.strategy)}. Plan status: {text(entry.plan_status)}. Recommendation status: {text(entry.status)}.</p>
                    <p>Signal date: {text(entry.signal_date)}. Reference close: {text(entry.reference_date)}.</p>
                    <ul>{entry.outcomes.slice(0, 3).map((outcome, outcomeIndex) => <li key={outcomeIndex}>
                      <strong>{outcome.sessions} sessions</strong> · ending {text(outcome.end_date)}
                      <p>Stock: {percent(outcome.stock_return)}. SPY: {percent(outcome.spy_return)}. Difference: {typeof outcome.excess_return === "number" && Number.isFinite(outcome.excess_return) ? `${(outcome.excess_return * 100).toFixed(2)} percentage points` : "Unavailable"}.</p>
                      {outcome.status === "complete" ? <p>Both price series are available for this comparison.</p> :
                        <p>{outcome.status === "maturing" ? "Still maturing. " : "Incomplete coverage. "}{reason(outcome.null_reason)}</p>}
                    </li>)}</ul>
                    {entry.outcomes.length === 0 && <p>No outcomes were supplied.</p>}
                  </details>
                </li>)}</ol>
                {journal.entries.length > visibleCount && <button type="button" className="secondary-button"
                  onClick={() => setVisibleCount(count => count + 100)}>Show more saved recommendations</button>}
              </>}
            </>}
    </div>
  </section>;
}
