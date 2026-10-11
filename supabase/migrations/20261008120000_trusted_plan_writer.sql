begin;

-- A user JWT may read its own plans and dismiss recommendations, but may not
-- create a plan with caller-supplied prices or Proposed Quantity. The API
-- verifies that JWT, builds the plan itself, and calls this writer using a
-- backend-only service-role key. No key or signing secret lives in SQL.
revoke all on function public.save_current_trading_plan(
  bigint, text, timestamptz, text, jsonb, jsonb, jsonb, jsonb
) from public, anon, authenticated;

-- Existing plans have no cryptographic/server provenance. Keep their history,
-- but require regeneration before any further paper action is recorded.
update public.plans
set status = 'STALE', stale_at = timezone('utc', now()),
    stale_reason = 'Plan predates the trusted server writer; regenerate before paper actions.'
where status = 'CURRENT';

create or replace function public.save_generated_trading_plan(
  p_user_id uuid,
  p_expected_portfolio_version bigint,
  p_strategy_name text,
  p_market_as_of timestamptz,
  p_summary text,
  p_portfolio_snapshot jsonb,
  p_regime jsonb,
  p_warnings jsonb,
  p_recommendations jsonb
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  selected_account public.competition_accounts%rowtype;
  created_plan_id uuid;
  item jsonb;
begin
  -- PostgreSQL checks EXECUTE privileges before entering this SECURITY
  -- DEFINER function. Only service_role is granted access below. Rely on
  -- that database role, not JWT claim GUCs: modern sb_secret_* keys are
  -- mapped to service_role by Supabase without being JWTs themselves.
  if p_user_id is null then
    raise exception 'A verified user identity is required.';
  end if;
  if p_expected_portfolio_version is null or nullif(btrim(p_strategy_name), '') is null
     or p_market_as_of is null
     or jsonb_typeof(p_portfolio_snapshot) <> 'object'
     or jsonb_typeof(p_regime) <> 'object'
     or jsonb_typeof(p_warnings) <> 'array'
     or jsonb_typeof(p_recommendations) <> 'array' then
    raise exception 'Plan payload has invalid structure.';
  end if;
  if jsonb_array_length(p_recommendations) > 500
     or octet_length(p_recommendations::text) > 1000000 then
    raise exception 'Plan recommendations exceed the supported size.';
  end if;
  select * into selected_account
  from public.competition_accounts
  where user_id = p_user_id
  order by updated_at desc
  limit 1
  for update;
  if selected_account.id is null then
    raise exception 'No competition account exists.';
  end if;
  if selected_account.portfolio_version <> p_expected_portfolio_version then
    raise exception using errcode = '40001',
      message = 'Portfolio changed while the plan was being generated.';
  end if;

  update public.plans
  set status = 'ARCHIVED', stale_at = timezone('utc', now()),
      stale_reason = 'Replaced by a newer daily plan.'
  where user_id = p_user_id and account_id = selected_account.id and status = 'CURRENT';

  insert into public.plans (
    user_id, account_id, market_as_of, portfolio_version, strategy_name,
    status, summary, portfolio_snapshot, regime, warnings
  ) values (
    p_user_id, selected_account.id, p_market_as_of,
    selected_account.portfolio_version, btrim(p_strategy_name), 'CURRENT',
    coalesce(p_summary, ''),
    p_portfolio_snapshot || jsonb_build_object('plan_provenance', 'TRUSTED_SERVER_V1'),
    p_regime, p_warnings
  ) returning id into created_plan_id;

  for item in select value from jsonb_array_elements(p_recommendations)
  loop
    insert into public.recommendations (
      user_id, plan_id, ticker, company, recommended_action, score, signal,
      reason, factor_details, rank, last_price, day_move_percent, price_as_of
    ) values (
      p_user_id, created_plan_id, upper(btrim(item ->> 'ticker')),
      coalesce(nullif(btrim(item ->> 'company'), ''), upper(btrim(item ->> 'ticker'))),
      upper(item ->> 'action'), (item ->> 'score')::numeric,
      coalesce(item ->> 'signal', item ->> 'action'), coalesce(item ->> 'reason', ''),
      coalesce(item -> 'factor_details', '{}'::jsonb), (item ->> 'rank')::integer,
      nullif(item ->> 'last_price', '')::numeric,
      nullif(item ->> 'day_move_percent', '')::numeric,
      nullif(item ->> 'price_as_of', '')::timestamptz
    );
  end loop;
  return created_plan_id;
end;
$$;

revoke all on function public.save_generated_trading_plan(
  uuid, bigint, text, timestamptz, text, jsonb, jsonb, jsonb, jsonb
) from public, anon, authenticated;
grant execute on function public.save_generated_trading_plan(
  uuid, bigint, text, timestamptz, text, jsonb, jsonb, jsonb, jsonb
) to service_role;

commit;
