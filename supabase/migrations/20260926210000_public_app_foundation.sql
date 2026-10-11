begin;

create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

revoke all on function public.set_updated_at() from public, anon, authenticated;

create table public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  terms_version text,
  terms_accepted_at timestamptz,
  privacy_version text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  deleted_at timestamptz
);

create table public.competition_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  name text not null default 'Competition account' check (char_length(name) between 1 and 120),
  starting_capital numeric(18, 4) not null check (starting_capital >= 0),
  cash numeric(18, 4) not null check (cash >= 0),
  snapshot_as_of timestamptz not null,
  portfolio_version bigint not null default 1 check (portfolio_version > 0),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (id, user_id)
);

create table public.positions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  account_id uuid not null,
  ticker text not null check (ticker = upper(ticker) and char_length(ticker) between 1 and 16),
  direction text not null check (direction in ('LONG', 'SHORT')),
  quantity numeric(24, 8) not null check (quantity > 0),
  average_price numeric(18, 6) check (average_price >= 0),
  exact_cost_basis numeric(18, 4) check (exact_cost_basis >= 0),
  last_price numeric(18, 6) check (last_price >= 0),
  market_value numeric(18, 4),
  unrealized_pnl numeric(18, 4),
  portfolio_weight numeric(9, 4),
  price_as_of timestamptz,
  opened_at timestamptz,
  closed_at timestamptz,
  status text not null default 'OPEN' check (status in ('OPEN', 'CLOSED')),
  source text not null check (source in ('ACCOUNT_SNAPSHOT', 'PAPER_ACTION')),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  foreign key (account_id, user_id) references public.competition_accounts(id, user_id) on delete cascade,
  check (average_price is not null or exact_cost_basis is not null)
);

create unique index positions_one_open_side_per_ticker
  on public.positions (account_id, ticker, direction)
  where status = 'OPEN';

create table public.plans (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  account_id uuid not null,
  generated_at timestamptz not null default timezone('utc', now()),
  market_as_of timestamptz not null,
  portfolio_version bigint not null check (portfolio_version > 0),
  strategy_name text not null,
  status text not null check (status in ('CURRENT', 'STALE', 'ARCHIVED', 'FAILED')),
  stale_at timestamptz,
  stale_reason text,
  summary text not null default '',
  portfolio_snapshot jsonb not null default '{}'::jsonb,
  regime jsonb not null default '{}'::jsonb,
  warnings jsonb not null default '[]'::jsonb check (jsonb_typeof(warnings) = 'array'),
  created_at timestamptz not null default timezone('utc', now()),
  foreign key (account_id, user_id) references public.competition_accounts(id, user_id) on delete cascade,
  unique (id, user_id)
);

create table public.recommendations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  plan_id uuid not null,
  ticker text not null check (ticker = upper(ticker) and char_length(ticker) between 1 and 16),
  company text,
  recommended_action text not null check (recommended_action in ('BUY', 'SELL', 'WAIT', 'AVOID')),
  score numeric(12, 6) not null,
  signal text not null,
  reason text not null,
  factor_details jsonb not null default '{}'::jsonb,
  rank integer not null check (rank > 0),
  status text not null default 'OPEN' check (status in ('OPEN', 'DISMISSED', 'RECORDED')),
  last_price numeric(18, 6) check (last_price >= 0),
  day_move_percent numeric(12, 6),
  price_as_of timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  foreign key (plan_id, user_id) references public.plans(id, user_id) on delete cascade,
  unique (plan_id, ticker),
  unique (id, user_id)
);

create table public.recommendation_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  recommendation_id uuid not null,
  event_type text not null check (event_type in ('DISMISSED', 'RECORDED', 'REOPENED')),
  note text check (note is null or char_length(note) <= 2000),
  created_at timestamptz not null default timezone('utc', now()),
  foreign key (recommendation_id, user_id) references public.recommendations(id, user_id) on delete cascade
);

create table public.paper_actions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  account_id uuid not null,
  plan_id uuid,
  recommendation_id uuid,
  action_type text not null check (action_type in ('OPEN', 'CLOSE', 'INCREASE', 'DECREASE')),
  ticker text not null check (ticker = upper(ticker) and char_length(ticker) between 1 and 16),
  direction text not null check (direction in ('LONG', 'SHORT')),
  quantity numeric(24, 8) not null check (quantity > 0),
  price numeric(18, 6) not null check (price >= 0),
  occurred_at timestamptz not null,
  idempotency_key text not null check (char_length(idempotency_key) between 8 and 200),
  portfolio_version_before bigint not null,
  portfolio_version_after bigint not null,
  created_at timestamptz not null default timezone('utc', now()),
  foreign key (account_id, user_id) references public.competition_accounts(id, user_id) on delete cascade,
  foreign key (plan_id, user_id) references public.plans(id, user_id) on delete set null,
  foreign key (recommendation_id, user_id) references public.recommendations(id, user_id) on delete set null,
  unique (user_id, idempotency_key),
  check (portfolio_version_after > portfolio_version_before)
);

create table public.portfolio_snapshots (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  account_id uuid not null,
  as_of timestamptz not null,
  cash numeric(18, 4) not null check (cash >= 0),
  portfolio_value numeric(18, 4),
  long_exposure numeric(18, 4),
  short_exposure numeric(18, 4),
  unrealized_pnl numeric(18, 4),
  positions_snapshot jsonb not null default '[]'::jsonb check (jsonb_typeof(positions_snapshot) = 'array'),
  warnings jsonb not null default '[]'::jsonb check (jsonb_typeof(warnings) = 'array'),
  created_at timestamptz not null default timezone('utc', now()),
  foreign key (account_id, user_id) references public.competition_accounts(id, user_id) on delete cascade,
  unique (account_id, as_of)
);

create table public.import_jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  source_checksum text not null check (char_length(source_checksum) between 32 and 128),
  source_schema_version text,
  status text not null check (status in ('PREVIEWED', 'CONFIRMED', 'COMPLETED', 'FAILED')),
  preview_summary jsonb not null default '{}'::jsonb,
  warnings jsonb not null default '[]'::jsonb check (jsonb_typeof(warnings) = 'array'),
  confirmed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id, source_checksum)
);

create table public.audit_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  event_type text not null check (char_length(event_type) between 1 and 100),
  request_id text check (request_id is null or char_length(request_id) <= 200),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index competition_accounts_user_idx on public.competition_accounts(user_id);
create index positions_user_account_idx on public.positions(user_id, account_id, status);
create index plans_user_account_generated_idx on public.plans(user_id, account_id, generated_at desc);
create index recommendations_user_plan_rank_idx on public.recommendations(user_id, plan_id, rank);
create index paper_actions_user_account_time_idx on public.paper_actions(user_id, account_id, occurred_at desc);
create index portfolio_snapshots_user_account_time_idx on public.portfolio_snapshots(user_id, account_id, as_of desc);
create index audit_events_user_time_idx on public.audit_events(user_id, created_at desc);

create trigger competition_accounts_set_updated_at before update on public.competition_accounts
for each row execute function public.set_updated_at();
create trigger positions_set_updated_at before update on public.positions
for each row execute function public.set_updated_at();
create trigger profiles_set_updated_at before update on public.profiles
for each row execute function public.set_updated_at();
create trigger import_jobs_set_updated_at before update on public.import_jobs
for each row execute function public.set_updated_at();

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (user_id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name'))
  on conflict (user_id) do nothing;
  return new;
end;
$$;

revoke all on function public.handle_new_user() from public, anon, authenticated;

create trigger auth_user_created
after insert on auth.users
for each row execute function public.handle_new_user();

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'profiles', 'competition_accounts', 'positions', 'plans', 'recommendations',
    'recommendation_events', 'paper_actions', 'portfolio_snapshots', 'import_jobs', 'audit_events'
  ]
  loop
    execute format('alter table public.%I enable row level security', table_name);
    execute format('revoke all on table public.%I from anon', table_name);
    execute format('revoke all on table public.%I from authenticated', table_name);
  end loop;
end $$;

grant select, insert, update, delete on public.profiles to authenticated;
grant select, insert, update, delete on public.competition_accounts to authenticated;
grant select, insert, update, delete on public.positions to authenticated;
grant select, insert, update, delete on public.plans to authenticated;
grant select, insert, update, delete on public.recommendations to authenticated;
grant select, insert, update, delete on public.recommendation_events to authenticated;
grant select, insert, update, delete on public.paper_actions to authenticated;
grant select, insert on public.portfolio_snapshots to authenticated;
grant select, insert, update on public.import_jobs to authenticated;
grant select, insert on public.audit_events to authenticated;

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'profiles', 'competition_accounts', 'positions', 'plans', 'recommendations',
    'recommendation_events', 'paper_actions', 'portfolio_snapshots', 'import_jobs', 'audit_events'
  ]
  loop
    execute format(
      'create policy %I on public.%I for select to authenticated using ((select auth.uid()) is not null and (select auth.uid()) = user_id)',
      table_name || '_owner_select', table_name
    );
    execute format(
      'create policy %I on public.%I for insert to authenticated with check ((select auth.uid()) is not null and (select auth.uid()) = user_id)',
      table_name || '_owner_insert', table_name
    );
  end loop;
end $$;

create policy profiles_owner_update on public.profiles for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy profiles_owner_delete on public.profiles for delete to authenticated using ((select auth.uid()) = user_id);

create policy accounts_owner_update on public.competition_accounts for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy accounts_owner_delete on public.competition_accounts for delete to authenticated using ((select auth.uid()) = user_id);

create policy positions_owner_update on public.positions for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy positions_owner_delete on public.positions for delete to authenticated using ((select auth.uid()) = user_id);

create policy plans_owner_update on public.plans for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy plans_owner_delete on public.plans for delete to authenticated using ((select auth.uid()) = user_id);

create policy recommendations_owner_update on public.recommendations for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy recommendations_owner_delete on public.recommendations for delete to authenticated using ((select auth.uid()) = user_id);

create policy recommendation_events_owner_update on public.recommendation_events for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy recommendation_events_owner_delete on public.recommendation_events for delete to authenticated using ((select auth.uid()) = user_id);

create policy paper_actions_owner_update on public.paper_actions for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy paper_actions_owner_delete on public.paper_actions for delete to authenticated using ((select auth.uid()) = user_id);

create policy import_jobs_owner_update on public.import_jobs for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

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
  select * into selected_account
  from public.competition_accounts
  where user_id = (select auth.uid())
    and (p_account_id is null or id = p_account_id)
  order by updated_at desc
  limit 1;

  if selected_account.id is null then
    return null;
  end if;

  select * into selected_snapshot
  from public.portfolio_snapshots
  where user_id = (select auth.uid()) and account_id = selected_account.id
  order by as_of desc
  limit 1;

  select * into selected_plan
  from public.plans
  where user_id = (select auth.uid()) and account_id = selected_account.id
  order by generated_at desc
  limit 1;

  select coalesce(jsonb_agg(jsonb_build_object(
    'ticker', ticker,
    'side', direction,
    'quantity', quantity,
    'price', last_price,
    'value', market_value,
    'pnl', unrealized_pnl,
    'weight', portfolio_weight,
    'price_as_of', price_as_of
  ) order by ticker), '[]'::jsonb)
  into positions_json
  from public.positions
  where user_id = (select auth.uid()) and account_id = selected_account.id and status = 'OPEN';

  if selected_plan.id is null then
    recommendations_json := '[]'::jsonb;
  else
    select coalesce(jsonb_agg(jsonb_build_object(
      'ticker', ticker,
      'company', coalesce(company, ticker),
      'action', recommended_action,
      'score', score,
      'strategy', selected_plan.strategy_name,
      'reason', reason,
      'price', last_price,
      'move', day_move_percent,
      'price_as_of', price_as_of
    ) order by rank), '[]'::jsonb)
    into recommendations_json
    from public.recommendations
    where user_id = (select auth.uid()) and plan_id = selected_plan.id;
  end if;

  return jsonb_build_object(
    'snapshot_time', timezone('utc', now()),
    'data_mode', 'cloud',
    'portfolio', jsonb_build_object(
      'value', selected_snapshot.portfolio_value,
      'cash', selected_account.cash,
      'long_exposure', selected_snapshot.long_exposure,
      'short_exposure', selected_snapshot.short_exposure,
      'unrealized_pnl', selected_snapshot.unrealized_pnl
    ),
    'plan', jsonb_build_object(
      'generated_at', selected_plan.generated_at,
      'market_as_of', selected_plan.market_as_of,
      'status', coalesce(selected_plan.status, 'NONE'),
      'strategy', coalesce(selected_plan.strategy_name, 'No strategy selected'),
      'summary', coalesce(selected_plan.summary, 'No plan has been generated.'),
      'warnings', coalesce(selected_plan.warnings, '[]'::jsonb)
    ),
    'regime', coalesce(selected_plan.regime, jsonb_build_object(
      'name', 'UNAVAILABLE',
      'label', 'Unavailable',
      'reason', 'No current market regime is available.',
      'trend_strength', null,
      'market_breadth', null,
      'volatility', null,
      'preferred_strategies', '[]'::jsonb
    )),
    'recommendations', recommendations_json,
    'positions', positions_json
  );
end;
$$;

revoke all on function public.get_current_account_snapshot(uuid) from public, anon;
grant execute on function public.get_current_account_snapshot(uuid) to authenticated;

commit;
