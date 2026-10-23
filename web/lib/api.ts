import { DEMO_SNAPSHOT } from "./demo-data";
import type { ExternalResearch } from "../components/company-research";
import { runPlanJob, type PlanProgress } from "./plan-job-client";
export type { PlanProgress } from "./plan-job-client";
import type { PaperAction, PlanHistoryItem, PlanningProfile, PublicSnapshot, TradingViewPlanningRules } from "./types";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "";
export const demoMode = (process.env.NEXT_PUBLIC_DEMO_MODE ?? "true").toLowerCase() === "true";

export class AccountNotFoundError extends Error {}

export type RecommendationJournalOutcome = {
  sessions: number;
  end_date: string | null;
  stock_return: number | null;
  spy_return: number | null;
  excess_return: number | null;
  stock_null_reason: string | null;
  spy_null_reason: string | null;
  null_reason: string | null;
  status: "complete" | "maturing" | "unavailable";
};

export type RecommendationJournalResponse = {
  asof: string;
  price_load_status?: "loaded" | "partial" | "busy" | "timed_out" | "unavailable" | "not_needed";
  warnings?: string[];
  source_history: unknown[];
  entries: {
    plan_id: string | null;
    recommendation_index: number;
    strategy: string | null;
    plan_status: string | null;
    generated_at: string | null;
    market_as_of: string | null;
    ticker: string;
    status: string | null;
    action: string | null;
    recommendation: Record<string, unknown>;
    signal_date: string | null;
    reference_date: string | null;
    outcomes: RecommendationJournalOutcome[];
  }[];
  needed_symbols: string[];
  deferred_symbols: string[];
  fetch_request: { tickers: string[]; start_date: string; end_date: string } | null;
  methodology: {
    kind: string; return_units: "fraction"; price_basis: string;
    reference: string; signal_date_return: string; execution: string;
    calendar_source: string; calendar_years: number[]; coverage: string; bias_warning: string;
  };
};

async function loadAuthenticatedResearch(path: string, accessToken: string): Promise<unknown> {
  if (!accessToken?.trim()) throw new Error("Sign in to load research.");
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/${path}`, {
    method: "GET", headers: { Authorization: `Bearer ${accessToken}` },
    cache: "no-store", redirect: "error", signal: AbortSignal.timeout(60_000),
  });
  if (!response.ok) throw new Error(response.status === 401
    ? "Your session expired. Please sign in again." : "Research is unavailable. Please try again later.");
  return response.json();
}

export async function loadCompanyResearch(accessToken: string, ticker: string): Promise<ExternalResearch> {
  const symbol = ticker.trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?$/.test(symbol)) throw new Error("Enter a valid stock ticker.");
  return await loadAuthenticatedResearch(`research/company/${encodeURIComponent(symbol)}`, accessToken) as ExternalResearch;
}

export async function loadRecommendationJournal(accessToken: string): Promise<RecommendationJournalResponse> {
  const value = await loadAuthenticatedResearch("recommendation-journal", accessToken) as RecommendationJournalResponse;
  // Reject malformed or incompatible results rather than inventing zero returns.
  if (!value || typeof value.asof !== "string" || !Array.isArray(value.source_history) ||
      (value.price_load_status !== undefined && !["loaded", "partial", "busy", "timed_out", "unavailable", "not_needed"].includes(value.price_load_status)) ||
      (value.warnings !== undefined && (!Array.isArray(value.warnings) || !value.warnings.every(warning => typeof warning === "string"))) ||
      !Array.isArray(value.entries) || !Array.isArray(value.needed_symbols) || !Array.isArray(value.deferred_symbols) ||
      value.methodology?.return_units !== "fraction" || value.methodology.kind !== "post_publication_close_research_return" ||
      value.methodology.price_basis !== "loader_adjusted_close" ||
      !value.entries.every(entry => entry && typeof entry.ticker === "string" && Array.isArray(entry.outcomes) &&
        [entry.strategy, entry.plan_status, entry.generated_at, entry.market_as_of, entry.status, entry.action, entry.signal_date, entry.reference_date]
          .every(field => field == null || typeof field === "string") &&
        entry.outcomes.every(outcome => outcome && [5, 20, 60].includes(outcome.sessions) &&
          ["complete", "maturing", "unavailable"].includes(outcome.status) &&
          [outcome.end_date, outcome.stock_null_reason, outcome.spy_null_reason, outcome.null_reason].every(field => field === null || typeof field === "string") &&
          [outcome.stock_return, outcome.spy_return, outcome.excess_return].every(field => field === null || typeof field === "number" && Number.isFinite(field)) &&
          (outcome.status !== "complete" || [outcome.stock_return, outcome.spy_return, outcome.excess_return].every(field => field !== null))))) {
    throw new Error("The journal returned an unsupported response. Please try again later.");
  }
  return value;
}

export async function loadSnapshot(accessToken?: string): Promise<PublicSnapshot> {
  if (!apiBase) {
    if (demoMode) return DEMO_SNAPSHOT;
    throw new Error("The application API is not configured.");
  }

  try {
    const response = await fetch(`${apiBase}/v1/snapshot`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) {
      if (response.status === 404 && accessToken) throw new AccountNotFoundError("Set up your competition account to continue.");
      if (!accessToken && demoMode) return DEMO_SNAPSHOT;
      throw new Error(response.status === 401 ? "Your session expired. Please sign in again." : "Account data is unavailable.");
    }
    return (await response.json()) as PublicSnapshot;
  } catch (error) {
    if (!accessToken && demoMode) return DEMO_SNAPSHOT;
    throw error;
  }
}

export type AccountSetup = {
  name: string;
  starting_capital: number;
  cash: number;
  snapshot_as_of: string;
};

export type AccountPositionInput = {
  ticker: string;
  side: "LONG" | "SHORT";
  quantity: number;
  average_price: number | null;
  exact_cost_basis: number | null;
};

export type AccountUpdate = AccountSetup & {
  positions: AccountPositionInput[];
  expected_portfolio_version: number;
};

export async function createAccount(accessToken: string, account: AccountSetup): Promise<PublicSnapshot> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/accounts`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(account),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    throw new Error(response.status === 401 ? "Your session expired. Please sign in again." : "Your account could not be saved.");
  }
  return (await response.json()) as PublicSnapshot;
}

export async function updateAccount(accessToken: string, account: AccountUpdate): Promise<PublicSnapshot> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/accounts/current`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(account),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    const message = response.status === 401
      ? "Your session expired. Please sign in again."
      : response.status === 409
        ? "Your portfolio changed in another session. Refresh and review the latest account before saving."
        : "Your account snapshot could not be saved.";
    throw new Error(message);
  }
  return (await response.json()) as PublicSnapshot;
}

export async function loadStrategies(accessToken: string): Promise<string[]> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/strategies`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error("Available strategies could not be loaded.");
  const payload = (await response.json()) as { strategies: string[] };
  return payload.strategies;
}

export type VixContext = {
  symbol: "VIX";
  value: number | null;
  price_as_of: string | null;
  status: "CURRENT" | "STALE" | "UNAVAILABLE";
  warning: string | null;
};

export async function loadVixContext(accessToken: string): Promise<VixContext> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/market/vix`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    signal: AbortSignal.timeout(25_000),
  });
  if (!response.ok) throw new Error("A dated VIX reading is unavailable.");
  return (await response.json()) as VixContext;
}

export async function loadPlanningProfiles(accessToken: string): Promise<PlanningProfile[]> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/planning-profiles`, {
    headers: { Authorization: `Bearer ${accessToken}` }, signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(response.status === 503 ? "Account settings need the cloud migration before they can be saved." : "Account settings could not be loaded.");
  return (await response.json()) as PlanningProfile[];
}

export type PlanningProfileInput = Pick<PlanningProfile, "name" | "mode" | "rules">;

export async function savePlanningProfile(
  accessToken: string, input: PlanningProfileInput, existing?: PlanningProfile,
): Promise<PlanningProfile> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/planning-profiles${existing ? `/${encodeURIComponent(existing.id)}` : ""}`, {
    method: existing ? "PUT" : "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(existing ? { ...input, expected_updated_at: existing.updated_at } : input),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    if (response.status === 409) throw new Error("Settings changed elsewhere. Reload before editing them again.");
    if (response.status === 503) throw new Error("Apply the account-settings cloud migration before saving.");
    throw new Error("Settings could not be saved. Check the rule values.");
  }
  return (await response.json()) as PlanningProfile;
}

export type TradingViewEstimate = {
  shares: number;
  position_value: number;
  cash_remaining: number;
  stop_watch_price: number;
  take_profit_watch_price: number;
  planned_risk_at_stop: number;
  risk_budget: number;
  warning: string;
};

export type TradingViewAutoPlan = TradingViewEstimate & {
  ticker: string;
  price_as_of: string;
  entry_price: number;
  atr_14: number;
  stop_distance_pct: number;
};

export async function loadTradingViewAutoPlan(
  accessToken: string,
  input: { ticker: string; available_cash: number; account_equity: number },
): Promise<TradingViewAutoPlan> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/plans/exit-guidance`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(input), signal: AbortSignal.timeout(55_000),
  });
  if (!response.ok) {
    let detail = "The automatic paper plan could not be calculated.";
    try { const body = (await response.json()) as { detail?: string }; if (typeof body.detail === "string") detail = body.detail; } catch {}
    throw new Error(detail);
  }
  return (await response.json()) as TradingViewAutoPlan;
}

export async function estimateTradingViewLong(
  accessToken: string,
  input: { entry_price: number; available_cash: number; account_equity: number; rules: TradingViewPlanningRules },
): Promise<TradingViewEstimate> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/planning-profiles/estimate`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(input), signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error("The estimate could not be calculated. Check cash, equity, price, and profile rules.");
  return (await response.json()) as TradingViewEstimate;
}

export async function buildDailyPlan(accessToken: string, strategy: string, targetPositionValue: number, minimumPrice = 5, universeMode: "EXPANDED" | "BROAD" = "EXPANDED", onProgress?: (progress: PlanProgress) => void): Promise<PublicSnapshot> {
  if (!apiBase) throw new Error("The application API is not configured.");
  return runPlanJob({
    baseUrl: apiBase, token: accessToken, onProgress,
    input: { request_id: crypto.randomUUID(), strategy, target_position_value: targetPositionValue, minimum_price: minimumPrice, universe_mode: universeMode },
  });
}

export async function loadPlanHistory(accessToken: string): Promise<PlanHistoryItem[]> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/plans/history`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error("Plan history could not be loaded.");
  return (await response.json()) as PlanHistoryItem[];
}

export async function loadPaperActions(accessToken: string): Promise<PaperAction[]> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/paper-actions`, {
    headers: { Authorization: `Bearer ${accessToken}` }, signal: AbortSignal.timeout(10_000),
  });
  if (response.status === 503) throw new Error("Paper action recording needs the new cloud migration.");
  if (!response.ok) throw new Error("Paper action history is unavailable. Recording stays disabled.");
  return (await response.json()) as PaperAction[];
}

export async function recordPaperAction(
  accessToken: string,
  input: { recommendation_id: string; quantity: number; expected_portfolio_version: number; idempotency_key: string },
): Promise<{ action_id: string; snapshot: PublicSnapshot }> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/paper-actions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(input), signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    let detail = "The paper action was not confirmed. Refresh your account before trying a new action.";
    try {
      const payload = (await response.json()) as { detail?: string };
      if (typeof payload.detail === "string") detail = payload.detail;
    } catch {}
    throw new Error(detail);
  }
  return (await response.json()) as { action_id: string; snapshot: PublicSnapshot };
}

export type AccountArchive = {
  schema_version: number;
  exported_at: string;
  scope: string;
  consistency: string;
  counts: Record<string, number>;
  tables: Record<string, Array<Record<string, unknown>>>;
};

export async function loadAccountArchive(accessToken: string): Promise<AccountArchive> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/account-export`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    signal: AbortSignal.timeout(180_000),
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("Your session expired. Please sign in again.");
    if (response.status === 503) throw new Error("Account export needs the latest cloud migrations.");
    throw new Error("Account export could not be completed. No partial archive was downloaded.");
  }
  return (await response.json()) as AccountArchive;
}

export async function changeRecommendationStatus(
  accessToken: string, recommendationId: string, eventType: "DISMISSED" | "REOPENED",
): Promise<PublicSnapshot> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/recommendations/${encodeURIComponent(recommendationId)}/events`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ event_type: eventType }),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    if (response.status === 409) throw new Error("This plan changed. Refresh before updating the recommendation.");
    throw new Error("Recommendation status could not be saved.");
  }
  return (await response.json()) as PublicSnapshot;
}
