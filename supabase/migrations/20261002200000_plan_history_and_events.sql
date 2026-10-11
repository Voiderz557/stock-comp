begin;

-- Keep the current snapshot compact while exposing the explanation and stable
-- recommendation identity needed by the authenticated client.
create or replace function public.get_current_account_snapshot(p_account_id uuid default null)
returns jsonb
language plpgsql
security invoker
stable
set search_path = ''
as $$
declare
  selected_account public.competition_accounts%rowtype;
  selected_snapshot public.portfolio_snapshots%rowtype;
  selected_plan public.plans%rowtype;
  positions_json jsonb;
  recommendations_json jsonb;
begin
  select * into selected_account from public.competition_accounts
  where user_id = (select auth.uid()) and (p_account_id is null or id = p_account_id)
  order by updated_at desc limit 1;
  if selected_account.id is null then return null; end if;

  select * into selected_snapshot from public.portfolio_snapshots
  where user_id = (select auth.uid()) and account_id = selected_account.id
  order by portfolio_version desc nulls last, created_at desc limit 1;
  select * into selected_plan from public.plans
  where user_id = (select auth.uid()) and account_id = selected_account.id
  order by generated_at desc, created_at desc limit 1;

  select coalesce(jsonb_agg(jsonb_build_object(
    'ticker', ticker, 'side', direction, 'quantity', quantity,
    'average_price', average_price, 'exact_cost_basis', exact_cost_basis,
    'price', last_price, 'value', market_value, 'pnl', unrealized_pnl,
    'weight', portfolio_weight, 'price_as_of', price_as_of, 'source', source
  ) order by ticker, direction), '[]'::jsonb) into positions_json
  from public.positions
  where user_id = (select auth.uid()) and account_id = selected_account.id and status = 'OPEN';

  if selected_plan.id is null
     or selected_plan.status <> 'CURRENT'
     or selected_plan.portfolio_version <> selected_account.portfolio_version then
    recommendations_json := '[]'::jsonb;
  else
    select coalesce(jsonb_agg(jsonb_build_object(
      'id', id, 'ticker', ticker, 'company', coalesce(company, ticker),
      'action', recommended_action, 'score', score, 'signal', signal,
      'strategy', selected_plan.strategy_name, 'reason', reason,
      'factor_details', factor_details, 'rank', rank, 'status', status,
      'price', last_price, 'move', day_move_percent, 'price_as_of', price_as_of
    ) order by rank), '[]'::jsonb) into recommendations_json
    from public.recommendations where user_id = (select auth.uid()) and plan_id = selected_plan.id;
  end if;

  return jsonb_build_object(
    'snapshot_time', timezone('utc', now()), 'data_mode', 'cloud',
    'account', jsonb_build_object(
      'name', selected_account.name, 'starting_capital', selected_account.starting_capital,
      'snapshot_as_of', selected_account.snapshot_as_of,
      'portfolio_version', selected_account.portfolio_version
    ),
    'portfolio', jsonb_build_object(
      'value', selected_snapshot.portfolio_value, 'cash', selected_account.cash,
      'long_exposure', selected_snapshot.long_exposure, 'short_exposure', selected_snapshot.short_exposure,
      'unrealized_pnl', selected_snapshot.unrealized_pnl
    ),
    'plan', jsonb_build_object(
      'id', selected_plan.id, 'generated_at', selected_plan.generated_at,
      'market_as_of', selected_plan.market_as_of,
      'status', coalesce(selected_plan.status, 'NONE'),
      'strategy', coalesce(selected_plan.strategy_name, 'No strategy selected'),
      'summary', coalesce(selected_plan.summary, 'No plan has been generated.'),
      'warnings', coalesce(selected_plan.warnings, '[]'::jsonb)
    ),
    'regime', coalesce(selected_plan.regime, jsonb_build_object(
      'name', 'UNAVAILABLE', 'label', 'Unavailable', 'reason', 'No current market regime is available.',
      'trend_strength', null, 'market_breadth', null, 'volatility', null,
      'preferred_strategies', '[]'::jsonb
    )),
    'recommendations', recommendations_json, 'positions', positions_json
  );
end;
$$;

revoke all on function public.get_current_account_snapshot(uuid) from public, anon;
grant execute on function public.get_current_account_snapshot(uuid) to authenticated;

create or replace function public.get_account_plan_history(p_limit integer default 20)
returns jsonb
language plpgsql
security invoker
stable
set search_path = ''
as $$
declare
  selected_account_id uuid;
  history_json jsonb;
begin
  if p_limit is null or p_limit < 1 or p_limit > 50 then
    raise exception 'History limit must be between 1 and 50.';
  end if;
  select id into selected_account_id from public.competition_accounts
  where user_id = (select auth.uid()) order by updated_at desc limit 1;
  if selected_account_id is null then return '[]'::jsonb; end if;

  select coalesce(jsonb_agg(jsonb_build_object(
    'id', p.id, 'generated_at', p.generated_at, 'market_as_of', p.market_as_of,
    'portfolio_version', p.portfolio_version, 'strategy', p.strategy_name,
    'status', p.status, 'summary', p.summary, 'warnings', p.warnings,
    'cash_at_generation', p.portfolio_snapshot -> 'cash',
    'recommendations', coalesce((
      select jsonb_agg(jsonb_build_object(
        'ticker', r.ticker, 'action', r.recommended_action, 'signal', r.signal,
        'score', r.score, 'reason', r.reason, 'rank', r.rank, 'status', r.status
      ) order by r.rank)
      from public.recommendations r
      where r.user_id = (select auth.uid()) and r.plan_id = p.id
    ), '[]'::jsonb)
  ) order by p.generated_at desc, p.id desc), '[]'::jsonb) into history_json
  from (
    select id, generated_at, market_as_of, portfolio_version, strategy_name,
           status, summary, warnings, portfolio_snapshot
    from public.plans
    where user_id = (select auth.uid()) and account_id = selected_account_id
    order by generated_at desc, id desc limit p_limit
  ) p;
  return history_json;
end;
$$;

revoke all on function public.get_account_plan_history(integer) from public, anon;
grant execute on function public.get_account_plan_history(integer) to authenticated;

create or replace function public.set_recommendation_status(
  p_recommendation_id uuid,
  p_event_type text
)
returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
  selected_recommendation public.recommendations%rowtype;
  selected_account_id uuid;
  target_status text;
begin
  if p_event_type is null or p_event_type not in ('DISMISSED', 'REOPENED') then
    raise exception 'Only dismissal and reopening are supported.';
  end if;
  select id into selected_account_id from public.competition_accounts
  where user_id = (select auth.uid()) order by updated_at desc limit 1 for update;
  if selected_account_id is null then
    raise exception 'No competition account exists.';
  end if;
  select r.* into selected_recommendation
  from public.recommendations r
  join public.plans p on p.id = r.plan_id and p.user_id = r.user_id
  join public.competition_accounts a on a.id = p.account_id and a.user_id = p.user_id
  where r.id = p_recommendation_id and r.user_id = (select auth.uid())
    and a.id = selected_account_id
    and p.status = 'CURRENT' and p.portfolio_version = a.portfolio_version
  for update of r;
  if selected_recommendation.id is null then
    raise exception using errcode = '40001', message = 'Recommendation is unavailable or its plan is stale.';
  end if;
  if selected_recommendation.status = 'RECORDED' then
    raise exception 'A recorded recommendation cannot be dismissed or reopened.';
  end if;
  target_status := case when p_event_type = 'DISMISSED' then 'DISMISSED' else 'OPEN' end;
  if selected_recommendation.status = target_status then
    return selected_recommendation.id;
  end if;
  update public.recommendations set status = target_status
  where id = selected_recommendation.id and user_id = (select auth.uid());
  insert into public.recommendation_events (user_id, recommendation_id, event_type)
  values ((select auth.uid()), selected_recommendation.id, p_event_type);
  return selected_recommendation.id;
end;
$$;

revoke all on function public.set_recommendation_status(uuid, text) from public, anon;
grant execute on function public.set_recommendation_status(uuid, text) to authenticated;

-- Authenticated clients may read their RLS-scoped rows, but writes to account
-- state must use the checked RPCs. Their owner is the migration role, while
-- each function still scopes its work to auth.uid().
alter function public.create_or_get_competition_account(text, numeric, numeric, timestamptz) security definer;
alter function public.update_competition_account_snapshot(text, numeric, numeric, timestamptz, jsonb, bigint) security definer;
alter function public.save_current_trading_plan(bigint, text, timestamptz, text, jsonb, jsonb, jsonb, jsonb) security definer;
alter function public.set_recommendation_status(uuid, text) security definer;

revoke insert, update, delete on public.competition_accounts from authenticated;
revoke insert, update, delete on public.positions from authenticated;
revoke insert, update, delete on public.plans from authenticated;
revoke insert, update, delete on public.recommendations from authenticated;
revoke insert, update, delete on public.recommendation_events from authenticated;
revoke insert, update, delete on public.paper_actions from authenticated;
revoke insert, update, delete on public.portfolio_snapshots from authenticated;
revoke insert, update, delete on public.audit_events from authenticated;
revoke insert, update, delete on public.import_jobs from authenticated;

commit;
