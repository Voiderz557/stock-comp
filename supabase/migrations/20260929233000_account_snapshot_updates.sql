begin;

alter table public.portfolio_snapshots
  drop constraint if exists portfolio_snapshots_account_id_as_of_key;

alter table public.portfolio_snapshots
  add column if not exists portfolio_version bigint check (portfolio_version is null or portfolio_version > 0);

alter table public.positions
  add column if not exists replaced_at timestamptz;

alter table public.positions
  drop constraint if exists positions_status_check;

alter table public.positions
  add constraint positions_status_check check (status in ('OPEN', 'CLOSED', 'REPLACED'));

create index if not exists portfolio_snapshots_account_asof_idx
  on public.portfolio_snapshots (account_id, as_of desc);

create index if not exists portfolio_snapshots_account_version_idx
  on public.portfolio_snapshots (account_id, portfolio_version desc, created_at desc);

drop function if exists public.update_competition_account_snapshot(text, numeric, numeric, timestamptz, jsonb);

create or replace function public.update_competition_account_snapshot(
  p_name text,
  p_starting_capital numeric,
  p_cash numeric,
  p_snapshot_as_of timestamptz,
  p_positions jsonb,
  p_expected_portfolio_version bigint
)
returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
  selected_account public.competition_accounts%rowtype;
  prior_snapshot public.portfolio_snapshots%rowtype;
  prior_positions jsonb;
  item jsonb;
  normalized_ticker text;
  normalized_direction text;
  position_count integer;
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
    raise exception using
      errcode = '40001',
      message = 'Portfolio changed since this form was loaded. Refresh and review the latest account before saving.';
  end if;
  if nullif(btrim(p_name), '') is null or char_length(btrim(p_name)) > 120 then
    raise exception 'Account name must contain 1 to 120 characters.';
  end if;
  if p_starting_capital < 0 or p_cash < 0 then
    raise exception 'Capital and cash cannot be negative.';
  end if;
  if p_snapshot_as_of is null then
    raise exception 'Snapshot date is required.';
  end if;
  if jsonb_typeof(p_positions) <> 'array' then
    raise exception 'Positions must be a JSON array.';
  end if;
  if jsonb_array_length(p_positions) > 200 then
    raise exception 'A snapshot may contain at most 200 positions.';
  end if;

  for item in select value from jsonb_array_elements(p_positions)
  loop
    normalized_ticker := upper(btrim(item ->> 'ticker'));
    normalized_direction := upper(btrim(item ->> 'side'));
    if normalized_ticker !~ '^[A-Z0-9.\-]{1,16}$' then
      raise exception 'Each ticker must contain 1 to 16 valid symbol characters.';
    end if;
    if normalized_direction not in ('LONG', 'SHORT') then
      raise exception 'Each position side must be LONG or SHORT.';
    end if;
    if coalesce((item ->> 'quantity')::numeric, 0) <= 0 then
      raise exception 'Each position quantity must be greater than zero.';
    end if;
    if nullif(item ->> 'average_price', '') is null and nullif(item ->> 'exact_cost_basis', '') is null then
      raise exception 'Each position needs an average price or exact cost basis.';
    end if;
    if coalesce(nullif(item ->> 'average_price', '')::numeric, 0) < 0
       or coalesce(nullif(item ->> 'exact_cost_basis', '')::numeric, 0) < 0 then
      raise exception 'Position cost values cannot be negative.';
    end if;
  end loop;

  select count(*) into position_count
  from (
    select upper(btrim(value ->> 'ticker')), upper(btrim(value ->> 'side'))
    from jsonb_array_elements(p_positions)
    group by 1, 2
    having count(*) > 1
  ) duplicates;
  if position_count > 0 then
    raise exception 'Duplicate ticker and side combinations are not allowed.';
  end if;

  select coalesce(jsonb_agg(jsonb_build_object(
    'ticker', ticker, 'side', direction, 'quantity', quantity,
    'average_price', average_price, 'exact_cost_basis', exact_cost_basis,
    'source', source
  ) order by ticker, direction), '[]'::jsonb)
  into prior_positions
  from public.positions
  where user_id = (select auth.uid()) and account_id = selected_account.id and status = 'OPEN';

  select * into prior_snapshot
  from public.portfolio_snapshots
  where user_id = (select auth.uid()) and account_id = selected_account.id
  order by portfolio_version desc nulls last, created_at desc
  limit 1;

  insert into public.portfolio_snapshots (
    user_id, account_id, as_of, cash, portfolio_value, long_exposure,
    short_exposure, unrealized_pnl, positions_snapshot, warnings, portfolio_version
  ) values (
    (select auth.uid()), selected_account.id, timezone('utc', now()), selected_account.cash,
    prior_snapshot.portfolio_value, prior_snapshot.long_exposure,
    prior_snapshot.short_exposure, prior_snapshot.unrealized_pnl,
    prior_positions, jsonb_build_array('Backup created before account snapshot replacement.'),
    selected_account.portfolio_version
  );

  update public.plans
  set status = 'STALE', stale_at = timezone('utc', now()),
      stale_reason = 'Portfolio account snapshot was updated.'
  where user_id = (select auth.uid()) and account_id = selected_account.id and status = 'CURRENT';

  update public.positions
  set status = 'REPLACED', replaced_at = clock_timestamp()
  where user_id = (select auth.uid()) and account_id = selected_account.id and status = 'OPEN';

  insert into public.positions (
    user_id, account_id, ticker, direction, quantity, average_price,
    exact_cost_basis, status, source
  )
  select
    (select auth.uid()), selected_account.id,
    upper(btrim(value ->> 'ticker')), upper(btrim(value ->> 'side')),
    (value ->> 'quantity')::numeric,
    nullif(value ->> 'average_price', '')::numeric,
    nullif(value ->> 'exact_cost_basis', '')::numeric,
    'OPEN', 'ACCOUNT_SNAPSHOT'
  from jsonb_array_elements(p_positions);

  update public.competition_accounts
  set name = btrim(p_name), starting_capital = p_starting_capital,
      cash = p_cash, snapshot_as_of = p_snapshot_as_of,
      portfolio_version = portfolio_version + 1
  where id = selected_account.id and user_id = (select auth.uid());

  insert into public.portfolio_snapshots (
    user_id, account_id, as_of, cash, portfolio_value, long_exposure,
    short_exposure, unrealized_pnl, positions_snapshot, warnings, portfolio_version
  ) values (
    (select auth.uid()), selected_account.id, p_snapshot_as_of, p_cash,
    case when jsonb_array_length(p_positions) = 0 then p_cash else null end,
    case when jsonb_array_length(p_positions) = 0 then 0 else null end,
    case when jsonb_array_length(p_positions) = 0 then 0 else null end,
    null, p_positions,
    case when jsonb_array_length(p_positions) = 0 then '[]'::jsonb
         else jsonb_build_array('Current prices are required before portfolio value and P&L can be calculated.') end,
    selected_account.portfolio_version + 1
  );

  return selected_account.id;
end;
$$;

revoke all on function public.update_competition_account_snapshot(text, numeric, numeric, timestamptz, jsonb, bigint)
  from public, anon;
grant execute on function public.update_competition_account_snapshot(text, numeric, numeric, timestamptz, jsonb, bigint)
  to authenticated;

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
  order by generated_at desc limit 1;

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
      'ticker', ticker, 'company', coalesce(company, ticker), 'action', recommended_action,
      'score', score, 'strategy', selected_plan.strategy_name, 'reason', reason,
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
      'generated_at', selected_plan.generated_at, 'market_as_of', selected_plan.market_as_of,
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

commit;
