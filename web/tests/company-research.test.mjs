import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";

// Same transpilation + hook simulation approach as hosted-bootstrap.test.mjs.
// Key reconciliation is modeled explicitly; these are component unit tests, not browser tests.
const require = createRequire(import.meta.url);
const { renderToStaticMarkup } = require("react-dom/server");
const compiled = ts.transpileModule(readFileSync(new URL("../components/company-research.tsx", import.meta.url), "utf8"), {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const tick = () => new Promise(resolve => setImmediate(resolve));
const item = (overrides = {}) => ({ id: "filing:1", kind: "filing", title: "Quarterly filing",
  source: "SEC", url: "https://www.sec.gov/Archives/example", published_at: "2026-10-22",
  event_date: null, summary: "A public filing was published.", ...overrides });
const result = (overrides = {}) => ({ ticker: "AAPL", as_of: "2026-10-23T12:30:00Z",
  items: [item()], providers: [{ name: "sec", status: "ok" }], warnings: [], ...overrides });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  return tree?.props ? [tree, ...nodes(tree.props.children)] : [];
}
function words(tree) {
  if (typeof tree === "string" || typeof tree === "number") return String(tree);
  if (Array.isArray(tree)) return tree.map(words).join("");
  return tree?.props ? words(tree.props.children) : "";
}

function harness(loadResearch, initial = {}) {
  let slots = [], cursor = 0, effects = [], key, mounted = true, lateWrites = 0;
  let props = { ticker: "AAPL", loadResearch, ...initial };
  const api = {
    useRef(initialValue) {
      const index = cursor++;
      return slots[index] ??= { current: initialValue };
    },
    useState(initialValue) {
      const index = cursor++, ownedSlots = slots;
      if (!(index in slots)) slots[index] = initialValue;
      return [slots[index], value => {
        if (!mounted || slots !== ownedSlots) lateWrites++;
        ownedSlots[index] = typeof value === "function" ? value(ownedSlots[index]) : value;
      }];
    },
    useEffect(callback, deps) {
      const index = cursor++, previous = slots[index];
      if (!previous || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
        effects.push(() => { previous?.cleanup?.(); slots[index] = { deps, cleanup: callback() }; });
      }
    },
  };
  const module = { exports: {} };
  runInNewContext(compiled, { module, exports: module.exports, URL,
    require: name => name === "react" ? api : require(name) });
  const cleanup = () => { for (const slot of slots) slot?.cleanup?.(); };
  function view(next = {}) {
    props = { ...props, ...next };
    const element = module.exports.CompanyResearch(props);
    if (element.key !== key) { cleanup(); slots = []; key = element.key; }
    cursor = 0; effects = []; mounted = true;
    const tree = element.type(element.props);
    effects.forEach(effect => effect());
    return tree;
  }
  return { view, click: () => nodes(view()).find(node => node.type === "button").props.onClick(),
    unmount: () => { cleanup(); mounted = false; }, lateWrites: () => lateWrites,
    restartEffects: () => { cleanup(); slots.forEach((slot, index) => { if (slot?.deps) delete slots[index]; }); } };
}

test("explicit button only: no load on mount/rerender, synchronous double-click guard, retry allowed", async () => {
  const pending = deferred(), calls = [];
  const h = harness(ticker => { calls.push(ticker); return pending.promise; });
  assert.match(words(h.view()), /No check yet/);
  h.view(); h.view();
  assert.equal(calls.length, 0);
  const button = nodes(h.view()).find(node => node.type === "button");
  assert.equal(button.props.className, "secondary-button");
  assert.equal(button.props.children, "Check outside information");
  const first = button.props.onClick();
  button.props.onClick();
  assert.deepEqual(calls, ["AAPL"]);
  assert.equal(nodes(h.view()).find(node => node.type === "button").props.disabled, true);
  pending.resolve(result()); await first;
  const tree = h.view();
  assert.equal(tree.props.className, "panel standalone-panel");
  assert.match(words(tree), /read-only check for the selected ticker/);
  assert.match(words(tree), /Checked: 2026-10-23 12:30 UTC/);
  assert.match(words(tree), /Published: 2026-10-22/);
  assert.equal(nodes(tree).filter(node => node.type === "details").length, 1);
  assert.equal(nodes(tree).find(node => node.type === "details").props.open, undefined);
  h.view({ loadResearch: async () => result() });
  assert.equal(calls.length, 1);
  await h.click();
  assert.match(words(h.view()), /Quarterly filing/);
});

test("ticker changes clear results immediately and discard late success/error, even switching back", async () => {
  for (const rejectOld of [false, true]) {
    const old = deferred(), recent = deferred(); let count = 0;
    const h = harness(() => ++count === 1 ? old.promise : recent.promise);
    h.click();
    assert.doesNotMatch(words(h.view({ ticker: "MSFT" })), /Checking sources/);
    h.view({ ticker: "AAPL" }); h.click();
    if (rejectOld) old.reject(new Error("PRIVATE_TOKEN")); else old.resolve(result({ items: [item({ title: "OLD RESULT" })] }));
    await tick();
    assert.match(words(h.view()), /Checking sources/);
    recent.resolve(result({ items: [item({ title: "NEW RESULT" })] })); await tick();
    assert.match(words(h.view()), /NEW RESULT/);
    assert.doesNotMatch(words(h.view()), /OLD RESULT|PRIVATE_TOKEN|could not be checked/);
    assert.equal(h.lateWrites(), 0);
    assert.doesNotMatch(words(h.view({ ticker: "MSFT" })), /NEW RESULT|Checked:/);
  }
});

test("unmount and effect cleanup discard late completions", async () => {
  for (const restart of [false, true]) {
    const old = deferred(), h = harness(() => old.promise);
    h.click();
    if (restart) { h.restartEffects(); h.view(); } else h.unmount();
    old.resolve(result()); await tick();
    assert.equal(h.lateWrites(), 0);
    if (restart) assert.doesNotMatch(words(h.view()), /Quarterly filing/);
  }
});

test("disabled and empty ticker cannot load; disabling invalidates pending data", async () => {
  let calls = 0;
  for (const props of [{ enabled: false }, { ticker: " " }]) {
    const h = harness(async () => { calls++; return result(); }, props);
    assert.equal(nodes(h.view()).find(node => node.type === "button").props.disabled, true);
    await h.click();
  }
  assert.equal(calls, 0);
  const pending = deferred(), h = harness(() => pending.promise);
  h.click(); h.view({ enabled: false }); pending.resolve(result()); await tick();
  assert.match(words(h.view()), /disabled/);
  assert.doesNotMatch(words(h.view({ enabled: true })), /Quarterly filing/);
  assert.equal(h.lateWrites(), 0);
});

test("source links require HTTPS without userinfo; invalid dates and unsourced items are omitted", async () => {
  const invalid = ["http://example.com", "javascript:alert(1)", "data:text/html,test", "//example.com", "/local",
    "https://user:secret@example.com", "https://@example.com", "https://example.com\\@evil.com", "https://example.com/\nunsafe"];
  const h = harness(async () => result({ items: [
    ...invalid.map(url => item({ url })), item({ published_at: "2026-02-30" }),
    item({ source: "" }), item({ event_date: "invalid" }), item(),
  ] }));
  await h.click(); const tree = h.view(), links = nodes(tree).filter(node => node.type === "a");
  assert.equal(links.length, 1);
  assert.equal(links[0].props.href, item().url);
  assert.equal(links[0].props.rel, "noopener noreferrer");
  assert.match(words(tree), /were omitted/);
});

test("bounded list and text; external markup is escaped and no automatic directional labels", async () => {
  const h = harness(async () => result({
    items: Array.from({ length: 50 }, (_, i) => item({ id: String(i), title: "<script>test</script>" + "x".repeat(1000), summary: "y".repeat(2000) })),
    providers: Array.from({ length: 30 }, () => ({ name: "sec", status: "ok" })),
  }));
  await h.click(); const tree = h.view(), html = renderToStaticMarkup(tree);
  assert.equal(nodes(tree).filter(node => node.type === "a").length, 20);
  assert.ok(html.length < 25000);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|bullish|bearish/i);
  assert.match(words(tree), /first 8 providers/);
  assert.match(words(tree), /separate from the technical recommendation/);
});

test("empty results, source configuration and unavailable states are honest; diagnostics stay private", async () => {
  const h = harness(async () => result({ items: [], warnings: ["PRIVATE_TOKEN"], providers: [
    { name: "sec", status: "unavailable", reason: "missing_contact_user_agent" },
    { name: "massive", status: "unavailable", reason: "runtime_adapter_not_configured" },
    { name: "other", status: "unavailable", reason: "PRIVATE_TOKEN" },
  ] }));
  await h.click(); const tree = h.view();
  assert.match(words(tree), /Setup required/);
  assert.match(words(tree), /Unavailable; coverage may be incomplete/);
  assert.match(words(tree), /No sourced results/);
  assert.match(words(tree), /Sources reported warnings/);
  assert.doesNotMatch(words(tree), /PRIVATE_TOKEN/);
  assert.equal(nodes(tree).filter(node => node.type === "details").length, 0);
});

test("mismatched and malformed results or thrown errors show a safe error and permit retry", async () => {
  for (const value of [null, {}, result({ ticker: "MSFT" }), result({ as_of: "bad" }), result({ items: null }), new Error("PRIVATE_TOKEN")]) {
    let attempts = 0;
    const h = harness(() => {
      if (++attempts > 1) return Promise.resolve(result());
      if (value instanceof Error) throw value;
      return Promise.resolve(value);
    });
    await h.click();
    assert.match(words(h.view()), /could not be checked/);
    assert.doesNotMatch(words(h.view()), /PRIVATE_TOKEN|Quarterly filing/);
    await h.click(); assert.match(words(h.view()), /Quarterly filing/);
  }
});
