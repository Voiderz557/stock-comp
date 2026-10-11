import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_PLANNING_SETTINGS, effectiveMinimumPrice, managedAppSettingsProfile, resolvePlanningSettings, savedAppSettingsProfile } from "../lib/planning-settings.ts";

test("legacy Any keeps the competition floor; lower filters are explicit research settings", () => {
  assert.equal(effectiveMinimumPrice(DEFAULT_PLANNING_SETTINGS), 5);
  assert.equal(effectiveMinimumPrice({ ...DEFAULT_PLANNING_SETTINGS, minimum_price_mode: "ANY", minimum_price: 99 }), 5);
  assert.equal(effectiveMinimumPrice({ ...DEFAULT_PLANNING_SETTINGS, minimum_price_mode: "NO_MINIMUM" }), 0);
  assert.equal(effectiveMinimumPrice({ ...DEFAULT_PLANNING_SETTINGS, minimum_price_mode: "CUSTOM", minimum_price: 12.5 }), 12.5);
  assert.equal(effectiveMinimumPrice({ ...DEFAULT_PLANNING_SETTINGS, minimum_price_mode: "CUSTOM", minimum_price: 4.99 }), 4.99);
  assert.throws(() => effectiveMinimumPrice({ ...DEFAULT_PLANNING_SETTINGS, minimum_price_mode: "CUSTOM", minimum_price: 0 }));
});

test("legacy saved competition rules get opt-in exit defaults", () => {
  const resolved = resolvePlanningSettings({ strategy: "Momentum V2", target_position_value: 5000 });
  assert.equal(resolved.strategy, "Momentum V2");
  assert.equal(resolved.stop_loss_enabled, false);
  assert.equal(resolved.take_profit_enabled, false);
  assert.equal(resolved.minimum_price_mode, "ANY");
  assert.equal(resolved.universe_mode, "EXPANDED");
});

test("saving app settings never selects an unrelated profile by name", () => {
  const legacy = { id: "legacy", name: "App settings", mode: "COMPETITION", rules: { strategy: "Baseline", target_position_value: 5000 } };
  const managed = { id: "managed", name: "App settings", mode: "COMPETITION", rules: { strategy: "Momentum V2", target_position_value: 10000, app_settings: true } };
  assert.equal(managedAppSettingsProfile([legacy]), undefined);
  assert.equal(savedAppSettingsProfile([legacy]), legacy);
  assert.equal(managedAppSettingsProfile([legacy, managed]), managed);
  assert.equal(savedAppSettingsProfile([legacy, managed]), managed);
});
