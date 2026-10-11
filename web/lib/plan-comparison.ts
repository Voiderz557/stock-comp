import type { PlanHistoryItem } from "./types";

type SavedRecommendation = PlanHistoryItem["recommendations"][number];

export type DroppedRecommendation = {
  ticker: string;
  prior: SavedRecommendation;
  explanation: string;
};

/** Compare saved evidence only; an absent ticker does not prove signal failure. */
export function compareSavedPlans(current: PlanHistoryItem, previous: PlanHistoryItem) {
  const before = new Map(previous.recommendations.map((item) => [item.ticker, item]));
  const after = new Map(current.recommendations.map((item) => [item.ticker, item]));
  const context: string[] = [];
  if (current.strategy !== previous.strategy) {
    context.push(`Strategy changed from ${previous.strategy} to ${current.strategy}.`);
  }
  if (current.portfolio_version !== previous.portfolio_version) {
    context.push("The portfolio snapshot changed between plans.");
  }
  return {
    added: current.recommendations.filter((item) => !before.has(item.ticker)),
    dropped: previous.recommendations.filter((item) => !after.has(item.ticker)).map((prior): DroppedRecommendation => {
      const matchingWarnings = current.warnings.filter((warning) =>
        new RegExp(`(^|[^A-Z0-9])${prior.ticker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^A-Z0-9]|$)`, "i").test(warning),
      );
      const currentDiagnostic = current.candidate_diagnostics?.[prior.ticker];
      const currentAssessment = currentDiagnostic
        ? currentDiagnostic.status === "RECOMMENDED"
          ? "The current diagnostics say this ticker was recommended, but it is absent from the saved recommendation list. The plan data is inconsistent."
          : `Current plan assessment (${currentDiagnostic.status}${currentDiagnostic.signal ? ` / ${currentDiagnostic.signal}` : ""}): ${currentDiagnostic.reason}`
        : "The current plan did not save a candidate-level rejection reason, so the exact cause of removal is unknown.";
      return {
        ticker: prior.ticker,
        prior,
        explanation: [
          `Previous saved signal: ${prior.action} (score ${prior.score}); ${prior.reason}`,
          ...context,
          ...matchingWarnings.map((warning) => `Current plan warning: ${warning}`),
          currentAssessment,
        ].join(" "),
      };
    }),
    changed: current.recommendations.filter((item) => {
      const prior = before.get(item.ticker);
      return prior && (prior.action !== item.action || prior.score !== item.score);
    }).map((item) => ({ ...item, prior: before.get(item.ticker)! })),
  };
}
