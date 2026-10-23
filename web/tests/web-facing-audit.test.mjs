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
    cleanup() { for (const slot of slots) slot?.cleanup?.(); },
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

function CompanyResearchStub() { return null; }
function RecommendationJournalStub() { return null; }

const journalFixture = () => ({
  asof: "2026-10-23", source_history: [{}], needed_symbols: ["SPY", "MSFT"], deferred_symbols: [], fetch_request: null,
  methodology: { return_units: "fraction", kind: "post_publication_close_research_return", price_basis: "loader_adjusted_close" },
  entries: [{ ticker: "MSFT", action: "SELL", status: "OPEN", strategy: "Test", plan_status: "STALE",
    generated_at: "2026-10-01T12:00:00Z", signal_date: "2026-09-30", reference_date: "2026-10-02",
    outcomes: [
      { sessions: 5, end_date: "2026-10-09", stock_return: 0.12, spy_return: 0.02, excess_return: 0.10,
        stock_null_reason: null, spy_null_reason: null, null_reason: null, status: "complete" },
      { sessions: 20, end_date: "2026-10-30", stock_return: null, spy_return: null, excess_return: null,
        stock_null_reason: "maturing", spy_null_reason: "maturing", null_reason: "maturing", status: "maturing" },
      { sessions: 60, end_date: null, stock_return: null, spy_return: null, excess_return: null,
        stock_null_reason: "missing_history", spy_null_reason: "missing_history", null_reason: "missing_history", status: "unavailable" },
    ] }],
});

test("research GET loaders require authentication, normalize tickers, avoid caches and reject invalid journal data", async () => {
  const originalFetch = globalThis.fetch, originalBase = process.env.NEXT_PUBLIC_API_BASE_URL;
  try {
    process.env.NEXT_PUBLIC_API_BASE_URL = "/api";
    const calls = []; let response = journalFixture(), status = 200;
    globalThis.fetch = async (url, options) => { calls.push({ url, options }); return Response.json(response, { status }); };
    const api = loadComponent("../lib/api.ts", hooks(), { "./demo-data": {}, "./plan-job-client": {} });
    await assert.rejects(api.loadCompanyResearch("", "MSFT"), /Sign in/);
    await assert.rejects(api.loadRecommendationJournal(""), /Sign in/);
    await assert.rejects(api.loadCompanyResearch("token", "../secret"), /valid stock ticker/);
    assert.equal(calls.length, 0);
    await api.loadCompanyResearch("fresh-token", " brk.b ");
    assert.equal(calls[0].url, "/api/v1/research/company/BRK.B");
    assert.equal((await api.loadRecommendationJournal("fresh-token")).entries[0].outcomes[0].stock_return, 0.12);
    assert.equal(calls[1].url, "/api/v1/recommendation-journal");
    for (const { options } of calls) {
      assert.equal(options.method, "GET"); assert.equal(options.headers.Authorization, "Bearer fresh-token");
      assert.equal(options.cache, "no-store"); assert.equal(options.redirect, "error");
    }
    response = journalFixture(); response.entries[0].outcomes[0].stock_return = "12%";
    await assert.rejects(api.loadRecommendationJournal("token"), /unsupported response/);
    for (const price_load_status of ["loaded", "partial", "busy", "timed_out", "unavailable", "not_needed"]) {
      response = { ...journalFixture(), price_load_status, warnings: ["PRIVATE_ERROR"] };
      assert.equal((await api.loadRecommendationJournal("token")).price_load_status, price_load_status);
    }
    for (const fields of [{ price_load_status: "PRIVATE_ERROR" }, { price_load_status: null }, { warnings: "PRIVATE_ERROR" }, { warnings: [42] }, { warnings: null }]) {
      response = { ...journalFixture(), ...fields };
      await assert.rejects(api.loadRecommendationJournal("token"), /unsupported response/);
    }
    response = { detail: "PRIVATE_TOKEN" }; status = 503;
    await assert.rejects(api.loadRecommendationJournal("token"), error => /unavailable/.test(error.message) && !/PRIVATE_TOKEN/.test(error.message));
    status = 401;
    await assert.rejects(api.loadCompanyResearch("token", "MSFT"), /session expired/);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL; else process.env.NEXT_PUBLIC_API_BASE_URL = originalBase;
  }
});

test("journal loads only on click, guards duplicate requests, and preserves real missing outcomes", async () => {
  const reactHooks = hooks(), pending = deferred(); let calls = 0;
  const { __JournalSession } = loadComponent("../components/recommendation-journal.tsx", reactHooks, {}, "module.exports.__JournalSession = JournalSession;");
  const props = { enabled: true, loadJournal: () => { calls++; return pending.promise; } };
  const view = () => reactHooks.render(__JournalSession, props);
  view(); view(); assert.equal(calls, 0);
  const click = button(view(), "Load journal").props.onClick;
  const request = click(); click(); assert.equal(calls, 1);
  pending.resolve(journalFixture()); await request;
  const output = words(view());
  assert.match(output, /Stock: 12.00%/); assert.match(output, /SPY: 2.00%/);
  assert.match(output, /10.00 percentage points/); assert.match(output, /Stock: Unavailable/);
  assert.match(output, /Still maturing/); assert.match(output, /Required price history is missing/);
  assert.match(output, /including SELL and AVOID/); assert.match(output, /1 supplied saved plans/);
  assert.equal(calls, 1);
});

test("journal handles empty results, private errors, disabled access and unmount", async () => {
  for (const mode of ["empty", "error", "disabled", "unmount"]) {
    const reactHooks = hooks(), pending = deferred(); let calls = 0;
    const { __JournalSession } = loadComponent("../components/recommendation-journal.tsx", reactHooks, {}, "module.exports.__JournalSession = JournalSession;");
    const props = { enabled: mode !== "disabled", loadJournal: () => { calls++; return pending.promise; } };
    const view = () => reactHooks.render(__JournalSession, props);
    const request = button(view(), "Load journal").props.onClick();
    if (mode === "disabled") { assert.equal(calls, 0); continue; }
    if (mode === "unmount") reactHooks.cleanup();
    if (mode === "error") pending.reject(new Error("PRIVATE_TOKEN"));
    else pending.resolve({ ...journalFixture(), entries: [] });
    await request;
    const output = words(view());
    assert.doesNotMatch(output, /PRIVATE_TOKEN/);
    if (mode === "empty") assert.match(output, /No saved recommendations/);
    if (mode === "error") { assert.match(output, /could not be loaded/); assert.equal(button(view(), "Load journal").props.disabled, false); }
    if (mode === "unmount") assert.doesNotMatch(output, /Observation cutoff/);
  }
});

test("journal explains every price-load state without echoing warnings and supports older responses", async () => {
  for (const [status, message] of [
    ["loaded", /Price history was loaded/], ["partial", /Only some requested price history/],
    ["busy", /price service is busy/], ["timed_out", /price request timed out/],
    ["unavailable", /price service is unavailable/], ["not_needed", /No price lookup was needed/],
    [undefined, /Price-loading status was not supplied/],
  ]) {
    const reactHooks = hooks(); let calls = 0;
    const { __JournalSession } = loadComponent("../components/recommendation-journal.tsx", reactHooks, {}, "module.exports.__JournalSession = JournalSession;");
    const response = journalFixture();
    if (status !== undefined) { response.price_load_status = status; response.warnings = ["PRIVATE_ERROR <script>secret</script>"]; }
    const props = { enabled: true, loadJournal: async () => { calls++; return response; } };
    const view = () => reactHooks.render(__JournalSession, props);
    assert.equal(view().props.className, "panel standalone-panel");
    assert.equal(calls, 0);
    await button(view(), "Load journal").props.onClick();
    assert.match(words(view()), message);
    assert.doesNotMatch(words(view()), /PRIVATE_ERROR|<script>/);
    if (status !== undefined) assert.match(words(view()), /coverage warnings/);
    view(); assert.equal(calls, 1);
  }
});

test("journal retains every returned status and reveals further rows without provider calls", async () => {
  const reactHooks = hooks(); let calls = 0;
  const { __JournalSession } = loadComponent("../components/recommendation-journal.tsx", reactHooks, {}, "module.exports.__JournalSession = JournalSession;");
  const journal = journalFixture();
  journal.entries = Array.from({ length: 101 }, (_, index) => ({ ...journal.entries[0],
    ticker: index === 100 ? "LAST" : "MSFT", status: ["OPEN", "DISMISSED", "RECORDED"][index % 3] }));
  const props = { enabled: true, loadJournal: async () => { calls++; return journal; } };
  const view = () => reactHooks.render(__JournalSession, props);
  await button(view(), "Load journal").props.onClick();
  const output = words(view());
  assert.match(output, /saved tickers and date ranges from the configured market-data provider/);
  for (const status of ["OPEN", "DISMISSED", "RECORDED"]) assert.ok(output.includes(`Recommendation status: ${status}`));
  assert.doesNotMatch(output, /LAST/);
  button(view(), "Show more saved recommendations").props.onClick();
  assert.match(words(view()), /LAST/);
  assert.equal(calls, 1);
});

test("research and journal callbacks use the current session and reject prior-session completions", async () => {
  const research = deferred(), journal = deferred(), calls = [];
  const { page, reactHooks, auth } = pageHarness({
    loadCompanyResearch: (token, ticker) => { calls.push([token, ticker]); return research.promise; },
    loadRecommendationJournal: token => { calls.push([token]); return journal.promise; },
  });
  reactHooks.render(page.default); auth("SIGNED_IN", "alice"); await tick();
  const first = reactHooks.render(page.default);
  assert.equal(calls.length, 0);
  const oldResearch = first.props.onLoadResearch("MSFT"), oldJournal = first.props.onLoadJournal();
  assert.deepEqual(calls, [["alice", "MSFT"], ["alice"]]);
  auth("TOKEN_REFRESHED", "bob"); await tick();
  const second = reactHooks.render(page.default);
  assert.notEqual(first.key, second.key);
  await assert.rejects(first.props.onLoadResearch("MSFT"), /session changed/i);
  await assert.rejects(first.props.onLoadJournal(), /session changed/i);
  research.resolve({ ticker: "MSFT" }); journal.resolve(journalFixture());
  await assert.rejects(oldResearch, /session changed/i); await assert.rejects(oldJournal, /session changed/i);
  await second.props.onLoadResearch("AAPL"); await second.props.onLoadJournal();
  assert.deepEqual(calls.slice(-2), [["bob", "AAPL"], ["bob"]]);
});

test("Research tab, selected ticker modal and History wire explicit loaders; demos disable both", () => {
  const { page, reactHooks } = pageHarness(); let calls = 0;
  const props = { snapshot: snapshot(), source: "api", strategies: [], history: [], historyError: null,
    paperActions: [], paperActionsAvailable: true, paperActionsError: null, vix: null,
    savedSettings: null, settingsLoaded: true, settingsError: null,
    onLoadResearch: async () => { calls++; }, onLoadJournal: async () => { calls++; } };
  const view = () => reactHooks.render(page.__AppShell, props, false);
  button(view(), "Inspect").props.onClick();
  const modalResearch = nodes(view()).find(node => node.type === CompanyResearchStub);
  assert.equal(modalResearch.props.ticker, "MSFT");
  assert.equal(modalResearch.props.loadResearch, props.onLoadResearch);
  nodes(view()).find(node => node.props["aria-label"] === "Close details").props.onClick();
  button(view(), "Research").props.onClick();
  label(view(), "Research ticker").props.onChange({ target: { value: " aapl " } });
  const research = nodes(view()).find(node => node.type === CompanyResearchStub);
  assert.equal(research.props.ticker, "AAPL"); assert.equal(research.props.enabled, true);
  props.source = "demo";
  assert.equal(nodes(view()).find(node => node.type === CompanyResearchStub).props.enabled, false);
  button(view(), "History").props.onClick();
  const journal = nodes(view()).find(node => node.type === RecommendationJournalStub);
  assert.equal(journal.props.enabled, false); assert.equal(journal.props.loadJournal, props.onLoadJournal);
  assert.equal(calls, 0);
});

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
    "@/components/company-research": { CompanyResearch: CompanyResearchStub },
    "@/components/recommendation-journal": { RecommendationJournal: RecommendationJournalStub },
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
  }, "module.exports.__SignIn = SignIn; module.exports.__AppShell = AppShell; module.exports.__isPlanActionable = isPlanActionable; module.exports.__marketSessionDate = marketSessionDate;");
  return { page, reactHooks, auth: (event, token) => authListener(event, { access_token: token }) };
}

function signInHarness(overrides = {}) {
  const { page, reactHooks } = pageHarness();
  const props = { onDemo() {}, onOAuth: async () => {}, onMagicLink: async () => {}, onGoogleError() {},
    googleReady: true, microsoftReady: false, magicReady: true, demoReady: false, error: null, ...overrides };
  return { props, view: () => reactHooks.render(page.__SignIn, props, false) };
}

test("production login hides unavailable provider and demo buttons", () => {
  const h = signInHarness();
  const tree = h.view();
  assert.ok(button(tree, "Continue with Google"));
  assert.ok(button(tree, "Send sign-in link"));
  assert.equal(nodes(tree).some(node => node.type === "button" && /Microsoft|demo account/.test(words(node))), false);
  assert.match(words(tree), /Nothing here places real trades/);
  assert.match(words(tree), /Anyone can open this website/);
  assert.match(words(tree), /Use the same Google account or email as before/);
  assert.doesNotMatch(words(tree), /owner-private preview/);
  assert.equal(nodes(tree).some(node => node.type === "input" && node.props.type === "password"), false);
});
test("get started changes presentation only and never creates an account", () => {
  let calls = 0;
  const h = signInHarness({ onOAuth: async () => { calls++; }, onMagicLink: async () => { calls++; } });
  button(h.view(), "Get started").props.onClick();
  assert.match(words(h.view()), /Start your workspace/);
  assert.match(words(h.view()), /same sign-in restores it/);
  assert.equal(calls, 0);
  button(h.view(), "Log in").props.onClick();
  assert.match(words(h.view()), /Welcome back/);
});
test("email submission is trimmed, guarded against repeats, and confirms inbox guidance", async () => {
  const pending = deferred();
  const emails = [];
  const h = signInHarness({ onMagicLink: email => { emails.push(email); return pending.promise; } });
  const input = nodes(h.view()).find(node => node.type === "input");
  input.props.onChange({ target: { value: " fixture@example.com " } });
  const submit = nodes(h.view()).find(node => node.type === "form").props.onSubmit;
  const first = submit({ preventDefault() {} });
  await submit({ preventDefault() {} });
  assert.equal(emails.length, 1);
  assert.equal(emails[0], "fixture@example.com");
  assert.equal(button(h.view(), "Continue with Google").props.disabled, true);
  assert.match(words(h.view()), /Sending your link/);
  pending.resolve();
  await first;
  assert.match(words(h.view()), /Check your inbox/);
  assert.ok(nodes(h.view()).some(node => node.props.role === "status"));
});
test("email failure is an alert, not a green success, and remains retryable", async () => {
  const h = signInHarness({ onMagicLink: async () => { throw new Error("Please wait before retrying."); } });
  nodes(h.view()).find(node => node.type === "input").props.onChange({ target: { value: "fixture@example.com" } });
  await nodes(h.view()).find(node => node.type === "form").props.onSubmit({ preventDefault() {} });
  const alert = nodes(h.view()).find(node => node.props.role === "alert");
  assert.equal(alert.props.className, "auth-error");
  assert.match(words(alert), /Please wait/);
  assert.equal(button(h.view(), "Send sign-in link").props.disabled, false);
});
test("OAuth double clicks are guarded and controls recover after completion", async () => {
  const pending = deferred();
  const providers = [];
  const h = signInHarness({ onOAuth: provider => { providers.push(provider); return pending.promise; } });
  const open = button(h.view(), "Continue with Google").props.onClick;
  open(); open();
  assert.equal(providers.length, 1);
  assert.match(words(h.view()), /Opening Google/);
  assert.equal(button(h.view(), "Send sign-in link").props.disabled, true);
  pending.resolve(); await tick();
  assert.equal(button(h.view(), "Continue with Google").props.disabled, false);
});
test("email-only configuration remains usable and missing configuration fails honestly", () => {
  const h = signInHarness({ googleReady: false });
  assert.ok(button(h.view(), "Send sign-in link"));
  assert.equal(nodes(h.view()).some(node => node.type === "button" && words(node).includes("Google")), false);
  const unavailable = signInHarness({ googleReady: false, magicReady: false });
  assert.match(words(unavailable.view()), /Sign-in is unavailable/);
});
test("configured Microsoft and explicitly enabled demos remain accessible", () => {
  const h = signInHarness({ microsoftReady: true, demoReady: true });
  assert.ok(button(h.view(), "Continue with Microsoft"));
  assert.ok(button(h.view(), "Explore demo account"));
});

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

test("initial build is explicit, confirmed action refreshes once, and uncertain retry keeps its key", async () => {
  const { page, reactHooks } = pageHarness();
  let builds = 0;
  const attempts = [];
  const fresh = snapshot();
  fresh.recommendations[0].price_as_of = fresh.plan.market_as_of;
  fresh.recommendations[0].reason = "5/6 strategies signal BUY; this is not a trade order.";
  const props = {
    snapshot: { ...fresh, plan: { ...fresh.plan, status: "STALE" } },
    source: "api", strategies: ["Test"], history: [], historyError: null,
    paperActions: [], paperActionsAvailable: true, paperActionsError: null, vix: null,
    savedSettings: null, settingsLoaded: true, settingsError: null,
    onSignOut() {}, onUpdateAccount: async () => {}, onRecommendationEvent: async () => {},
    onExportAccount: async () => {}, onSaveSettings: async () => {}, onStopPreview: async () => {},
    onBuildPlan: async (...args) => {
      builds++;
      assert.deepEqual(args, ["Test", 5000, 5, "EXPANDED"]);
      props.snapshot = builds === 1 ? fresh : { ...fresh, recommendations: [] };
    },
    onRecordPaperAction: async (...args) => {
      attempts.push(args);
      if (attempts.length === 1) throw new Error("Response lost; refresh before a new action.");
      props.snapshot = { ...fresh, plan: { ...fresh.plan, status: "STALE" }, recommendations: [] };
    },
  };
  const view = () => reactHooks.render(page.__AppShell, props, false);
  button(view(), "Today's plan").props.onClick();
  assert.equal(builds, 0, "Navigation and rerenders must not generate a plan");
  button(view(), "Rebuild plan").props.onClick();
  await tick();
  assert.equal(builds, 1);
  button(view(), "Inspect").props.onClick();
  assert.match(words(view()), /5\/6 strategies/);
  assert.equal(attempts.length, 0);
  button(view(), "Review paper action").props.onClick();
  assert.match(words(view()), /Explicit paper action review/);
  assert.match(words(view()), /Estimated fill: the saved daily close/i);
  assert.equal(attempts.length, 0, "Review must not record an action");
  button(view(), "Confirm paper action").props.onClick();
  await tick();
  await tick();
  assert.match(words(view()), /Response lost/);
  button(view(), "Confirm paper action").props.onClick();
  await tick();
  assert.equal(attempts.length, 2);
  assert.deepEqual(attempts[1], attempts[0], "Retry must retain the same recommendation, quantity, version and key");
  assert.equal(attempts[0][1], 5, "The proposed whole-share quantity should prefill the review");
  assert.match(attempts[0][3], /^[0-9a-f-]{36}$/i);
  assert.equal(nodes(view()).some(node => node.props.role === "dialog"), false);
  assert.equal(nodes(view()).some(node => node.type === "button" && words(node).includes("Inspect")), false,
    "The replacement plan must not reuse old actionable candidates");
  assert.equal(builds, 2, "One automatic refresh follows the confirmed action, not the uncertain attempt");
});

function postTradeHarness(action = "BUY", failRefresh = false) {
  const { page, reactHooks } = pageHarness();
  const position = { ticker: "MSFT", side: "LONG", quantity: 5, price: 100, value: 500, pnl: 0, weight: 0.5 };
  const initial = snapshot({ position: action === "SELL" ? position : null });
  initial.recommendations[0].action = action;
  const gate = deferred();
  let writes = 0;
  let scans = 0;
  const props = {
    snapshot: initial, source: "api", strategies: ["Test"], history: [], historyError: null,
    paperActions: [], paperActionsAvailable: true, paperActionsError: null, vix: null,
    savedSettings: null, settingsLoaded: true, settingsError: null,
    onSignOut() {}, onUpdateAccount: async () => {}, onRecommendationEvent: async () => {},
    onExportAccount: async () => {}, onSaveSettings: async () => {}, onStopPreview: async () => {},
    onRecordPaperAction: async () => {
      writes++;
      props.snapshot = { ...initial, account: { ...initial.account, portfolio_version: 2 },
        portfolio: { ...initial.portfolio, cash: action === "BUY" ? 99500 : 100500 },
        positions: action === "BUY" ? [position] : [], recommendations: [],
        plan: { ...initial.plan, status: "STALE" } };
    },
    onBuildPlan: async () => {
      scans++;
      assert.equal(props.snapshot.account.portfolio_version, 2, "Scan follows the confirmed new account state");
      await gate.promise;
      if (failRefresh && scans === 1) throw new Error("Price service unavailable");
      props.snapshot = { ...props.snapshot, plan: { ...initial.plan, id: "replacement", status: "CURRENT" },
        recommendations: [
          ...(action === "BUY" ? [{ ...initial.recommendations[0], id: "holding-review", action: "WAIT", reason: "Trend remains positive; keep the holding under review." }] : []),
          { ...initial.recommendations[0], id: "next-buy", ticker: "NVDA", action: "BUY", reason: "Next qualified stock within remaining cash." },
        ] };
    },
  };
  const view = () => reactHooks.render(page.__AppShell, props, false);
  const confirm = () => {
    button(view(), "Inspect").props.onClick();
    button(view(), "Review paper action").props.onClick();
    const click = button(view(), "Confirm paper action").props.onClick;
    click(); click(); // Same-render duplicate click must be blocked synchronously.
  };
  return { view, confirm, gate, props, counts: () => ({ writes, scans }), page, reactHooks };
}

for (const action of ["BUY", "SELL"]) test(`${action} updates cash/holdings then refreshes only recommendations once`, async () => {
  const h = postTradeHarness(action);
  h.confirm();
  await tick();
  assert.deepEqual(h.counts(), { writes: 1, scans: 1 });
  assert.match(words(h.view()), /Paper trade recorded — updating your plan/);
  assert.match(words(h.view()), /New buys need a refreshed plan/);
  assert.equal(nodes(h.view()).some(node => node.props.role === "dialog"), false);
  assert.match(words(h.view()), action === "BUY" ? /Cash remaining: \$99,500/ : /Cash remaining: \$100,500/);
  h.gate.resolve(); await tick(); await tick(); await tick();
  const tree = h.view();
  assert.match(words(tree), /Paper trade recorded — plan refreshed/);
  const buys = nodes(tree).find(node => node.type === "section" && words(node).includes("New buys to review") && node.props.className === "panel recommendations-panel");
  assert.match(words(buys), /NVDA/);
  assert.doesNotMatch(words(buys), /MSFT/);
  if (action === "BUY") assert.match(words(tree), /MSFT · HOLD \/ WAIT/);
  else assert.match(words(tree), /No open positions/);
  h.view(); h.view();
  assert.deepEqual(h.counts(), { writes: 1, scans: 1 }, "Rerenders never place a trade or start another scan");
});

test("failed post-trade scan preserves the saved trade and retries only the scan", async () => {
  const h = postTradeHarness("BUY", true);
  h.confirm(); await tick(); h.gate.resolve(); await tick(); await tick(); await tick();
  assert.match(words(h.view()), /Paper trade recorded — plan still needs refreshing/);
  assert.match(words(h.view()), /Do not record the same trade or account update again/);
  assert.match(words(h.view()), /Price service unavailable/);
  assert.match(words(h.view()), /Cash remaining: \$99,500/);
  assert.match(words(h.view()), /MSFT · REVIEW UNAVAILABLE/);
  button(h.view(), "Rebuild plan").props.onClick(); await tick(); await tick(); await tick();
  assert.deepEqual(h.counts(), { writes: 1, scans: 2 });
  assert.match(words(h.view()), /plan refreshed/);
});

test("holdings are reviewed even with zero cash; HOLD is not an executable sell", async () => {
  const h = postTradeHarness();
  h.props.snapshot = snapshot({ position: { ticker: "MSFT", side: "LONG", quantity: 10, price: 100, value: 1000, pnl: 0, weight: 1 } });
  h.props.snapshot.portfolio.cash = 0;
  h.props.snapshot.recommendations[0].action = "WAIT";
  h.props.snapshot.recommendations[0].reason = "Hold while the trend remains supportive.";
  button(h.view(), "Today's plan").props.onClick();
  assert.match(words(h.view()), /MSFT · HOLD \/ WAIT/);
  assert.match(words(h.view()), /Hold while the trend remains supportive/);
  assert.match(words(h.view()), /No new-stock buys qualify/);
  button(h.view(), "Inspect MSFT review").props.onClick();
  assert.equal(button(h.view(), "Review paper action").props.disabled, true);
  assert.deepEqual(h.counts(), { writes: 0, scans: 0 });
});

test("sell reviews and missing holding reviews remain distinct from new buys", () => {
  const h = postTradeHarness("SELL");
  button(h.view(), "Today's plan").props.onClick();
  assert.match(words(h.view()), /MSFT · REVIEW SELL/);
  h.props.snapshot = { ...h.props.snapshot, recommendations: [] };
  assert.match(words(h.view()), /MSFT · REVIEW UNAVAILABLE/);
  assert.match(words(h.view()), /Missing data is not a sell signal/);
  h.props.snapshot.recommendations = [{ ...snapshot().recommendations[0], action: "WAIT", signal: "UNAVAILABLE" }];
  assert.match(words(h.view()), /MSFT · REVIEW UNAVAILABLE/);
  assert.doesNotMatch(words(h.view()), /MSFT · HOLD \/ WAIT/);
});

test("manual account snapshot refreshes recommendations without inventing a trade", async () => {
  const h = postTradeHarness();
  let updates = 0;
  h.props.onUpdateAccount = async () => {
    updates++;
    h.props.snapshot = { ...h.props.snapshot, account: { ...h.props.snapshot.account, portfolio_version: 2 },
      plan: { ...h.props.snapshot.plan, status: "STALE" }, recommendations: [] };
  };
  button(h.view(), "Settings").props.onClick();
  button(h.view(), "Edit account & holdings").props.onClick();
  const editor = nodes(h.view()).find(node => typeof node.props.onSave === "function" && node.props.snapshot);
  assert.ok(editor);
  const saving = editor.props.onSave({});
  await tick();
  assert.equal(updates, 1);
  assert.deepEqual(h.counts(), { writes: 0, scans: 1 });
  assert.match(words(h.view()), /Account snapshot saved — updating your plan/);
  h.gate.resolve(); await saving;
  assert.match(words(h.view()), /Account snapshot saved — plan refreshed/);
  assert.doesNotMatch(words(h.view()), /Paper trade recorded/);
});

test("an account changed again during a successful scan is not labelled refreshed", async () => {
  const h = postTradeHarness();
  h.props.onBuildPlan = async () => h.props.snapshot;
  h.confirm(); await tick(); await tick(); await tick();
  assert.match(words(h.view()), /plan still needs refreshing/);
  assert.match(words(h.view()), /changed again during the scan/);
  assert.equal(h.counts().writes, 1);
});

test("a held-stock addition keeps its independent HOLD review and is not a new-stock pick", () => {
  const h = postTradeHarness("SELL");
  const rec = h.props.snapshot.recommendations[0];
  rec.action = "BUY";
  rec.factor_details["Holding Review"] = { Action: "KEEP", Reason: "Zero exit votes; review the existing holding." };
  button(h.view(), "Today's plan").props.onClick();
  const text = words(h.view());
  assert.match(text, /MSFT · HOLD \/ WAIT/);
  assert.match(text, /Zero exit votes/);
  assert.match(text, /Separate addition proposal/);
  assert.match(text, /No new-stock buys qualify/);
});
