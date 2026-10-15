import assert from "node:assert/strict";
import test from "node:test";
import { runPlanJob } from "../lib/plan-job-client.ts";

const id = "11111111-1111-1111-1111-111111111111";
const running = { id, status: "RUNNING", stage: "Loading price history", detail: "AAPL (1/200)", elapsed_seconds: 1, error: null };
const snapshot = { account: { portfolio_version: 1 }, plan: { status: "CURRENT" }, recommendations: [] };
function harness(responses, overrides = {}) {
  const calls = [], progress = [];
  return { calls, progress, run: () => runPlanJob({
    baseUrl: "/api", token: "fake-session", input: { request_id: "stable-id", strategy: "Test" },
    pause: async () => {}, onProgress: value => progress.push(value),
    fetcher: async (url, options) => {
      calls.push({ url, options });
      const next = responses.shift();
      if (next instanceof Error) throw next;
      assert.ok(next, "Unexpected extra request");
      return Response.json(next.body ?? next, { status: next.http ?? 200 });
    }, ...overrides,
  }) };
}
test("short requests poll the same job and return only a confirmed saved snapshot", async () => {
  const h = harness([running, { ...running, elapsed_seconds: 130 }, { ...running, status: "SUCCEEDED", snapshot }]);
  assert.deepEqual(await h.run(), snapshot);
  assert.deepEqual(h.calls.map(c => c.options.method), ["POST", "GET", "GET"]);
  assert.equal(h.calls[1].url, `/api/v1/plan-jobs/${id}`);
  assert.equal(h.progress[1].elapsed_seconds, 130, "Exceeding the old proxy deadline must not restart the scan");
  assert.ok(h.calls.every(c => c.options.headers.Authorization === "Bearer fake-session"));
});
test("lost start response retries the exact body, not a new request ID", async () => {
  const h = harness([new TypeError("network"), running, { ...running, status: "SUCCEEDED", snapshot }]);
  await h.run();
  assert.equal(h.calls[0].options.body, h.calls[1].options.body);
});
test("transient poll failure retries only status and never starts another scan", async () => {
  const h = harness([running, { http: 502, body: {} }, { ...running, status: "SUCCEEDED", snapshot }]);
  await h.run();
  assert.equal(h.calls.filter(c => c.options.method === "POST").length, 1);
});
for (const status of [401, 404, 409, 429]) test(`HTTP ${status} stops without another start`, async () => {
  const h = harness([{ http: status, body: { detail: "Unavailable" } }]);
  await assert.rejects(h.run());
  assert.equal(h.calls.length, 1);
});
test("failed, malformed and missing-result jobs are never presented as success", async () => {
  await assert.rejects(harness([{ ...running, status: "FAILED", error: "Missing benchmark history" }]).run(), /Missing benchmark/);
  await assert.rejects(harness([{ ...running, status: "SUCCEEDED" }, { ...running, status: "SUCCEEDED" }]).run(), /could not be reloaded/);
  await assert.rejects(harness([{ nonsense: true }, { nonsense: true }]).run(), /invalid progress/);
});
test("a lost-start retry that finds a completed job reloads its saved snapshot", async () => {
  const h = harness([new TypeError("lost response"), { ...running, status: "SUCCEEDED" }, { ...running, status: "SUCCEEDED", snapshot }]);
  assert.deepEqual(await h.run(), snapshot);
  assert.equal(h.calls[2].url, `/api/v1/plan-jobs/${id}`);
});
test("overall waiting deadline never silently submits a second scan", async () => {
  let time = 0;
  // First pause would ordinarily be followed by a poll; provide its response.
  const slow = harness([running, running], { now: () => time, pause: async () => { time += 14 * 60000; } });
  await assert.rejects(slow.run(), /Stopped waiting/);
  assert.equal(slow.calls.filter(c => c.options.method === "POST").length, 1);
});
