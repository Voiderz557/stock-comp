import assert from "node:assert/strict";
import test from "node:test";
import { compareSavedPlans } from "../lib/plan-comparison.ts";

function plan({ strategy = "Momentum V2", portfolioVersion = 1, warnings = [], recommendations = [] } = {}) {
  return { strategy, portfolio_version: portfolioVersion, warnings, recommendations };
}

test("dropped candidate explains saved evidence without inventing a cause", () => {
  const previous = plan({ recommendations: [{ ticker: "AAPL", action: "BUY", score: 75, reason: "Earlier breakout." }] });
  const current = plan({ strategy: "Baseline", portfolioVersion: 2, warnings: ["AAPL: price unavailable"], recommendations: [] });
  const changes = compareSavedPlans(current, previous);
  assert.equal(changes.dropped.length, 1);
  assert.match(changes.dropped[0].explanation, /Earlier breakout/);
  assert.match(changes.dropped[0].explanation, /Strategy changed/);
  assert.match(changes.dropped[0].explanation, /portfolio snapshot changed/);
  assert.match(changes.dropped[0].explanation, /AAPL: price unavailable/);
  assert.match(changes.dropped[0].explanation, /exact cause of removal is unknown/);
});

test("warning matching respects ticker boundaries", () => {
  const previous = plan({ recommendations: [{ ticker: "AI", action: "WAIT", score: 30, reason: "Mixed." }] });
  const current = plan({ warnings: ["AIG: price unavailable"] });
  assert.doesNotMatch(compareSavedPlans(current, previous).dropped[0].explanation, /AIG/);
});

test("saved current-plan diagnosis explains a dropped ticker without guessing", () => {
  const previous = plan({ recommendations: [{ ticker: "AAPL", action: "BUY", score: 75, reason: "Prior signal." }] });
  const current = {
    ...plan(),
    candidate_diagnostics: { AAPL: { status: "NOT_SELECTED", signal: "WAIT", score: 40, reason: "Momentum cooled." } },
  };
  const explanation = compareSavedPlans(current, previous).dropped[0].explanation;
  assert.match(explanation, /NOT_SELECTED \/ WAIT/);
  assert.match(explanation, /Momentum cooled/);
  assert.doesNotMatch(explanation, /exact cause of removal is unknown/);
});

test("added and changed recommendations remain deterministic", () => {
  const previous = plan({ recommendations: [{ ticker: "AAPL", action: "BUY", score: 60, reason: "Old" }] });
  const current = plan({ recommendations: [
    { ticker: "AAPL", action: "WAIT", score: 50, reason: "New" },
    { ticker: "MSFT", action: "BUY", score: 80, reason: "New" },
  ] });
  const changes = compareSavedPlans(current, previous);
  assert.deepEqual(changes.added.map((item) => item.ticker), ["MSFT"]);
  assert.deepEqual(changes.dropped, []);
  assert.deepEqual(changes.changed.map((item) => item.ticker), ["AAPL"]);
});
