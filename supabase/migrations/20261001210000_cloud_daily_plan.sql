begin;

create or replace function public.save_current_trading_plan(
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
security invoker
set search_path = ''
as $$
declare
  selected_account public.competition_accounts%rowtype;
  created_plan_id uuid;
  item jsonb;
begin
  select * into selected_account
  from public.competition_accounts
  where user_id = (select auth.uid())
  order by updated_at desc
  limit 1
  for update;

  if selected_account.id is null then
    raise exception 'No competition account exists.';
  end if;
  if p_expected_portfolio_version is null or p_expected_portfolio_version <> selected_account.portfolio_version then
    raise exception using errcode = '40001', message = 'Portfolio changed while the plan was being generated.';
  end if;
  if nullif(btrim(p_strategy_name), '') is null then
    raise exception 'Strategy name is required.';
  end if;
  if p_market_as_of is null then
    raise exception 'Market timestamp is required.';
  end if;
  if jsonb_typeof(p_portfolio_snapshot) <> 'object'
     or jsonb_typeof(p_regime) <> 'object'
     or jsonb_typeof(p_warnings) <> 'array'
     or jsonb_typeof(p_recommendations) <> 'array' then
    raise exception 'Plan payload has invalid JSON structure.';
  end if;

  update public.plans
  set status = 'ARCHIVED', stale_at = timezone('utc', now()), stale_reason = 'Replaced by a newer daily plan.'
  where user_id = (select auth.uid()) and account_id = selected_account.id and status = 'CURRENT';

  insert into public.plans (
    user_id, account_id, market_as_of, portfolio_version, strategy_name,
    status, summary, portfolio_snapshot, regime, warnings
  ) values (
    (select auth.uid()), selected_account.id, p_market_as_of,
    selected_account.portfolio_version, btrim(p_strategy_name), 'CURRENT',
    coalesce(p_summary, ''), p_portfolio_snapshot, p_regime, p_warnings
  ) returning id into created_plan_id;

  for item in select value from jsonb_array_elements(p_recommendations)
  loop
    insert into public.recommendations (
      user_id, plan_id, ticker, company, recommended_action, score, signal,
      reason, factor_details, rank, last_price, day_move_percent, price_as_of
    ) values (
      (select auth.uid()), created_plan_id, upper(btrim(item ->> 'ticker')),
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

revoke all on function public.save_current_trading_plan(bigint, text, timestamptz, text, jsonb, jsonb, jsonb, jsonb)
  from public, anon;
grant execute on function public.save_current_trading_plan(bigint, text, timestamptz, text, jsonb, jsonb, jsonb, jsonb)
  to authenticated;

commit;
