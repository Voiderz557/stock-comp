begin;

create unique index competition_accounts_one_per_user
  on public.competition_accounts (user_id);

create or replace function public.create_or_get_competition_account(
  p_name text,
  p_starting_capital numeric,
  p_cash numeric,
  p_snapshot_as_of timestamptz
)
returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
  account_id uuid;
begin
  if (select auth.uid()) is null then
    raise exception 'Authentication is required.';
  end if;

  if nullif(btrim(p_name), '') is null or char_length(btrim(p_name)) > 120 then
    raise exception 'Account name must contain 1 to 120 characters.';
  end if;

  if p_starting_capital < 0 or p_cash < 0 then
    raise exception 'Capital and cash cannot be negative.';
  end if;

  insert into public.competition_accounts (
    user_id, name, starting_capital, cash, snapshot_as_of
  )
  values (
    (select auth.uid()), btrim(p_name), p_starting_capital, p_cash, p_snapshot_as_of
  )
  on conflict (user_id) do nothing
  returning id into account_id;

  if account_id is null then
    select id into account_id
    from public.competition_accounts
    where user_id = (select auth.uid());
  else
    insert into public.portfolio_snapshots (
      user_id, account_id, as_of, cash, portfolio_value,
      long_exposure, short_exposure, unrealized_pnl,
      positions_snapshot, warnings
    )
    values (
      (select auth.uid()), account_id, p_snapshot_as_of, p_cash, p_cash,
      0, 0, null, '[]'::jsonb, '[]'::jsonb
    );
  end if;

  return account_id;
end;
$$;

revoke all on function public.create_or_get_competition_account(text, numeric, numeric, timestamptz)
  from public, anon;
grant execute on function public.create_or_get_competition_account(text, numeric, numeric, timestamptz)
  to authenticated;

commit;
