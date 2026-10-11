import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { jsx } = require("react/jsx-runtime");
const publicConfig = {
  supabase_url: "https://example.supabase.co",
  supabase_publishable_key: "sb_publishable_test_fixture",
  storage_configured: true,
  compute_configured: true,
  compute_message: "",
};
const waitingMessage = "Daily scanning and automatic stop guidance are waiting for the strategy service setup. Saved accounts and history remain available.";
const tick = () => new Promise((resolve) => setImmediate(resolve));
const response = (config = publicConfig) => new Response(JSON.stringify(config), {
  headers: { "content-type": "application/json" },
});

function load(path, mocks = {}, globals = {}) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  runInNewContext(compiled, {
    module, exports: module.exports,
    require: (name) => name in mocks ? mocks[name] : require(name),
    process: { env: {} }, URL, TextDecoder, AbortController, AbortSignal, atob,
    ...globals,
  }, { filename: path });
  return module.exports;
}

function hooks() {
  const slots = [];
  let cursor = 0;
  let pending = [];
  return {
    api: {
      useState(initial) {
        const index = cursor++;
        if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
        return [slots[index], (next) => {
          slots[index] = typeof next === "function" ? next(slots[index]) : next;
        }];
      },
      useEffect(callback, deps) {
        const index = cursor++;
        const previous = slots[index];
        if (!previous || deps.some((value, offset) => !Object.is(value, previous.deps[offset]))) {
          pending.push({ index, callback, deps });
        }
      },
    },
    render(Component, props = {}, effects = true) {
      cursor = 0;
      pending = [];
      const tree = Component(props);
      if (effects) {
        for (const { index, callback, deps } of pending) {
          slots[index]?.cleanup?.();
          slots[index] = { deps, cleanup: callback() };
        }
      }
      return tree;
    },
    cleanup(restart = false) {
      for (const [index, value] of slots.entries()) {
        if (value?.deps) {
          value.cleanup?.();
          if (restart) delete slots[index];
        }
      }
    },
  };
}

function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree?.props) return [];
  return [tree, ...nodes(tree.props.children)];
}

function words(tree) {
  if (typeof tree === "string" || typeof tree === "number") return String(tree);
  if (Array.isArray(tree)) return tree.map(words).join("");
  return tree?.props ? words(tree.props.children) : "";
}

function harness(fetcher = async () => response(), flag = "true", env = {}) {
  const reactHooks = hooks();
  const timers = new Map();
  const requests = [];
  const creations = [];
  let timerId = 0;
  let stops = 0;
  let signOuts = 0;
  const client = { auth: {
    stopAutoRefresh: () => { stops++; },
    signOut: () => { signOuts++; },
    getSession: async () => ({ data: { session: null } }),
  } };
  const globals = {
    process: { env: { ...env, NEXT_PUBLIC_HOSTED_RUNTIME_CONFIG: flag } },
    window: {},
    fetch: (...args) => { requests.push(args); return fetcher(...args); },
    setTimeout: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; },
    clearTimeout: (id) => timers.delete(id),
  };
  const supabase = load("../lib/supabase.ts", {
    "@supabase/supabase-js": { createClient: (...args) => { creations.push(args); return client; } },
  }, globals);
  const component = load("../components/hosted-bootstrap.tsx", {
    react: reactHooks.api, "@/lib/supabase": supabase,
  }, globals).HostedBootstrap;
  const children = jsx("div", { children: "Saved account child" });
  return {
    ...reactHooks, component, supabase, children, client, globals, requests, creations, timers,
    view: (effects = true) => reactHooks.render(component, { children }, effects),
    hasChildren: (tree) => nodes(tree).includes(children),
    stops: () => stops, signOuts: () => signOuts,
  };
}

test("hosted server HTML and first hydration gate children without a request", () => {
  const h = harness();
  const serverComponent = load("../components/hosted-bootstrap.tsx", {
    react: React, "@/lib/supabase": h.supabase,
  }, { ...h.globals, window: undefined }).HostedBootstrap;
  const html = renderToStaticMarkup(jsx(serverComponent, { children: h.children }));
  const first = h.view(false);
  assert.equal(renderToStaticMarkup(first), html);
  assert.match(html, /Connecting to your saved accounts/);
  assert.equal(h.hasChildren(first), false);
  assert.equal(h.requests.length, 0);
  assert.equal(h.creations.length, 0);
});

test("successful config sets live Supabase settings before children and signed-out restoration", async () => {
  let complete;
  const h = harness(() => new Promise((resolve) => { complete = resolve; }));
  assert.equal(h.hasChildren(h.view()), false);
  assert.equal(h.supabase.getSupabaseClient(), null);
  assert.equal(h.requests[0][0], "/api/v1/config");
  assert.equal(h.requests[0][1].cache, "no-store");
  assert.equal(h.requests[0][1].credentials, "same-origin");
  assert.equal(h.requests[0][1].redirect, "error");
  assert.equal([...h.timers.values()][0].delay, 10_000);
  complete(response());
  await tick();
  assert.equal(h.supabase.supabaseConfigured, true);
  assert.equal(h.supabase.supabaseUrl, publicConfig.supabase_url);
  assert.equal(h.supabase.publishableKey, publicConfig.supabase_publishable_key);
  assert.equal(h.hasChildren(h.view()), true);
  const client = h.supabase.getSupabaseClient();
  assert.equal(client, h.client);
  assert.equal((await client.auth.getSession()).data.session, null);
  assert.deepEqual(JSON.parse(JSON.stringify(h.creations[0][2].auth)), {
    flowType: "pkce", detectSessionInUrl: false, persistSession: true, autoRefreshToken: true,
  });
  assert.equal(h.timers.size, 0);
  assert.equal(h.signOuts(), 0);
});

test("compute missing shows the honest fixed banner, never backend text or credentials", async () => {
  const h = harness(async () => response({
    ...publicConfig, compute_configured: false,
    compute_message: `${publicConfig.supabase_publishable_key} PRIVATE_TOKEN_MARKER <script>unsafe</script>`,
  }));
  h.view();
  await tick();
  const tree = h.view();
  assert.equal(h.hasChildren(tree), true);
  assert.match(words(tree), new RegExp(waitingMessage.replaceAll(".", "\\.")));
  assert.doesNotMatch(words(tree), /sb_publishable_|PRIVATE_TOKEN_MARKER|<script>/);
});

test("missing config and storage block even build-time credentials; Retry succeeds", async () => {
  for (const first of [() => new Response("PRIVATE_TOKEN_MARKER", { status: 503 }),
    () => response({ ...publicConfig, storage_configured: false, supabase_url: "", supabase_publishable_key: "" })]) {
    let attempts = 0;
    const h = harness(async () => ++attempts === 1 ? first() : response(), "true", {
      NEXT_PUBLIC_SUPABASE_URL: "https://build.example.com",
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_build_fixture",
    });
    h.view();
    await tick();
    const failed = h.view();
    assert.equal(h.hasChildren(failed), false);
    assert.match(words(failed), /configuration or account storage is unavailable/);
    assert.doesNotMatch(words(failed), /PRIVATE_TOKEN_MARKER|sb_publishable_|demo/i);
    assert.equal(h.creations.length, 0);
    nodes(failed).find((node) => node.type === "button").props.onClick();
    assert.equal(h.hasChildren(h.view()), false);
    await tick();
    assert.equal(h.hasChildren(h.view()), true);
    assert.equal(attempts, 2);
    assert.equal(h.supabase.supabaseUrl, publicConfig.supabase_url);
  }
});

test("network exceptions, invalid fields and nonpublic keys fail closed without echoed values", async () => {
  const legacyKey = (role) => `eyJhbGciOiJIUzI1NiJ9.${Buffer.from(JSON.stringify({ role })).toString("base64url")}.test_signature`;
  const invalid = [
    null, [], {},
    { ...publicConfig, storage_configured: "true" },
    { ...publicConfig, compute_configured: "false" },
    { ...publicConfig, compute_message: null },
    { ...publicConfig, compute_message: "x".repeat(1_025) },
    { ...publicConfig, supabase_url: "not a URL" },
    { ...publicConfig, supabase_url: "http://example.supabase.co" },
    { ...publicConfig, supabase_url: "https://user:PRIVATE_TOKEN_MARKER@example.com" },
    { ...publicConfig, supabase_url: "https://example.com?token=PRIVATE_TOKEN_MARKER" },
    { ...publicConfig, supabase_url: "https://example.com#PRIVATE_TOKEN_MARKER" },
    { ...publicConfig, supabase_url: `https://${"x".repeat(2_049)}.com` },
    { ...publicConfig, supabase_publishable_key: "" },
    { ...publicConfig, supabase_publishable_key: "sb_secret_PRIVATE_TOKEN_MARKER" },
    { ...publicConfig, supabase_publishable_key: "sb_publishable_" + "x".repeat(4_097) },
    { ...publicConfig, supabase_publishable_key: legacyKey("service_role") },
    { ...publicConfig, supabase_publishable_key: "invalid.payload.signature" },
  ];
  const fetchers = [
    async () => { throw new Error("PRIVATE_TOKEN_MARKER"); },
    async () => new Response("PRIVATE_TOKEN_MARKER", { headers: { "content-type": "application/json" } }),
    async () => new Response(JSON.stringify(publicConfig), { headers: { "content-type": "text/html" } }),
    ...invalid.map((config) => async () => response(config)),
  ];
  for (const fetcher of fetchers) {
    const h = harness(fetcher);
    h.view();
    await tick();
    const tree = h.view();
    assert.equal(h.hasChildren(tree), false);
    assert.match(words(tree), /Please retry/);
    assert.doesNotMatch(words(tree), /PRIVATE_TOKEN_MARKER|sb_publishable_|sb_secret_|demo/i);
    assert.equal(h.supabase.supabaseConfigured, false);
  }
  const h = harness(async () => response({ ...publicConfig, supabase_publishable_key: legacyKey("anon") }));
  h.view();
  await tick();
  assert.equal(h.hasChildren(h.view()), true);
});

test("oversized bodies are bounded with or without content-length and streams are cancelled", async () => {
  for (const declared of [true, false]) {
    let cancelled = false;
    const h = harness(async () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(new TextEncoder().encode("x".repeat(16_385))); },
      cancel() { cancelled = true; },
    }), { headers: { "content-type": "application/json", ...(declared ? { "content-length": "16385" } : {}) } }));
    h.view();
    await tick();
    assert.equal(h.hasChildren(h.view()), false);
    assert.match(words(h.view()), /Please retry/);
    assert.equal(cancelled, true);
  }
});

test("10-second deadline covers stalled fetch and stalled body; late success cannot open gate", async () => {
  for (const stallBody of [false, true]) {
    let complete;
    const h = harness(() => stallBody
      ? Promise.resolve(new Response(new ReadableStream({ start(controller) { complete = controller; } }), { headers: { "content-type": "application/json" } }))
      : new Promise((resolve) => { complete = resolve; }));
    h.view();
    await tick();
    [...h.timers.values()][0].callback();
    assert.equal(h.requests[0][1].signal.aborted, true);
    assert.match(words(h.view()), /Please retry/);
    if (stallBody) {
      complete.enqueue(new TextEncoder().encode(JSON.stringify(publicConfig)));
      complete.close();
    } else complete(response());
    await tick();
    assert.equal(h.hasChildren(h.view()), false);
    assert.equal(h.supabase.supabaseConfigured, false);
  }
});

test("unmount/StrictMode cleanup discards old config and permits the replacement effect", async () => {
  const pending = [];
  const h = harness(() => new Promise((resolve) => pending.push(resolve)));
  h.view();
  h.cleanup(true);
  assert.equal(h.requests[0][1].signal.aborted, true);
  h.view();
  pending[0](response({ ...publicConfig, supabase_url: "https://old.example.com" }));
  await tick();
  assert.equal(h.supabase.supabaseConfigured, false);
  assert.equal(h.hasChildren(h.view()), false);
  pending[1](response());
  await tick();
  assert.equal(h.hasChildren(h.view()), true);
  assert.equal(h.supabase.supabaseUrl, publicConfig.supabase_url);
});

test("local default and nonexact flags keep env config without fetching or delaying children", () => {
  for (const flag of [undefined, "false", "TRUE", "1"]) {
    const h = harness(async () => { throw new Error("Unexpected config fetch"); }, flag, {
      NEXT_PUBLIC_SUPABASE_URL: " https://local.example.com ",
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: " local_fixture ",
    });
    // Passing undefined to the harness uses its default; override explicitly for this case.
    if (flag === undefined) h.globals.process.env.NEXT_PUBLIC_HOSTED_RUNTIME_CONFIG = undefined;
    const Component = load("../components/hosted-bootstrap.tsx", {
      react: h.api, "@/lib/supabase": h.supabase,
    }, h.globals).HostedBootstrap;
    assert.equal(h.hasChildren(h.render(Component, { children: h.children })), true);
    assert.equal(h.requests.length, 0);
    assert.equal(h.supabase.supabaseConfigured, true);
    assert.equal(h.supabase.supabaseUrl, "https://local.example.com");
    assert.equal(h.supabase.publishableKey, "local_fixture");
  }
});

test("configureSupabase is idempotent, uses current settings and does not sign out", async () => {
  const h = harness(async () => new Response(JSON.stringify({ external: { google: true, azure: true } })));
  h.supabase.configureSupabase(publicConfig.supabase_url, publicConfig.supabase_publishable_key);
  const original = h.supabase.getSupabaseClient();
  h.supabase.configureSupabase(` ${publicConfig.supabase_url} `, ` ${publicConfig.supabase_publishable_key} `);
  assert.equal(h.supabase.getSupabaseClient(), original);
  assert.equal(h.creations.length, 1);
  assert.equal(h.stops(), 0);
  const availability = await h.supabase.getOAuthAvailability();
  assert.equal(availability.google, true);
  assert.equal(availability.azure, true);
  assert.equal(h.requests[0][0], `${publicConfig.supabase_url}/auth/v1/settings`);
  assert.equal(h.requests[0][1].headers.apikey, publicConfig.supabase_publishable_key);
  h.supabase.configureSupabase("https://updated.example.com", "sb_publishable_updated_fixture");
  h.supabase.getSupabaseClient();
  assert.equal(h.creations[1][0], "https://updated.example.com");
  assert.equal(h.stops(), 1);
  h.supabase.configureSupabase("", "");
  assert.equal(h.supabase.supabaseConfigured, false);
  assert.equal(h.supabase.getSupabaseClient(), null);
  assert.equal(h.signOuts(), 0);
});

test("runtime configuration preserves Google/Microsoft identity and existing sign-in redirects", async () => {
  const h = harness();
  h.globals.window.location = { origin: "https://private.example.com" };
  const identity = { user: { id: "existing-user", identities: [{ provider: "google" }, { provider: "azure" }] } };
  h.client.auth.getSession = async () => ({ data: { session: identity } });
  const oauth = [];
  const links = [];
  h.client.auth.signInWithOAuth = async (input) => { oauth.push(input); return { error: null }; };
  h.client.auth.signInWithOtp = async (input) => { links.push(input); return { error: null }; };
  h.view();
  await tick();
  const client = h.supabase.getSupabaseClient();
  assert.equal((await client.auth.getSession()).data.session, identity);
  h.supabase.configureSupabase(publicConfig.supabase_url, publicConfig.supabase_publishable_key);
  assert.equal(h.supabase.getSupabaseClient(), client);
  await h.supabase.startOAuth("google");
  await h.supabase.startOAuth("azure");
  await h.supabase.sendMagicLink("fixture@example.com");
  assert.equal(oauth[0].provider, "google");
  assert.equal(oauth[1].provider, "azure");
  assert.equal(oauth[1].options.scopes, "email");
  for (const input of oauth) assert.equal(input.options.redirectTo, "https://private.example.com/auth/callback");
  assert.equal(links[0].options.emailRedirectTo, "https://private.example.com/auth/callback");
  assert.equal(links[0].options.shouldCreateUser, true);
  assert.equal(h.signOuts(), 0);
});

test("root layout wraps callback in the same bootstrap and preserves site metadata", async () => {
  const h = harness();
  const callbackHooks = hooks();
  const exchanged = [];
  const redirects = [];
  h.client.auth.exchangeCodeForSession = async (code) => { exchanged.push(code); return { error: null }; };
  const Callback = load("../app/auth/callback/page.tsx", {
    react: callbackHooks.api, "@/lib/supabase": h.supabase,
    "@/components/legal-links": { LegalLinks: () => null },
  }, { window: { location: { href: "https://private.example.com/auth/callback?code=fixture_code", replace: (url) => redirects.push(url) } } }).default;
  const layout = load("../app/layout.tsx", {
    "@/components/hosted-bootstrap": { HostedBootstrap: h.component }, "./globals.css": {},
  }, { process: { env: { NEXT_PUBLIC_SITE_URL: "https://private.example.com" } } });
  const callback = jsx(Callback, {});
  const gate = nodes(layout.default({ children: callback })).find((node) => node.type === h.component);
  assert.ok(gate);
  const first = h.render(gate.type, gate.props);
  assert.equal(nodes(first).includes(callback), false);
  assert.equal(exchanged.length, 0);
  h.view();
  await tick();
  const ready = h.render(gate.type, gate.props);
  assert.equal(nodes(ready).includes(callback), true);
  callbackHooks.render(Callback);
  await tick();
  assert.equal(exchanged[0], "fixture_code");
  assert.equal(redirects[0], "/");
  assert.equal(layout.metadata.metadataBase.origin, "https://private.example.com");
  assert.equal(layout.metadata.alternates.canonical, "/");
});
