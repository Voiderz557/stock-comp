begin;

-- Creation keys survive retries; existing accounts and runtime history are untouched.
drop index public.competition_accounts_one_per_user;
alter table public.competition_accounts add column creation_request_id uuid;
create unique index competition_accounts_creation_request
  on public.competition_accounts(user_id, creation_request_id);

create or replace function public.selected_portfolio_id()
returns uuid language plpgsql stable security invoker set search_path = '' as $$
declare
  headers jsonb;
  selected_id uuid;
  owned_count integer;
begin
  if auth.uid() is null then raise exception 'Authentication is required.'; end if;
  headers := coalesce(nullif(current_setting('request.headers', true), ''), '{}')::jsonb;
  if jsonb_typeof(headers) <> 'object' then raise exception 'Invalid request headers.'; end if;
  if headers ? 'x-portfolio-id' then
    if jsonb_typeof(headers -> 'x-portfolio-id') <> 'string'
       or coalesce(headers ->> 'x-portfolio-id', '') !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
      raise exception 'Invalid x-portfolio-id header.';
    end if;
    selected_id := (headers ->> 'x-portfolio-id')::uuid;
    if not exists (select 1 from public.competition_accounts where id = selected_id and user_id = auth.uid()) then
      raise exception 'Selected portfolio is not owned by this user.';
    end if;
    return selected_id;
  end if;
  select count(*), (array_agg(id))[1] into owned_count, selected_id
  from public.competition_accounts where user_id = auth.uid();
  if owned_count > 1 then raise exception 'Explicit portfolio selection is required.'; end if;
  return selected_id;
end;
$$;
revoke all on function public.selected_portfolio_id() from public, anon;
grant execute on function public.selected_portfolio_id() to authenticated;

create or replace function public.list_competition_accounts()
returns jsonb language plpgsql stable security invoker set search_path = '' as $$
begin
  if auth.uid() is null then raise exception 'Authentication is required.'; end if;
  return (select coalesce(jsonb_agg(jsonb_build_object(
    'id', id, 'name', name, 'starting_capital', starting_capital, 'cash', cash,
    'snapshot_as_of', snapshot_as_of, 'portfolio_version', portfolio_version,
    'created_at', created_at, 'updated_at', updated_at
  ) order by created_at, id), '[]'::jsonb)
  from public.competition_accounts where user_id = auth.uid());
end;
$$;
revoke all on function public.list_competition_accounts() from public, anon;
grant execute on function public.list_competition_accounts() to authenticated;

create or replace function public.create_portfolio(
  p_name text, p_starting_capital numeric, p_cash numeric,
  p_snapshot_as_of timestamptz, p_request_id uuid
)
returns uuid language plpgsql security definer set search_path = '' as $$
declare
  account_id uuid;
begin
  if auth.uid() is null then raise exception 'Authentication is required.'; end if;
  if p_request_id is null then raise exception 'Creation request ID is required.'; end if;
  -- Shared with first-login creation: serialize the count and insert per user.
  perform pg_advisory_xact_lock(hashtextextended(auth.uid()::text, 0));
  select id into account_id from public.competition_accounts
  where user_id = auth.uid() and creation_request_id = p_request_id;
  if account_id is not null then return account_id; end if;
  if nullif(btrim(p_name), '') is null or char_length(btrim(p_name)) > 120 then
    raise exception 'Account name must contain 1 to 120 characters.';
  end if;
  if p_starting_capital is null or p_cash is null
     or p_starting_capital::text in ('NaN', 'Infinity', '-Infinity')
     or p_cash::text in ('NaN', 'Infinity', '-Infinity')
     or p_starting_capital < 0 or p_cash < 0 then
    raise exception 'Capital and cash must be finite and nonnegative.';
  end if;
  if p_snapshot_as_of is null or not isfinite(p_snapshot_as_of) then
    raise exception 'A finite snapshot date is required.';
  end if;
  if (select count(*) from public.competition_accounts where user_id = auth.uid()) >= 20 then
    raise exception 'At most 20 portfolios are allowed.';
  end if;
  insert into public.competition_accounts(user_id, name, starting_capital, cash, snapshot_as_of, creation_request_id)
  values (auth.uid(), btrim(p_name), p_starting_capital, p_cash, p_snapshot_as_of, p_request_id)
  returning id into account_id;
  insert into public.portfolio_snapshots(user_id, account_id, as_of, cash, portfolio_value,
    long_exposure, short_exposure, unrealized_pnl, positions_snapshot, warnings, portfolio_version)
  values (auth.uid(), account_id, p_snapshot_as_of, p_cash, p_cash, 0, 0, null, '[]', '[]', 1);
  return account_id;
end;
$$;
revoke all on function public.create_portfolio(text,numeric,numeric,timestamptz,uuid) from public, anon;
grant execute on function public.create_portfolio(text,numeric,numeric,timestamptz,uuid) to authenticated;

create or replace function public.create_or_get_competition_account(
  p_name text, p_starting_capital numeric, p_cash numeric, p_snapshot_as_of timestamptz
)
returns uuid language plpgsql security definer set search_path = '' as $$
declare
  account_id uuid;
begin
  if auth.uid() is null then raise exception 'Authentication is required.'; end if;
  perform pg_advisory_xact_lock(hashtextextended(auth.uid()::text, 0));
  if exists (select 1 from public.competition_accounts where user_id = auth.uid()) then
    return public.selected_portfolio_id();
  end if;
  -- Validate any supplied selector even on an empty login; never create on a bad header.
  account_id := public.selected_portfolio_id();
  return public.create_portfolio(p_name, p_starting_capital, p_cash, p_snapshot_as_of, gen_random_uuid());
end;
$$;

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
security definer
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
  where user_id = (select auth.uid()) and id = public.selected_portfolio_id()
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
  if p_account_id is not null then
    if auth.uid() is null or not exists (select 1 from public.competition_accounts
      where id = p_account_id and user_id = auth.uid()) then
      raise exception 'Selected portfolio is not owned by this user.';
    end if;
    if coalesce(nullif(current_setting('request.headers', true), ''), '{}')::jsonb ? 'x-portfolio-id'
       and public.selected_portfolio_id() is distinct from p_account_id then
      raise exception 'Explicit account and portfolio header disagree.';
    end if;
  else
    p_account_id := public.selected_portfolio_id();
  end if;
  select * into selected_account from public.competition_accounts
  where user_id = (select auth.uid()) and id = p_account_id;
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
      'id', selected_account.id, 'name', selected_account.name, 'starting_capital', selected_account.starting_capital,
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
  where user_id = (select auth.uid()) and id = public.selected_portfolio_id();
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

create or replace function public.set_recommendation_status(
  p_recommendation_id uuid,
  p_event_type text
)
returns uuid
language plpgsql
security definer
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
  where user_id = (select auth.uid()) and id = public.selected_portfolio_id() for update;
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

create or replace function public.record_recommendation_paper_action(
  p_recommendation_id uuid,
  p_quantity numeric,
  p_expected_portfolio_version bigint,
  p_idempotency_key text
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  selected_account public.competition_accounts%rowtype;
  selected_plan public.plans%rowtype;
  selected_recommendation public.recommendations%rowtype;
  existing_action public.paper_actions%rowtype;
  existing_long public.positions%rowtype;
  position_row public.positions%rowtype;
  quote_price numeric;
  quote_as_of timestamptz;
  fill_price numeric;
  notional numeric;
  gross_exposure numeric := 0;
  same_ticker_exposure numeric := 0;
  long_exposure numeric := 0;
  short_exposure numeric := 0;
  portfolio_value numeric;
  unrealized_pnl numeric := 0;
  row_market_value numeric;
  row_pnl numeric;
  row_basis numeric;
  basis_released numeric := 0;
  realized_pnl numeric := 0;
  action_type text;
  created_action_id uuid;
  positions_json jsonb;
  new_cash numeric;
begin
  if p_recommendation_id is null or p_quantity is null or p_quantity <= 0
     or p_quantity <> trunc(p_quantity) or p_quantity > 1000000
     or p_expected_portfolio_version is null
     or p_idempotency_key is null or char_length(p_idempotency_key) not between 8 and 200 then
    raise exception 'A recommendation, whole-share quantity, portfolio version, and idempotency key are required.';
  end if;

  select * into selected_account from public.competition_accounts
  where user_id = (select auth.uid()) and id = public.selected_portfolio_id() for update;
  if selected_account.id is null then raise exception 'No competition account exists.'; end if;

  -- The account lock serializes retries and all competing account writes.
  select * into existing_action from public.paper_actions
  where user_id = (select auth.uid()) and idempotency_key = p_idempotency_key;
  if existing_action.id is not null then
    if existing_action.account_id <> selected_account.id
       or existing_action.recommendation_id is distinct from p_recommendation_id
       or existing_action.quantity <> p_quantity
       or existing_action.portfolio_version_before <> p_expected_portfolio_version then
      raise exception using errcode = '40001', message = 'Idempotency key was already used for a different paper action.';
    end if;
    return existing_action.id;
  end if;

  if selected_account.portfolio_version <> p_expected_portfolio_version then
    raise exception using errcode = '40001', message = 'Portfolio changed. Refresh before recording a paper action.';
  end if;
  select * into selected_recommendation from public.recommendations
  where id = p_recommendation_id and user_id = (select auth.uid()) and status = 'OPEN';
  if selected_recommendation.id is null then
    raise exception using errcode = '40001', message = 'Recommendation is unavailable or already recorded.';
  end if;
  select * into selected_plan from public.plans
  where id = selected_recommendation.plan_id and account_id = selected_account.id
    and user_id = (select auth.uid()) and status = 'CURRENT'
    and portfolio_version = selected_account.portfolio_version;
  if selected_plan.id is null
     or selected_plan.id is distinct from (
       select id from public.plans where user_id = (select auth.uid())
         and account_id = selected_account.id order by generated_at desc, id desc limit 1
     ) then
    raise exception using errcode = '40001', message = 'The plan is stale. Generate a new plan before recording an action.';
  end if;
  if selected_recommendation.recommended_action not in ('BUY', 'SELL') then
    raise exception 'Only BUY and review-to-sell recommendations can be recorded.';
  end if;
  if selected_plan.market_as_of > now()
     or selected_plan.market_as_of < now() - interval '4 days' then
    raise exception using errcode = '40001', message = 'The plan market date is too old. Generate a fresh plan.';
  end if;
  fill_price := selected_recommendation.last_price;
  if fill_price is null or fill_price <= 0
     or selected_recommendation.price_as_of is null
     or selected_recommendation.price_as_of > now()
     or selected_recommendation.price_as_of::date <> selected_plan.market_as_of::date then
    raise exception using errcode = '40001', message = 'A current dated daily close is required for the paper fill.';
  end if;
  if selected_recommendation.recommended_action = 'BUY' then
    if fill_price < 5 then raise exception 'The stock price is below the $5 competition minimum.'; end if;
    if nullif(selected_recommendation.factor_details ->> 'Proposed Quantity', '') is null
       or p_quantity > (selected_recommendation.factor_details ->> 'Proposed Quantity')::numeric then
      raise exception 'Quantity exceeds the saved plan proposal.';
    end if;
  end if;
  notional := p_quantity * fill_price;

  -- Every open holding must have a plan quote from the same market session.
  -- Imported cost basis is never substituted for a missing market price.
  for position_row in select * from public.positions
    where user_id = (select auth.uid()) and account_id = selected_account.id and status = 'OPEN'
  loop
    select last_price, price_as_of into quote_price, quote_as_of
    from public.recommendations where user_id = (select auth.uid())
      and plan_id = selected_plan.id and ticker = position_row.ticker;
    if position_row.direction = 'SHORT' then
      raise exception using errcode = 'P0001',
        message = 'Open SHORT holdings require cash-proceeds reconciliation before paper actions.';
    end if;
    if quote_price is null or quote_price <= 0 or quote_as_of is null
       or quote_as_of > now()
       or quote_as_of::date <> selected_plan.market_as_of::date then
      raise exception using errcode = 'P0001', message = 'A holding lacks a current plan quote; capacity cannot be checked.';
    end if;
    gross_exposure := gross_exposure + position_row.quantity * quote_price;
    if position_row.ticker = selected_recommendation.ticker and position_row.direction = 'LONG' then
      same_ticker_exposure := same_ticker_exposure + position_row.quantity * quote_price;
      existing_long := position_row;
    end if;
  end loop;

  if selected_recommendation.recommended_action = 'BUY' then
    if selected_account.cash < notional then raise exception 'Insufficient cash for this paper action.'; end if;
    if selected_account.starting_capital <= 0
       or gross_exposure + notional > selected_account.starting_capital then
      raise exception 'Shared gross-exposure capacity would be exceeded.';
    end if;
    if same_ticker_exposure + notional > 20000 then
      raise exception 'The $20,000 per-stock purchase cap would be exceeded.';
    end if;
    if existing_long.id is null then
      action_type := 'OPEN';
      insert into public.positions (
        user_id, account_id, ticker, direction, quantity, average_price,
        exact_cost_basis, opened_at, status, source
      ) values (
        (select auth.uid()), selected_account.id, selected_recommendation.ticker,
        'LONG', p_quantity, fill_price, notional, timezone('utc', now()), 'OPEN', 'PAPER_ACTION'
      );
    else
      action_type := 'INCREASE';
      row_basis := coalesce(existing_long.exact_cost_basis, existing_long.average_price * existing_long.quantity);
      update public.positions set
        quantity = existing_long.quantity + p_quantity,
        exact_cost_basis = row_basis + notional,
        average_price = (row_basis + notional) / (existing_long.quantity + p_quantity)
      where id = existing_long.id and user_id = (select auth.uid());
    end if;
    new_cash := selected_account.cash - notional;
  else
    if existing_long.id is null or p_quantity > existing_long.quantity then
      raise exception 'There are not enough open LONG shares to record this exit.';
    end if;
    row_basis := coalesce(existing_long.exact_cost_basis, existing_long.average_price * existing_long.quantity);
    basis_released := row_basis * p_quantity / existing_long.quantity;
    realized_pnl := notional - basis_released;
    if p_quantity = existing_long.quantity then
      action_type := 'CLOSE';
      update public.positions set status = 'CLOSED', closed_at = timezone('utc', now()),
        last_price = fill_price, price_as_of = selected_recommendation.price_as_of
      where id = existing_long.id and user_id = (select auth.uid());
    else
      action_type := 'DECREASE';
      update public.positions set quantity = existing_long.quantity - p_quantity,
        exact_cost_basis = row_basis - basis_released,
        average_price = (row_basis - basis_released) / (existing_long.quantity - p_quantity)
      where id = existing_long.id and user_id = (select auth.uid());
    end if;
    new_cash := selected_account.cash + notional;
  end if;

  update public.competition_accounts set cash = new_cash,
    snapshot_as_of = timezone('utc', now()), portfolio_version = portfolio_version + 1
  where id = selected_account.id and user_id = (select auth.uid());

  -- Revalue the resulting portfolio using the saved plan's dated closes.
  portfolio_value := new_cash;
  for position_row in select * from public.positions
    where user_id = (select auth.uid()) and account_id = selected_account.id and status = 'OPEN'
  loop
    select last_price, price_as_of into quote_price, quote_as_of from public.recommendations
    where user_id = (select auth.uid()) and plan_id = selected_plan.id and ticker = position_row.ticker;
    row_basis := coalesce(position_row.exact_cost_basis, position_row.average_price * position_row.quantity);
    row_pnl := position_row.quantity * quote_price - row_basis;
    row_market_value := position_row.quantity * quote_price;
    long_exposure := long_exposure + row_market_value;
    unrealized_pnl := unrealized_pnl + row_pnl;
    portfolio_value := portfolio_value + row_market_value;
    update public.positions set last_price = quote_price, price_as_of = quote_as_of,
      market_value = row_market_value, unrealized_pnl = row_pnl,
      portfolio_weight = null
    where id = position_row.id and user_id = (select auth.uid());
  end loop;

  select coalesce(jsonb_agg(jsonb_build_object(
    'ticker', ticker, 'side', direction, 'quantity', quantity,
    'average_price', average_price, 'exact_cost_basis', exact_cost_basis,
    'price', last_price, 'price_as_of', price_as_of, 'source', source
  ) order by ticker, direction), '[]'::jsonb) into positions_json
  from public.positions where user_id = (select auth.uid())
    and account_id = selected_account.id and status = 'OPEN';

  insert into public.portfolio_snapshots (
    user_id, account_id, as_of, cash, portfolio_value, long_exposure,
    short_exposure, unrealized_pnl, positions_snapshot, warnings, portfolio_version
  ) values (
    (select auth.uid()), selected_account.id, timezone('utc', now()), new_cash,
    portfolio_value, long_exposure, short_exposure, unrealized_pnl, positions_json,
    jsonb_build_array('Paper fill and portfolio values use saved daily closes, not live broker prices.'),
    selected_account.portfolio_version + 1
  );

  insert into public.paper_actions (
    user_id, account_id, plan_id, recommendation_id, action_type, ticker,
    direction, quantity, price, occurred_at, idempotency_key,
    portfolio_version_before, portfolio_version_after,
    price_source, basis_released, realized_pnl
  ) values (
    (select auth.uid()), selected_account.id, selected_plan.id,
    selected_recommendation.id, action_type, selected_recommendation.ticker,
    'LONG', p_quantity, fill_price, timezone('utc', now()), p_idempotency_key,
    selected_account.portfolio_version, selected_account.portfolio_version + 1,
    'SAVED_DAILY_CLOSE_ESTIMATE', basis_released, realized_pnl
  ) returning id into created_action_id;

  update public.recommendations set status = 'RECORDED'
  where id = selected_recommendation.id and user_id = (select auth.uid());
  insert into public.recommendation_events (user_id, recommendation_id, event_type)
  values ((select auth.uid()), selected_recommendation.id, 'RECORDED');
  update public.plans set status = 'STALE', stale_at = timezone('utc', now()),
    stale_reason = 'A linked paper action changed the portfolio.'
  where id = selected_plan.id and user_id = (select auth.uid());
  return created_action_id;
end;
$$;

create or replace function public.get_recent_paper_actions(p_limit integer default 50)
returns jsonb
language plpgsql
security invoker
stable
set search_path = ''
as $$
declare
  selected_account_id uuid;
  actions_json jsonb;
begin
  if p_limit is null or p_limit < 1 or p_limit > 100 then
    raise exception 'History limit must be between 1 and 100.';
  end if;
  select id into selected_account_id from public.competition_accounts
  where user_id = (select auth.uid()) and id = public.selected_portfolio_id();
  if selected_account_id is null then return '[]'::jsonb; end if;
  select coalesce(jsonb_agg(to_jsonb(a) - 'user_id' - 'account_id' - 'idempotency_key'
    order by a.occurred_at desc, a.id desc), '[]'::jsonb) into actions_json
  from (
    select * from public.paper_actions
    where user_id = (select auth.uid()) and account_id = selected_account_id
    order by occurred_at desc, id desc limit p_limit
  ) a;
  return actions_json;
end;
$$;

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
security definer
set search_path = ''
as $$
declare
  selected_account public.competition_accounts%rowtype;
  created_plan_id uuid;
  item jsonb;
begin
  select * into selected_account
  from public.competition_accounts
  where user_id = (select auth.uid()) and id = public.selected_portfolio_id()
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
  target_account_id uuid;
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
  if p_portfolio_snapshot ? 'account_id' then
    if jsonb_typeof(p_portfolio_snapshot -> 'account_id') <> 'string'
       or coalesce(p_portfolio_snapshot ->> 'account_id', '') !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
      raise exception 'Invalid portfolio snapshot account_id.';
    end if;
    target_account_id := (p_portfolio_snapshot ->> 'account_id')::uuid;
  else
    if (select count(*) from public.competition_accounts where user_id = p_user_id) > 1 then
      raise exception 'Explicit portfolio selection is required.';
    end if;
    select id into target_account_id from public.competition_accounts where user_id = p_user_id;
  end if;
  select * into selected_account from public.competition_accounts
  where user_id = p_user_id and id = target_account_id for update;
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

-- CREATE OR REPLACE preserves existing ACLs. Explicitly retain the trusted boundary.
revoke all on function public.save_current_trading_plan(bigint,text,timestamptz,text,jsonb,jsonb,jsonb,jsonb) from public, anon, authenticated;
revoke all on function public.save_generated_trading_plan(uuid,bigint,text,timestamptz,text,jsonb,jsonb,jsonb,jsonb) from public, anon, authenticated;
grant execute on function public.save_generated_trading_plan(uuid,bigint,text,timestamptz,text,jsonb,jsonb,jsonb,jsonb) to service_role;
commit;
