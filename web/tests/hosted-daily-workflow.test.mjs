import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { PGlite } from "@electric-sql/pglite";
import { createApi } from "../../deployment/chatgpt-site/worker/api.mjs";

const owner = "11111111-1111-1111-1111-111111111111";
const token = "disposable-test-session";
const env = {
  STOCK_COMP_SUPABASE_URL: "https://storage.example.com",
  STOCK_COMP_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_disposable",
  STOCK_COMP_PYTHON_API_URL: "https://compute.example.com",
};

test("hosted account, trusted plan and idempotent action survive storage restart", async () => {
  const directory = mkdtempSync(path.join(tmpdir(), "stock-comp-workflow-"));
  let db = new PGlite(path.join(directory, "db"));
  try {
    await db.exec(`create role anon; create role authenticated; create role service_role;
      create schema auth;
      create table auth.users(id uuid primary key, raw_user_meta_data jsonb not null default '{}'::jsonb);
      create function auth.uid() returns uuid language sql stable as $$
        select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
      grant usage on schema auth to authenticated;
      insert into auth.users(id) values ('${owner}');`);
    const migrations = new URL("../../supabase/migrations/", import.meta.url);
    for (const name of readdirSync(migrations).filter(name => name.endsWith(".sql")).sort()) {
      await db.exec(readFileSync(new URL(name, migrations), "utf8")
        .replace("create extension if not exists pgcrypto;", ""));
    }
    await db.exec(`set role authenticated; set request.jwt.claim.sub = '${owner}';`);

    // Only transport/auth and compute are simulated. Every storage RPC below
    // runs the real migrations under the browser's restricted database role.
    const fetcher = async (target, options) => {
      const url = new URL(target);
      assert.equal(options.headers.Authorization, `Bearer ${token}`);
      assert.equal(options.redirect, "manual");
      if (url.pathname === "/auth/v1/user") return Response.json({ id: owner });
      const name = url.pathname.split("/").at(-1);
      const allowed = new Set([
        "create_or_get_competition_account", "update_competition_account_snapshot",
        "get_current_account_snapshot", "get_account_plan_history", "get_recent_paper_actions",
        "record_recommendation_paper_action", "set_recommendation_status",
      ]);
      assert.ok(allowed.has(name), `Unexpected test upstream ${url.pathname}`);
      const body = JSON.parse(options.body);
      const entries = Object.entries(body);
      assert.ok(entries.every(([key]) => /^p_[a-z_]+$/.test(key)));
      const params = entries.map(([, value]) => value !== null && typeof value === "object" ? JSON.stringify(value) : value);
      const args = entries.map(([key], index) => `${key} => $${index + 1}`).join(",");
      try {
        return Response.json((await db.query(`select public.${name}(${args}) as result`, params)).rows[0].result);
      } catch (error) {
        return Response.json({ code: error.code, message: error.message }, { status: 400 });
      }
    };
    let api = createApi({ fetcher, strategyNames: ["Momentum V2"] });
    const call = (route, body, method = body ? "POST" : "GET") => api(new Request(`https://site.example.com/api/v1/${route}`, {
      method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      ...(body ? { body: JSON.stringify(body) } : {}),
    }), env);
    const restart = async () => {
      await db.close();
      db = new PGlite(path.join(directory, "db"));
      await db.exec(`set role authenticated; set request.jwt.claim.sub = '${owner}';`);
      api = createApi({ fetcher, strategyNames: ["Momentum V2"] });
    };
    const created = await call("accounts", {
      name: "Disposable workflow", starting_capital: 100000, cash: 100000,
      snapshot_as_of: new Date().toISOString(),
    });
    assert.equal(created.account.portfolio_version, 1);
    await restart();
    assert.equal((await call("snapshot")).account.name, "Disposable workflow");

    const snapshot = await call("accounts/current", {
      name: "Disposable workflow", starting_capital: 100000, cash: 99000,
      snapshot_as_of: new Date().toISOString(), expected_portfolio_version: 1,
      positions: [{ ticker: "AAPL", side: "LONG", quantity: 10, average_price: 100 }],
    }, "PUT");
    assert.equal(snapshot.positions[0].source, "ACCOUNT_SNAPSHOT");
    assert.equal(snapshot.portfolio.value, null);
    assert.equal((await call("paper-actions")).length, 0);

    // Stand in for Python compute, but use its actual server-only writer.
    // The separate Python workflow test exercises the real strategy builder.
    const savePlan = async (version, quantity = 20) => {
      const date = new Date().toISOString();
      const recs = [
        { ticker: "AAPL", action: "WAIT", score: 50, rank: 1, last_price: 100, price_as_of: date, reason: "Already held" },
        { ticker: "MSFT", action: "BUY", score: 80, rank: 2, last_price: 150, price_as_of: date,
          reason: "Synthetic strategy evidence", factor_details: { "Proposed Quantity": quantity, "Proposed Allocation": quantity * 150 } },
      ];
      await db.exec("reset role");
      try {
        await db.exec("set role service_role");
        await db.query(`select public.save_generated_trading_plan($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
          [owner, version, "Momentum V2", date, "One synthetic buy", JSON.stringify({ cash: 99000 }),
            JSON.stringify({ name: "WEAK_UPTREND" }), "[]", JSON.stringify(recs)]);
      } finally { await db.exec("reset role; set role authenticated"); }
      return call("snapshot");
    };
    let planned = await savePlan(2);
    const savedPlanId = planned.plan.id;
    assert.equal(planned.plan.status, "CURRENT");
    await restart();
    planned = await call("snapshot");
    assert.equal(planned.plan.id, savedPlanId);
    const buy = planned.recommendations.find(item => item.ticker === "MSFT");
    assert.equal(buy.reason, "Synthetic strategy evidence");
    assert.equal(buy.factor_details["Proposed Quantity"], 20);
    assert.equal(buy.price, 150);

    const action = { recommendation_id: buy.id, quantity: 20, expected_portfolio_version: 2, idempotency_key: "restart-safe-action" };
    const recorded = await call("paper-actions", action);
    assert.equal(Number(recorded.snapshot.portfolio.cash), 96000);
    assert.equal(recorded.snapshot.plan.status, "STALE");
    assert.deepEqual(recorded.snapshot.recommendations, []);
    await restart();
    const retry = await call("paper-actions", action);
    assert.equal(retry.action_id, recorded.action_id);
    assert.equal(Number(retry.snapshot.portfolio.cash), 96000);
    assert.equal(retry.snapshot.positions.length, 2);
    assert.equal((await call("paper-actions")).length, 1);
    await assert.rejects(call("paper-actions", { ...action, idempotency_key: "new-duplicate-action", expected_portfolio_version: 3 }),
      error => error.status === 409 && /portfolio or plan changed/.test(error.message));

    planned = await savePlan(3, 1000);
    const oversized = planned.recommendations.find(item => item.ticker === "MSFT");
    await assert.rejects(call("paper-actions", { ...action, recommendation_id: oversized.id,
      quantity: 1000, expected_portfolio_version: 3, idempotency_key: "insufficient-cash" }),
    error => error.status === 422 && /Insufficient cash/.test(error.message));
    const unchanged = await call("snapshot");
    assert.equal(Number(unchanged.portfolio.cash), 96000);
    assert.equal((await call("paper-actions")).length, 1);
    assert.equal((await db.query("select count(*)::int as n from public.plans")).rows[0].n, 2);
  } finally {
    await db.close();
    // Only this test's explicitly created disposable directory is removed.
    rmSync(directory, { recursive: true, force: true });
  }
});
