import type { CompetitionPlanningRules, PlanningProfile } from "./types";

export const COMPETITION_PRICE_FLOOR = 5;
export const DEFAULT_PLANNING_SETTINGS: Required<CompetitionPlanningRules> = {
  strategy: "All strategies — consensus",
  universe_mode: "EXPANDED",
  target_position_value: 10_000,
  minimum_price_mode: "ANY",
  minimum_price: COMPETITION_PRICE_FLOOR,
  stop_loss_enabled: false,
  take_profit_enabled: false,
  app_settings: false,
};

export const DEFAULT_COMPETITION_PROFILE: PlanningProfile = {
  id: "competition-default",
  name: "Competition (standard)",
  mode: "COMPETITION",
  rules: DEFAULT_PLANNING_SETTINGS,
  created_at: null,
  updated_at: null,
};

/** Legacy saved profiles remain readable; missing new preferences get safe defaults. */
export function resolvePlanningSettings(rules: CompetitionPlanningRules): Required<CompetitionPlanningRules> {
  return { ...DEFAULT_PLANNING_SETTINGS, ...rules };
}

/** Preserve a legacy named profile, but only update one explicitly saved by this app. */
export function managedAppSettingsProfile(profiles: PlanningProfile[]): PlanningProfile | undefined {
  return profiles.find((profile) => profile.mode === "COMPETITION" && "app_settings" in profile.rules && profile.rules.app_settings === true);
}

export function savedAppSettingsProfile(profiles: PlanningProfile[]): PlanningProfile | undefined {
  return managedAppSettingsProfile(profiles) ?? profiles.find((profile) => profile.mode === "COMPETITION" && profile.name === "App settings");
}

/** Existing "Any" settings retain their $5 meaning; zero is explicit research-only. */
export function effectiveMinimumPrice(settings: CompetitionPlanningRules): number {
  if (settings.minimum_price_mode === "NO_MINIMUM") return 0;
  if (settings.minimum_price_mode !== "CUSTOM") return COMPETITION_PRICE_FLOOR;
  const price = settings.minimum_price;
  if (typeof price !== "number" || !Number.isFinite(price) || price <= 0 || price > 1_000_000) {
    throw new Error("Custom minimum price must be greater than $0 and at most $1,000,000.");
  }
  return price;
}
