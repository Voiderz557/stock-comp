begin;

-- Advisory settings only. These rows never own positions, plans, or orders.
-- Keep the existing competition account schema and its history untouched.
create table public.planning_profiles (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  name text not null check (char_length(btrim(name)) between 1 and 80),
  mode text not null check (mode in ('COMPETITION', 'TRADINGVIEW')),
  rules jsonb not null check (jsonb_typeof(rules) = 'object'),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id, name)
);

create index planning_profiles_user_created_idx
  on public.planning_profiles (user_id, created_at);

create trigger planning_profiles_set_updated_at
before update on public.planning_profiles
for each row execute function public.set_updated_at();

alter table public.planning_profiles enable row level security;
revoke all on table public.planning_profiles from anon, authenticated;
grant select, insert, update on public.planning_profiles to authenticated;

create policy planning_profiles_owner_select on public.planning_profiles
  for select to authenticated
  using ((select auth.uid()) is not null and (select auth.uid()) = user_id);
create policy planning_profiles_owner_insert on public.planning_profiles
  for insert to authenticated
  with check ((select auth.uid()) is not null and (select auth.uid()) = user_id);
create policy planning_profiles_owner_update on public.planning_profiles
  for update to authenticated
  using ((select auth.uid()) is not null and (select auth.uid()) = user_id)
  with check ((select auth.uid()) is not null and (select auth.uid()) = user_id);

commit;
