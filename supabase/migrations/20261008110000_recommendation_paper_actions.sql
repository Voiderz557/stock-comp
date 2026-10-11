begin;

-- Paper fills are estimates at the plan's dated daily close, never broker orders.
alter table public.paper_actions
  add column if not exists price_source text not null default 'USER_RECORDED';
alter table public.paper_actions
  add column if not exists basis_released numeric(18, 4);
alter table public.paper_actions
  add column if not exists realized_pnl numeric(18, 4);

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
  where user_id = (select auth.uid()) order by updated_at desc limit 1 for update;
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

revoke all on function public.record_recommendation_paper_action(uuid, numeric, bigint, text)
  from public, anon;
grant execute on function public.record_recommendation_paper_action(uuid, numeric, bigint, text)
  to authenticated;

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
  where user_id = (select auth.uid()) order by updated_at desc limit 1;
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

revoke all on function public.get_recent_paper_actions(integer) from public, anon;
grant execute on function public.get_recent_paper_actions(integer) to authenticated;

commit;
