export type Recommendation = {
  id?: string;
  ticker: string;
  company: string;
  action: "BUY" | "SELL" | "WAIT" | "AVOID";
  score: number;
  strategy: string;
  reason: string;
  signal?: string;
  factor_details?: Record<string, unknown>;
  rank?: number;
  status?: "OPEN" | "DISMISSED" | "RECORDED";
  price_as_of?: string | null;
  price: number | null;
  move: number | null;
};

export type CompetitionPlanningRules = {
  strategy: string;
  universe_mode?: "EXPANDED" | "BROAD";
  target_position_value: number;
  minimum_price_mode?: "ANY" | "CUSTOM" | "NO_MINIMUM";
  minimum_price?: number;
  stop_loss_enabled?: boolean;
  take_profit_enabled?: boolean;
  app_settings?: boolean;
};

export type TradingViewPlanningRules = {
  max_position_value: number;
  risk_per_trade_pct: number;
  stop_loss_pct: number;
  take_profit_pct: number;
};

export type PlanningProfile = {
  id: string;
  name: string;
  mode: "COMPETITION" | "TRADINGVIEW";
  rules: CompetitionPlanningRules | TradingViewPlanningRules;
  created_at: string | null;
  updated_at: string | null;
};

export type PlanHistoryItem = {
  id: string;
  generated_at: string;
  market_as_of: string;
  portfolio_version: number;
  strategy: string;
  status: string;
  summary: string;
  warnings: string[];
  cash_at_generation: number | null;
  recommendations: Array<Pick<Recommendation, "ticker" | "action" | "signal" | "score" | "reason" | "rank" | "status">>;
  candidate_diagnostics?: Record<string, {
    status: "RECOMMENDED" | "QUALIFIED" | "NOT_SELECTED" | "UNAVAILABLE" | "ALLOCATION_BLOCKED";
    signal: string | null;
    score: number | null;
    reason: string;
  }>;
};

export type Position = {
  ticker: string;
  side: "LONG" | "SHORT";
  quantity: number;
  price: number | null;
  value: number | null;
  pnl: number | null;
  weight: number | null;
  average_price?: number | null;
  exact_cost_basis?: number | null;
  source?: "ACCOUNT_SNAPSHOT" | "PAPER_ACTION";
};

export type PaperAction = {
  id: string;
  plan_id: string | null;
  recommendation_id: string | null;
  action_type: "OPEN" | "INCREASE" | "DECREASE" | "CLOSE";
  ticker: string;
  direction: "LONG" | "SHORT";
  quantity: number;
  price: number;
  price_source: string;
  occurred_at: string;
  basis_released: number | null;
  realized_pnl: number | null;
};

export type PublicSnapshot = {
  snapshot_time: string;
  data_mode: "demo" | "cloud";
  account?: {
    name: string;
    starting_capital: number;
    snapshot_as_of: string;
    portfolio_version: number;
  };
  portfolio: {
    value: number | null;
    cash: number;
    long_exposure: number | null;
    short_exposure: number | null;
    unrealized_pnl: number | null;
  };
  plan: {
    id?: string | null;
    generated_at: string | null;
    market_as_of: string | null;
    status: string;
    strategy: string;
    summary: string;
    warnings: string[];
  };
  regime: {
    name: string;
    label: string;
    reason: string;
    trend_strength: number | null;
    market_breadth: number | null;
    volatility: number | null;
    preferred_strategies: string[];
  };
  recommendations: Recommendation[];
  positions: Position[];
};
