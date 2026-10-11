begin;

insert into auth.users (id, instance_id, aud, role, email, encrypted_password, email_confirmed_at, created_at, updated_at)
values
  ('11111111-1111-1111-1111-111111111111', '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'owner-a@example.invalid', '', now(), now(), now()),
  ('22222222-2222-2222-2222-222222222222', '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'owner-b@example.invalid', '', now(), now(), now());

insert into public.competition_accounts (id, user_id, name, starting_capital, cash, snapshot_as_of)
values
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'A', 100000, 90000, now()),
  ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-2222-2222-2222-222222222222', 'B', 100000, 80000, now());

insert into public.plans (id, user_id, account_id, market_as_of, portfolio_version, strategy_name, status)
values
  ('aaaaaaaa-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', now(), 1, 'Baseline', 'CURRENT'),
  ('bbbbbbbb-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', now(), 1, 'Baseline', 'CURRENT');

insert into public.recommendations (id, user_id, plan_id, ticker, recommended_action, score, signal, reason, rank)
values
  ('aaaaaaaa-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'aaaaaaaa-0000-0000-0000-000000000001', 'AAPL', 'BUY', 1, 'BUY', 'Example', 1),
  ('bbbbbbbb-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', 'bbbbbbbb-0000-0000-0000-000000000001', 'MSFT', 'BUY', 1, 'BUY', 'Example', 1);

insert into public.planning_profiles (id, user_id, name, mode, rules)
values
  ('aaaaaaaa-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', 'A planner', 'COMPETITION', '{"target_position_value":10000}'::jsonb),
  ('bbbbbbbb-0000-0000-0000-000000000003', '22222222-2222-2222-2222-222222222222', 'B planner', 'TRADINGVIEW', '{"stop_loss_pct":5}'::jsonb);

set local role authenticated;
select set_config('request.jwt.claim.sub', '11111111-1111-1111-1111-111111111111', true);
select set_config('request.jwt.claim.role', 'authenticated', true);

do $$
declare
  visible_accounts integer;
  visible_name text;
  other_snapshot jsonb;
  first_account_id uuid;
  repeated_account_id uuid;
  history_json jsonb;
  own_recommendation uuid := 'aaaaaaaa-0000-0000-0000-000000000002';
begin
  select count(*)::integer, min(name)
  into visible_accounts, visible_name
  from public.competition_accounts;

  if visible_accounts <> 1 or visible_name <> 'A' then
    raise exception 'RLS failure: caller can see an unexpected account set';
  end if;

  begin
    insert into public.competition_accounts (user_id, name, starting_capital, cash, snapshot_as_of)
    values ('22222222-2222-2222-2222-222222222222', 'forged', 1, 1, now());
    raise exception 'RLS failure: caller inserted an account for another user';
  exception
    when insufficient_privilege then null;
  end;

  first_account_id := public.create_or_get_competition_account('ignored duplicate', 1, 1, now());
  repeated_account_id := public.create_or_get_competition_account('ignored duplicate', 2, 2, now());
  if first_account_id <> 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'::uuid
    or repeated_account_id <> first_account_id then
    raise exception 'Account setup RPC is not idempotent';
  end if;

  select count(*)::integer into visible_accounts from public.competition_accounts;
  if visible_accounts <> 1 then
    raise exception 'Account setup RPC created a duplicate account';
  end if;

  if (public.get_current_account_snapshot('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'::uuid) -> 'portfolio' ->> 'cash')::numeric <> 90000 then
    raise exception 'RLS failure: snapshot RPC did not return the caller-owned account';
  end if;

  other_snapshot := public.get_current_account_snapshot('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'::uuid);
  if other_snapshot is not null then
    raise exception 'RLS failure: snapshot RPC returned another user account';
  end if;

  history_json := public.get_account_plan_history(20);
  if jsonb_array_length(history_json) <> 1 or history_json -> 0 ->> 'id' <> 'aaaaaaaa-0000-0000-0000-000000000001' then
    raise exception 'RLS failure: plan history included another user or omitted the owner plan';
  end if;

  begin
    perform public.set_recommendation_status('bbbbbbbb-0000-0000-0000-000000000002', 'DISMISSED');
    raise exception 'RLS failure: caller changed another user recommendation';
  exception when serialization_failure then null;
  end;

  perform public.set_recommendation_status(own_recommendation, 'DISMISSED');
  perform public.set_recommendation_status(own_recommendation, 'DISMISSED');
  if (select count(*) from public.recommendation_events where recommendation_id = own_recommendation) <> 1 then
    raise exception 'Repeated dismissal created duplicate events';
  end if;
  perform public.set_recommendation_status(own_recommendation, 'REOPENED');
  if (select status from public.recommendations where id = own_recommendation) <> 'OPEN' then
    raise exception 'Reopening did not restore the recommendation';
  end if;

  if exists (select 1 from public.profiles where user_id = '22222222-2222-2222-2222-222222222222') then
    raise exception 'RLS failure: another user profile is visible';
  end if;
  if (select count(*) from public.planning_profiles) <> 1 then
    raise exception 'RLS failure: planning profiles crossed user boundary';
  end if;
  update public.planning_profiles set name = 'forged' where id = 'bbbbbbbb-0000-0000-0000-000000000003';
  if found then
    raise exception 'RLS failure: caller updated another user planning profile';
  end if;
  begin
    insert into public.planning_profiles (user_id, name, mode, rules)
    values ('22222222-2222-2222-2222-222222222222', 'forged', 'COMPETITION', '{}'::jsonb);
    raise exception 'RLS failure: caller inserted another user planning profile';
  exception when insufficient_privilege then null;
  end;
end;
$$;

reset role;
select set_config('request.jwt.claim.sub', '', true);
set local role anon;

do $$
begin
  begin
    perform * from public.competition_accounts;
    raise exception 'RLS failure: anonymous role read account data';
  exception
    when insufficient_privilege then null;
  end;
  begin
    perform * from public.planning_profiles;
    raise exception 'RLS failure: anonymous role read planning profiles';
  exception
    when insufficient_privilege then null;
  end;
end;
$$;

reset role;
rollback;

select 'public_app_rls_passed' as result;
