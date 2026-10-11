import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { PGlite } from "@electric-sql/pglite";

const migrations = [
  "20260926210000_public_app_foundation.sql",
  "20260926233000_first_login_account_setup.sql",
  "20260929233000_account_snapshot_updates.sql",
  "20261001210000_cloud_daily_plan.sql",
  "20261002200000_plan_history_and_events.sql",
  "20261007120000_planning_profiles.sql",
  "20261008110000_recommendation_paper_actions.sql",
];
const ownerId = "11111111-1111-1111-1111-111111111111";
const otherId = "22222222-2222-2222-2222-222222222222";

function sqlJson(value) {
  return `'${JSON.stringify(value).replaceAll("'", "''")}'::jsonb`;
}

async function rpcValue(db, sql, key) {
  const result = await db.query(sql);
  return result.rows[0][key];
}

test("disposable account survives the complete paper-planning lifecycle under user RLS", async () => {
  const db = new PGlite();
  try {
    await db.exec(`create role anon; create role authenticated; create schema auth;
      create table auth.users(id uuid primary key, raw_user_meta_data jsonb not null default '{}'::jsonb);
      create function auth.uid() returns uuid language sql stable as $$
        select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
      grant usage on schema auth to authenticated;
      insert into auth.users(id) values ('${ownerId}'), ('${otherId}');`);
    for (const name of migrations) {
      const sql = readFileSync(new URL(`../../supabase/migrations/${name}`, import.meta.url), "utf8")
        .replace("create extension if not exists pgcrypto;", "");
      await db.exec(sql);
    }
    await db.exec(`insert into public.competition_accounts
      (user_id,name,starting_capital,cash,snapshot_as_of)
      values ('${otherId}','Other account',100000,100000,now());`);
    await db.exec(`set role authenticated; set request.jwt.claim.sub = '${ownerId}';`);

    const accountId = await rpcValue(db, `select public.create_or_get_competition_account(
      'Disposable',100000,100000,now()) as id`, "id");
    assert.equal(await rpcValue(db, `select public.create_or_get_competition_account(
      'Ignored duplicate',100000,100000,now()) as id`, "id"), accountId);
    assert.equal((await db.query("select count(*)::int as n from public.competition_accounts")).rows[0].n, 1);

    const initial = await rpcValue(db, "select public.get_current_account_snapshot() as snapshot", "snapshot");
    assert.equal(Number(initial.portfolio.cash), 100000);
    assert.equal(initial.plan.status, "NONE");

    const positions = [{ ticker: "AAPL", side: "LONG", quantity: 10, average_price: 100 }];
    await db.query(`select public.update_competition_account_snapshot(
      'Disposable',100000,99000,now(),${sqlJson(positions)},1)`);
    const imported = await rpcValue(db, "select public.get_current_account_snapshot() as snapshot", "snapshot");
    assert.equal(imported.account.portfolio_version, 2);
    assert.equal(imported.positions.length, 1);
    assert.equal(imported.positions[0].source, "ACCOUNT_SNAPSHOT");
    assert.equal(imported.portfolio.value, null, "An imported holding is not a fabricated valuation");
    assert.equal((await db.query("select count(*)::int as n from public.paper_actions")).rows[0].n, 0);
    await assert.rejects(
      db.query(`select public.update_competition_account_snapshot(
        'Disposable',100000,99000,now(),${sqlJson(positions)},1)`),
      /Portfolio changed since this form was loaded/,
    );
    assert.equal((await db.query("select count(*)::int as n from public.positions where status='OPEN'")).rows[0].n, 1);
    assert.equal((await db.query("select count(*)::int as n from public.portfolio_snapshots")).rows[0].n, 3);

    const marketTime = new Date().toISOString();
    const recommendations = [
      { ticker: "AAPL", action: "WAIT", score: 50, rank: 1, last_price: 100, price_as_of: marketTime,
        reason: "Already held", factor_details: {} },
      { ticker: "MSFT", action: "BUY", score: 80, rank: 2, last_price: 150, price_as_of: marketTime,
        reason: "Synthetic candidate", factor_details: { "Proposed Quantity": 20 } },
    ];
    const savePlan = (version, recs, label) => `select public.save_current_trading_plan(
      ${version},'Momentum V2','${marketTime}', '${label}',
      ${sqlJson({ cash: version === 2 ? 99000 : 96000 })},
      ${sqlJson({ name: "SIDEWAYS" })},'[]'::jsonb,${sqlJson(recs)}) as id`;
    const firstPlanId = await rpcValue(db, savePlan(2, recommendations, "First plan"), "id");
    const current = await rpcValue(db, "select public.get_current_account_snapshot() as snapshot", "snapshot");
    assert.equal(current.plan.status, "CURRENT");
    assert.equal(current.recommendations.length, 2);
    const buy = current.recommendations.find((item) => item.ticker === "MSFT");
    assert.equal(buy.reason, "Synthetic candidate");

    await db.query(`select public.set_recommendation_status('${buy.id}','DISMISSED')`);
    assert.equal((await rpcValue(db, "select public.get_current_account_snapshot() as snapshot", "snapshot"))
      .recommendations.find((item) => item.id === buy.id).status, "DISMISSED");
    await db.query(`select public.set_recommendation_status('${buy.id}','REOPENED')`);
    const actionId = await rpcValue(db, `select public.record_recommendation_paper_action(
      '${buy.id}',20,2,'disposable-action-1') as id`, "id");
    assert.equal(await rpcValue(db, `select public.record_recommendation_paper_action(
      '${buy.id}',20,2,'disposable-action-1') as id`, "id"), actionId);

    const afterAction = await rpcValue(db, "select public.get_current_account_snapshot() as snapshot", "snapshot");
    assert.equal(Number(afterAction.portfolio.cash), 96000);
    assert.equal(Number(afterAction.portfolio.value), 100000);
    assert.equal(afterAction.account.portfolio_version, 3);
    assert.equal(afterAction.plan.status, "STALE");
    assert.deepEqual(afterAction.recommendations, []);
    assert.equal(afterAction.positions.length, 2);
    assert.equal((await rpcValue(db, "select public.get_recent_paper_actions(50) as actions", "actions")).length, 1);
    await assert.rejects(db.query(`select public.record_recommendation_paper_action(
      '${buy.id}',20,3,'disposable-action-2')`), /Recommendation is unavailable or already recorded/);

    const nextRecommendations = recommendations.map((item) => ({ ...item, action: "WAIT", score: 50 }));
    const nextPlanId = await rpcValue(db, savePlan(3, nextRecommendations, "Rebuilt plan"), "id");
    assert.notEqual(nextPlanId, firstPlanId);
    const reloaded = await rpcValue(db, "select public.get_current_account_snapshot() as snapshot", "snapshot");
    assert.equal(reloaded.plan.id, nextPlanId);
    assert.equal(reloaded.plan.status, "CURRENT");
    assert.equal(reloaded.recommendations.length, 2);
    const history = await rpcValue(db, "select public.get_account_plan_history(20) as history", "history");
    assert.equal(history.length, 2);
    assert.deepEqual(new Set(history.map((item) => item.status)), new Set(["CURRENT", "STALE"]));

    const refreshedPositions = [
      ...positions,
      { ticker: "MSFT", side: "LONG", quantity: 20, average_price: 150 },
    ];
    await db.query(`select public.update_competition_account_snapshot(
      'Disposable',100000,96000,now(),${sqlJson(refreshedPositions)},3)`);
    const afterReimport = await rpcValue(db, "select public.get_current_account_snapshot() as snapshot", "snapshot");
    assert.equal(afterReimport.plan.status, "STALE", "A plan for the old portfolio must not stay current");
    assert.deepEqual(afterReimport.recommendations, []);
    assert.equal(afterReimport.positions.length, 2, "Reimport replaces rather than duplicates open holdings");
    assert.equal(afterReimport.portfolio.value, null, "Reimport must not reuse a stale market valuation");
    assert.equal((await db.query("select count(*)::int as n from public.paper_actions")).rows[0].n, 1);
    assert.equal((await rpcValue(db, "select public.get_account_plan_history(20) as history", "history")).length, 2);

    // The export endpoint reads these tables sequentially under this same RLS role.
    for (const table of ["competition_accounts", "positions", "plans", "recommendations",
      "recommendation_events", "paper_actions", "portfolio_snapshots", "planning_profiles"]) {
      const rows = await db.query(`select user_id from public.${table}`);
      assert.ok(rows.rows.every((row) => row.user_id === ownerId), `${table} crossed the user boundary`);
    }
  } finally {
    await db.close();
  }
});
