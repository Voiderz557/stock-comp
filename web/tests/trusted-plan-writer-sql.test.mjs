import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { PGlite } from "@electric-sql/pglite";

const root = new URL("../../", import.meta.url);
const userId = "11111111-1111-1111-1111-111111111111";
const accountId = "22222222-2222-2222-2222-222222222222";
const signature = "uuid,bigint,text,timestamptz,text,jsonb,jsonb,jsonb,jsonb";

test("only the server role can save generated plans; old current plans become stale", async () => {
  const db = new PGlite();
  await db.exec(`create role anon; create role authenticated; create role service_role;
    create schema auth;
    create table auth.users(id uuid primary key, raw_user_meta_data jsonb not null default '{}'::jsonb);
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
    set request.jwt.claim.sub = '${userId}';
    set request.jwt.claim.role = 'service_role';
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
    const sql = readFileSync(new URL(`supabase/migrations/${name}`, root), "utf8")
      .replace("create extension if not exists pgcrypto;", "");
    await db.exec(sql);
  }
  await db.exec(`insert into public.competition_accounts
    (id,user_id,name,starting_capital,cash,snapshot_as_of,portfolio_version)
    values ('${accountId}','${userId}','Test',100000,100000,now(),1);
    insert into public.plans(user_id,account_id,market_as_of,portfolio_version,strategy_name,status)
    values ('${userId}','${accountId}',now(),1,'Legacy','CURRENT');`);

  await db.exec(readFileSync(new URL("supabase/migrations/20261008120000_trusted_plan_writer.sql", root), "utf8"));
  const privileges = await db.query(`select
    has_function_privilege('authenticated', 'public.save_current_trading_plan(bigint,text,timestamptz,text,jsonb,jsonb,jsonb,jsonb)', 'EXECUTE') as legacy_public,
    has_function_privilege('authenticated', 'public.save_generated_trading_plan(${signature})', 'EXECUTE') as browser_trusted,
    has_function_privilege('service_role', 'public.save_generated_trading_plan(${signature})', 'EXECUTE') as server_trusted,
    has_table_privilege('authenticated', 'public.plans', 'INSERT') as browser_direct_insert`);
  assert.deepEqual(privileges.rows[0], {
    legacy_public: false, browser_trusted: false, server_trusted: true, browser_direct_insert: false,
  });
  const legacy = await db.query("select status,stale_reason from public.plans where strategy_name='Legacy'");
  assert.equal(legacy.rows[0].status, "STALE");
  assert.match(legacy.rows[0].stale_reason, /trusted server writer/);

  const body = `'${userId}',1,'Baseline',now(),'One idea',
    '{"cash":100000}'::jsonb,'{}'::jsonb,'[]'::jsonb,
    '[{"ticker":"AAPL","action":"BUY","score":80,"rank":1,"last_price":100,"price_as_of":"2026-10-08T20:00:00Z"}]'::jsonb`;
  const saved = await db.query(`select public.save_generated_trading_plan(${body}) as id`);
  assert.match(saved.rows[0].id, /^[0-9a-f-]{36}$/i);
  const plan = await db.query(`select portfolio_snapshot from public.plans where id='${saved.rows[0].id}'`);
  assert.equal(plan.rows[0].portfolio_snapshot.plan_provenance, "TRUSTED_SERVER_V1");
  await db.exec("set role authenticated");
  await assert.rejects(db.query(`select public.save_generated_trading_plan(${body})`), /permission denied/);
  await db.exec("reset role");
  await db.close();
});
