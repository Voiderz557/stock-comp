import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { PGlite } from "@electric-sql/pglite";

const db = new PGlite();
const root = new URL("../../", import.meta.url);
const userId = "11111111-1111-1111-1111-111111111111";
const accountId = "22222222-2222-2222-2222-222222222222";
const planId = "33333333-3333-3333-3333-333333333333";
const recId = "44444444-4444-4444-4444-444444444444";
const secondPlanId = "55555555-5555-5555-5555-555555555555";
const secondRecId = "66666666-6666-6666-6666-666666666666";

test("migrations and estimated paper BUY are atomic and idempotent", async () => {
await db.exec(`create role anon; create role authenticated; create schema auth;
  create table auth.users(id uuid primary key, raw_user_meta_data jsonb not null default '{}'::jsonb);
  create function auth.uid() returns uuid language sql stable as $$
    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
  set request.jwt.claim.sub = '${userId}';
  insert into auth.users(id) values ('${userId}');`);
for (const name of [
  "20260926210000_public_app_foundation.sql",
  "20260926233000_first_login_account_setup.sql",
  "20260929233000_account_snapshot_updates.sql",
  "20261001210000_cloud_daily_plan.sql",
  "20261002200000_plan_history_and_events.sql",
  "20261007120000_planning_profiles.sql",
  "20261008110000_recommendation_paper_actions.sql",
]) {
  let sql = readFileSync(new URL(`supabase/migrations/${name}`, root), "utf8");
  sql = sql.replace("create extension if not exists pgcrypto;", "");
  try {
    await db.exec(sql);
  } catch (error) {
    process.stderr.write(`FAILED ${name}: ${error.message}\n`);
    throw error;
  }
}
const privileges = await db.query(`select
  has_table_privilege('authenticated', 'public.paper_actions', 'INSERT') as direct_insert,
  has_table_privilege('authenticated', 'public.positions', 'UPDATE') as direct_position_update,
  has_function_privilege('authenticated', 'public.record_recommendation_paper_action(uuid,numeric,bigint,text)', 'EXECUTE') as action_rpc`);
assert.equal(privileges.rows[0].direct_insert, false);
assert.equal(privileges.rows[0].direct_position_update, false);
assert.equal(privileges.rows[0].action_rpc, true);

await db.exec(`insert into public.competition_accounts
  (id,user_id,name,starting_capital,cash,snapshot_as_of,portfolio_version)
  values ('${accountId}','${userId}','Test',100000,100000,now(),1);
  insert into public.plans
  (id,user_id,account_id,market_as_of,portfolio_version,strategy_name,status)
  values ('${planId}','${userId}','${accountId}',now(),1,'Test','CURRENT');
  insert into public.recommendations
  (id,user_id,plan_id,ticker,recommended_action,score,signal,reason,factor_details,rank,last_price,price_as_of)
  values ('${recId}','${userId}','${planId}','AAPL','BUY',80,'BUY','Test',
    '{"Proposed Quantity":50}'::jsonb,1,100,now());`);
const first = await db.query(`select public.record_recommendation_paper_action(
  '${recId}',50,1,'test-key-123') as id`);
const retry = await db.query(`select public.record_recommendation_paper_action(
  '${recId}',50,1,'test-key-123') as id`);
assert.equal(first.rows[0].id, retry.rows[0].id);
const state = await db.query(`select cash,portfolio_version from public.competition_accounts where id='${accountId}'`);
assert.equal(Number(state.rows[0].cash), 95000);
assert.equal(Number(state.rows[0].portfolio_version), 2);
const actions = await db.query(`select action_type,price_source,quantity from public.paper_actions`);
assert.equal(actions.rows.length, 1);
assert.equal(actions.rows[0].price_source, "SAVED_DAILY_CLOSE_ESTIMATE");
const history = await db.query("select public.get_recent_paper_actions(50) as actions");
assert.equal(history.rows[0].actions.length, 1);
assert.equal(history.rows[0].actions[0].idempotency_key, undefined);

await assert.rejects(
  db.query(`select public.record_recommendation_paper_action('${recId}',40,1,'test-key-123')`),
  /Idempotency key was already used/,
);
await assert.rejects(
  db.query(`select public.record_recommendation_paper_action('${recId}',50,2,'new-key-123')`),
  /Recommendation is unavailable or already recorded/,
);

await db.exec(`insert into public.plans
  (id,user_id,account_id,market_as_of,portfolio_version,strategy_name,status)
  values ('${secondPlanId}','${userId}','${accountId}',now(),2,'Test','CURRENT');
  insert into public.recommendations
  (id,user_id,plan_id,ticker,recommended_action,score,signal,reason,factor_details,rank,last_price,price_as_of)
  values ('${secondRecId}','${userId}','${secondPlanId}','AAPL','SELL',20,'AVOID','Exit review',
    '{}'::jsonb,1,100,now());`);
await db.query(`select public.record_recommendation_paper_action('${secondRecId}',20,2,'sell-key-123')`);
const afterSell = await db.query(`select cash,portfolio_version from public.competition_accounts where id='${accountId}'`);
assert.equal(Number(afterSell.rows[0].cash), 97000);
assert.equal(Number(afterSell.rows[0].portfolio_version), 3);
const sold = await db.query("select action_type,realized_pnl,basis_released from public.paper_actions where idempotency_key='sell-key-123'");
assert.equal(sold.rows[0].action_type, "DECREASE");
assert.equal(Number(sold.rows[0].realized_pnl), 0);
assert.equal(Number(sold.rows[0].basis_released), 2000);
const remaining = await db.query("select quantity,exact_cost_basis from public.positions where status='OPEN'");
assert.equal(Number(remaining.rows[0].quantity), 30);
assert.equal(Number(remaining.rows[0].exact_cost_basis), 3000);

const thirdPlanId = "77777777-7777-7777-7777-777777777777";
const thirdRecId = "88888888-8888-8888-8888-888888888888";
await db.exec(`insert into public.plans
  (id,user_id,account_id,market_as_of,portfolio_version,strategy_name,status)
  values ('${thirdPlanId}','${userId}','${accountId}',now(),3,'Test','CURRENT');
  insert into public.recommendations
  (id,user_id,plan_id,ticker,recommended_action,score,signal,reason,factor_details,rank,last_price,price_as_of)
  values ('${thirdRecId}','${userId}','${thirdPlanId}','AAPL','BUY',80,'BUY','Test',
    '{"Proposed Quantity":1000}'::jsonb,1,100,now());`);
await assert.rejects(
  db.query(`select public.record_recommendation_paper_action('${thirdRecId}',1000,3,'cash-key-123')`),
  /Insufficient cash/,
);
await assert.rejects(
  db.query(`select public.record_recommendation_paper_action('${thirdRecId}',200,3,'cap-key-123')`),
  /per-stock purchase cap/,
);
const unchanged = await db.query(`select cash,portfolio_version from public.competition_accounts where id='${accountId}'`);
assert.equal(Number(unchanged.rows[0].cash), 97000);
assert.equal(Number(unchanged.rows[0].portfolio_version), 3);
assert.equal((await db.query("select count(*)::int as n from public.paper_actions")).rows[0].n, 2);

await db.exec(`update public.recommendations set price_as_of = now() - interval '1 day'
  where id='${thirdRecId}'`);
await assert.rejects(
  db.query(`select public.record_recommendation_paper_action('${thirdRecId}',1,3,'old-quote-key')`),
  /current dated daily close/,
);
await db.exec(`update public.recommendations set price_as_of = now() where id='${thirdRecId}';
  insert into public.positions(user_id,account_id,ticker,direction,quantity,average_price,exact_cost_basis,status,source)
  values ('${userId}','${accountId}','MSFT','LONG',2,100,200,'OPEN','ACCOUNT_SNAPSHOT')`);
await assert.rejects(
  db.query(`select public.record_recommendation_paper_action('${thirdRecId}',1,3,'missing-quote-key')`),
  /A holding lacks a current plan quote/,
);
await db.exec(`delete from public.positions where account_id='${accountId}' and ticker='MSFT';
  insert into public.positions(user_id,account_id,ticker,direction,quantity,average_price,exact_cost_basis,status,source)
  values ('${userId}','${accountId}','MSFT','SHORT',2,100,200,'OPEN','ACCOUNT_SNAPSHOT')`);
await assert.rejects(
  db.query(`select public.record_recommendation_paper_action('${thirdRecId}',1,3,'short-key-123')`),
  /cash-proceeds reconciliation/,
);
await db.exec(`delete from public.positions where account_id='${accountId}' and ticker='MSFT'`);
assert.equal((await db.query("select count(*)::int as n from public.paper_actions")).rows[0].n, 2);

const otherUser = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const otherAccount = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const otherPlan = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const otherRec = "dddddddd-dddd-dddd-dddd-dddddddddddd";
await db.exec(`insert into auth.users(id) values ('${otherUser}');
  insert into public.competition_accounts(id,user_id,name,starting_capital,cash,snapshot_as_of,portfolio_version)
  values ('${otherAccount}','${otherUser}','Other',100000,100000,now(),1);
  insert into public.plans(id,user_id,account_id,market_as_of,portfolio_version,strategy_name,status)
  values ('${otherPlan}','${otherUser}','${otherAccount}',now(),1,'Test','CURRENT');
  insert into public.recommendations
  (id,user_id,plan_id,ticker,recommended_action,score,signal,reason,factor_details,rank,last_price,price_as_of)
  values ('${otherRec}','${otherUser}','${otherPlan}','MSFT','BUY',80,'BUY','Other',
    '{"Proposed Quantity":1}'::jsonb,1,100,now());`);
await assert.rejects(
  db.query(`select public.record_recommendation_paper_action('${otherRec}',1,3,'other-key-123')`),
  /Recommendation is unavailable/,
);
await db.close();
});
