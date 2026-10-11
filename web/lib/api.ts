import { DEMO_SNAPSHOT } from "./demo-data";
import type { PaperAction, PlanHistoryItem, PlanningProfile, PublicSnapshot, TradingViewPlanningRules } from "./types";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "";
export const demoMode = (process.env.NEXT_PUBLIC_DEMO_MODE ?? "true").toLowerCase() === "true";

export class AccountNotFoundError extends Error {}

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

export async function buildDailyPlan(accessToken: string, strategy: string, targetPositionValue: number, minimumPrice = 5, universeMode: "EXPANDED" | "BROAD" = "EXPANDED"): Promise<PublicSnapshot> {
  if (!apiBase) throw new Error("The application API is not configured.");
  const response = await fetch(`${apiBase}/v1/plans/current`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ strategy, target_position_value: targetPositionValue, minimum_price: minimumPrice, universe_mode: universeMode }),
    signal: AbortSignal.timeout(180_000),
  });
  if (!response.ok) {
    let detail = "Today’s plan could not be generated.";
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {}
    throw new Error(detail);
  }
  return (await response.json()) as PublicSnapshot;
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
