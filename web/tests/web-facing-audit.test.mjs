import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);

function hooks() {
  let slots = [];
  let cursor = 0;
  let pending = [];
  const api = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
      return [slots[index], (next) => {
        slots[index] = typeof next === "function" ? next(slots[index]) : next;
      }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useMemo(factory) { cursor++; return factory(); },
    useEffect(callback, deps) {
      const index = cursor++;
      const previous = slots[index];
      if (!previous || !deps || deps.some((value, offset) => !Object.is(value, previous.deps?.[offset]))) {
        pending.push({ index, callback, deps });
      }
    },
  };
  return {
    api,
    render(Component, props, runEffects = true) {
      cursor = 0;
      pending = [];
      const tree = Component(props);
      if (runEffects) {
        for (const { index, callback, deps } of pending) {
          slots[index]?.cleanup?.();
          slots[index] = { deps, cleanup: callback() };
        }
      }
      return tree;
    },
  };
}

function loadComponent(path, reactHooks, mocks = {}, expose = "") {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  const localRequire = (name) => {
    if (name === "react") return reactHooks.api;
    if (name === "lucide-react") return new Proxy({}, { get: () => () => null });
    if (name in mocks) return mocks[name];
    if (name.startsWith("@/")) return {};
    return require(name);
  };
  new Function("require", "module", "exports", `${compiled}\n${expose}`)(localRequire, module, module.exports);
  return module.exports;
}

function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== "object" || !("props" in tree)) return [];
  return [tree, ...nodes(tree.props.children)];
}

function words(tree) {
  if (typeof tree === "string" || typeof tree === "number") return String(tree);
  if (Array.isArray(tree)) return tree.map(words).join("");
  return tree?.props ? words(tree.props.children) : "";
}

function label(tree, name) {
  const found = nodes(tree).find((node) => node.type === "label" && words(node).startsWith(name));
  assert.ok(found, `Missing ${name} label`);
  return nodes(found).find((node) => node.type === "input" || node.type === "select");
}

function button(tree, name) {
  const found = nodes(tree).find((node) => node.type === "button" && words(node).includes(name));
  assert.ok(found, `Missing ${name} button`);
  return found;
}

function snapshot({ owner = "Alice", position = null, marketDate = new Date().toISOString() } = {}) {
  return {
    snapshot_time: new Date().toISOString(), data_mode: "cloud",
    account: { name: owner, starting_capital: 100000, snapshot_as_of: new Date().toISOString(), portfolio_version: 1 },
    portfolio: { value: 100000, cash: 100000, long_exposure: 0, short_exposure: 0, unrealized_pnl: 0 },
    plan: { id: "plan", generated_at: new Date().toISOString(), market_as_of: marketDate, status: "CURRENT", strategy: "Test", summary: "One idea", warnings: [] },
    regime: { name: "NEUTRAL", label: "Neutral", reason: "Test", trend_strength: 50, market_breadth: 50, volatility: 50, preferred_strategies: [] },
    recommendations: [{ id: "rec", ticker: "MSFT", company: "Microsoft", action: "BUY", score: 80, strategy: "Test", reason: "Test", status: "OPEN", price: 100, move: 0, factor_details: { "Proposed Quantity": 5 } }],
    positions: position ? [position] : [],
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const tick = async () => { await Promise.resolve(); await Promise.resolve(); };

test("changing cost type clears the old amount and requires a value in the new units", async () => {
  const reactHooks = hooks();
  const { AccountEditor } = loadComponent("../components/account-editor.tsx", reactHooks);
  const saved = [];
  const props = {
    snapshot: snapshot({ position: { ticker: "MSFT", side: "LONG", quantity: 10, average_price: 12.5, exact_cost_basis: null } }),
    onSave: async (value) => saved.push(value), onCancel: () => {},
  };
  let tree = reactHooks.render(AccountEditor, props);
  assert.equal(label(tree, "Average price").props.value, "12.5");
  label(tree, "Cost type").props.onChange({ target: { value: "EXACT_COST_BASIS" } });
  tree = reactHooks.render(AccountEditor, props);
  assert.equal(label(tree, "Exact cost basis").props.value, "");
  assert.equal(button(tree, "Review changes").props.disabled, true);
  label(tree, "Exact cost basis").props.onChange({ target: { value: "125" } });
  tree = reactHooks.render(AccountEditor, props);
  assert.equal(button(tree, "Review changes").props.disabled, false);
  button(tree, "Review changes").props.onClick();
  tree = reactHooks.render(AccountEditor, props);
  button(tree, "Save snapshot").props.onClick();
  await tick();
  assert.deepEqual(saved[0].positions[0], { ticker: "MSFT", side: "LONG", quantity: 10, average_price: null, exact_cost_basis: 125 });

  const reverseHooks = hooks();
  const reverseEditor = loadComponent("../components/account-editor.tsx", reverseHooks).AccountEditor;
  const reverseProps = { ...props, snapshot: snapshot({ position: { ticker: "MSFT", side: "LONG", quantity: 10, average_price: null, exact_cost_basis: 125 } }) };
  tree = reverseHooks.render(reverseEditor, reverseProps);
  label(tree, "Cost type").props.onChange({ target: { value: "AVERAGE_PRICE" } });
  tree = reverseHooks.render(reverseEditor, reverseProps);
  assert.equal(label(tree, "Average price").props.value, "");
  assert.equal(button(tree, "Review changes").props.disabled, true);
});

function pageHarness(apiOverrides = {}) {
  const reactHooks = hooks();
  let authListener;
  const client = { auth: {
    getSession: async () => ({ data: { session: null } }),
    onAuthStateChange: (listener) => { authListener = listener; return { data: { subscription: { unsubscribe() {} } } }; },
    signOut: async () => {},
  } };
  const rules = { strategy: "Test", universe_mode: "EXPANDED", target_position_value: 5000, minimum_price_mode: "ANY", minimum_price: 5, stop_loss_enabled: false, take_profit_enabled: false };
  const api = {
    AccountNotFoundError: class AccountNotFoundError extends Error {},
    demoMode: true, loadSnapshot: async () => snapshot(), loadStrategies: async () => [], loadVixContext: async () => null,
    loadPlanHistory: async () => [], loadPaperActions: async () => [], loadPlanningProfiles: async () => [],
    ...apiOverrides,
  };
  const page = loadComponent("../app/page.tsx", reactHooks, {
    "@/lib/api": api,
    "@/lib/demo-data": { DEMO_SNAPSHOT: snapshot({ owner: "Demo" }) },
    "@/lib/planning-settings": {
      DEFAULT_COMPETITION_PROFILE: { rules }, resolvePlanningSettings: (value) => value, effectiveMinimumPrice: () => 5,
      managedAppSettingsProfile: (profiles) => profiles.find((item) => item.rules?.app_settings === true),
      savedAppSettingsProfile: (profiles) => profiles.find((item) => item.rules?.app_settings === true)
        ?? profiles.find((item) => item.name === "App settings"),
    },
    "@/lib/plan-comparison": { compareSavedPlans: () => ({ added: [], dropped: [], changed: [] }) },
    "@/lib/supabase": { getSupabaseClient: () => client, getOAuthAvailability: async () => ({ google: false, azure: false }), supabaseConfigured: true },
  }, "module.exports.__AppShell = AppShell; module.exports.__isPlanActionable = isPlanActionable; module.exports.__marketSessionDate = marketSessionDate;");
  return { page, reactHooks, auth: (event, token) => authListener(event, { access_token: token }) };
}

test("a new token hides the old account and rejects late history from the prior token", async () => {
  const loads = { alice: deferred(), bob: deferred() };
  const histories = { alice: deferred(), bob: deferred() };
  const { page, reactHooks, auth } = pageHarness({
    loadSnapshot: (token) => loads[token].promise,
    loadPlanHistory: (token) => histories[token].promise,
  });
  reactHooks.render(page.default);
  auth("SIGNED_IN", "alice");
  assert.equal(reactHooks.render(page.default).props.role, "status");
  loads.alice.resolve(snapshot({ owner: "Alice" }));
  await tick();
  let view = reactHooks.render(page.default);
  assert.equal(view.props.snapshot.account.name, "Alice");
  const oldBuildPlan = view.props.onBuildPlan;

  auth("TOKEN_REFRESHED", "bob");
  view = reactHooks.render(page.default);
  assert.equal(view.props.role, "status");
  await assert.rejects(oldBuildPlan("Test", 5000, 5, "EXPANDED"), /session changed/);
  loads.bob.resolve(snapshot({ owner: "Bob" }));
  await tick();
  view = reactHooks.render(page.default);
  assert.equal(view.props.snapshot.account.name, "Bob");
  assert.deepEqual(view.props.history, []);
  histories.alice.resolve([{ id: "alice-history" }]);
  await tick();
  assert.deepEqual(reactHooks.render(page.default).props.history, []);
  histories.bob.resolve([{ id: "bob-history" }]);
  await tick();
  assert.deepEqual(reactHooks.render(page.default).props.history, [{ id: "bob-history" }]);
});

test("failed account reload removes the previous user's snapshot and history", async () => {
  const loads = { alice: deferred(), bob: deferred() };
  const { page, reactHooks, auth } = pageHarness({ loadSnapshot: (token) => loads[token].promise, loadPlanHistory: async (token) => [{ id: `${token}-history` }] });
  reactHooks.render(page.default);
  auth("SIGNED_IN", "alice");
  reactHooks.render(page.default);
  loads.alice.resolve(snapshot({ owner: "Alice" }));
  await tick();
  const aliceView = reactHooks.render(page.default);
  assert.equal(aliceView.props.snapshot.account.name, "Alice");
  assert.deepEqual(aliceView.props.history, [{ id: "alice-history" }]);
  auth("SIGNED_IN", "bob");
  assert.equal(reactHooks.render(page.default).props.role, "status");
  loads.bob.reject(new Error("Account data is unavailable."));
  await tick();
  const view = reactHooks.render(page.default);
  assert.equal(view.type.name, "SignIn");
  assert.match(view.props.error, /Account data is unavailable/);
});

test("four-day market window controls recommendation and paper-action UI", async () => {
  const { page, reactHooks } = pageHarness();
  assert.equal(page.__marketSessionDate("2026-10-08T00:00:00+00:00"), "2026-10-08");
  assert.equal(page.__marketSessionDate(null), "unavailable");
  const now = Date.now();
  const freshPlan = snapshot({ marketDate: new Date(now - 4 * 86400000).toISOString() }).plan;
  assert.equal(page.__isPlanActionable(freshPlan, now), true);
  assert.equal(page.__isPlanActionable({ ...freshPlan, market_as_of: new Date(now - 4 * 86400000 - 1).toISOString() }, now), false);
  assert.equal(page.__isPlanActionable({ ...freshPlan, market_as_of: new Date(now + 1).toISOString() }, now), false);
  assert.equal(page.__isPlanActionable({ ...freshPlan, market_as_of: "invalid" }, now), false);
  assert.equal(page.__isPlanActionable({ ...freshPlan, status: "STALE" }, now), false);

  const current = snapshot();
  let records = 0;
  const props = {
    snapshot: current, source: "api", strategies: ["Test"], history: [], historyError: null,
    paperActions: [], paperActionsAvailable: true, paperActionsError: null, vix: null,
    savedSettings: null, settingsLoaded: true, settingsError: null,
    onSignOut() {}, onUpdateAccount: async () => {}, onBuildPlan: async () => {},
    onRecommendationEvent: async () => {}, onExportAccount: async () => {},
    onRecordPaperAction: async () => { records++; },
    onSaveSettings: async () => {}, onStopPreview: async () => {},
  };
  let tree = reactHooks.render(page.__AppShell, props, false);
  button(tree, "Inspect").props.onClick();
  tree = reactHooks.render(page.__AppShell, props, false);
  button(tree, "Review paper action").props.onClick();
  tree = reactHooks.render(page.__AppShell, props, false);
  assert.equal(button(tree, "Confirm paper action").props.disabled, false);

  const staleProps = { ...props, snapshot: snapshot({ marketDate: new Date(now - 5 * 86400000).toISOString() }) };
  tree = reactHooks.render(page.__AppShell, staleProps, false);
  assert.equal(button(tree, "Confirm paper action").props.disabled, true);
  assert.equal(nodes(tree).filter((node) => node.type === "button" && words(node).includes("Inspect")).length, 0);
  assert.match(words(tree), /Plan needs a fresh market date/);
  button(tree, "Confirm paper action").props.onClick();
  await tick();
  assert.equal(records, 0);
  assert.match(words(reactHooks.render(page.__AppShell, staleProps, false)), /no longer current/);
});

test("a saved holding-quote gap blocks paper actions before submission", async () => {
  const { page, reactHooks } = pageHarness();
  const current = snapshot();
  current.plan.warnings = ["Paper actions unavailable: AAPL: no scored holding review was saved. Build a fresh plan after resolving this gap."];
  let records = 0;
  const props = {
    snapshot: current, source: "api", strategies: ["Test"], history: [], historyError: null,
    paperActions: [], paperActionsAvailable: true, paperActionsError: null, vix: null,
    savedSettings: null, settingsLoaded: true, settingsError: null,
    onSignOut() {}, onUpdateAccount: async () => {}, onBuildPlan: async () => {},
    onRecommendationEvent: async () => {}, onExportAccount: async () => {},
    onRecordPaperAction: async () => { records++; },
    onSaveSettings: async () => {}, onStopPreview: async () => {},
  };
  let tree = reactHooks.render(page.__AppShell, props, false);
  button(tree, "Inspect").props.onClick();
  tree = reactHooks.render(page.__AppShell, props, false);
  assert.equal(button(tree, "Review paper action").props.disabled, true);
  assert.match(words(tree), /Paper actions unavailable/);
  assert.equal(records, 0);
});
