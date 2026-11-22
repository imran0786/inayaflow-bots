-- ROBOTS_FUNNEL_READER_v4_same_hours.sql (genie helper, 22 Nov 2026). NOT RUN. The Director runs it after telling Imran.
-- Replaces ONLY the function public.ops_funnel_daily(text, integer) (v3 = ROBOTS_FUNNEL_READER_v3_visits.sql).
-- Same name, same inputs, same token check, SAME RIGHTS (the revoke/grant lines at the end are unchanged: no new grant).
-- Every number v3 gave is still there, unchanged. No table, row or token is touched. Safe to run twice.
-- Undo: run v3 again.
--
-- NEW, inside each day's 'plugin' object, a 'same_hours' object (counts only, never ids):
--   calls             : calls from REAL ChatGPT callers (real callers = caller_kind not 'outside', env production) made between 00:00 UTC and
--                       the time of day it is NOW (UTC), on that day. Today and each earlier day use the same window.
--   callers           : different callers in that same window.
--   new_callers       : different callers that day (whole day) whose first-ever call was on that day.
--   returning_callers : different callers that day (whole day) who also called on an earlier day.
-- NEW at the top level: 'now_utc' = "HH:MI" (the window end).
-- The 19 Nov 13:28-13:30 UTC test window is left out, like in the other plugin numbers.

create or replace function public.ops_funnel_daily(p_token text, p_days integer default 8)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_days  integer := least(greatest(coalesce(p_days, 8), 1), 31);
  v_from  date := (now() at time zone 'utc')::date - (v_days - 1);
  v_out   jsonb;
  v_el    interval := (now() at time zone 'utc') - date_trunc('day', now() at time zone 'utc');  -- v4: time of day now
begin
  if p_token is null or length(p_token) < 32 or not exists (
       select 1 from public.ops_read_tokens t
       where t.name = 'funnel_read'
         and t.token_hash = encode(sha256(convert_to(p_token, 'UTF8')), 'hex')) then
    raise exception 'not allowed' using errcode = '42501';
  end if;

  with d as (
    select generate_series(v_from, (now() at time zone 'utc')::date, interval '1 day')::date as day
  ),
  su as (
    select (u.created_at at time zone 'utc')::date as day, count(*) as n
    from auth.users u where u.created_at >= v_from group by 1
  ),
  src as (
    select (s.created_at at time zone 'utc')::date as day, count(*) as n
    from public.signup_sources s where s.env = 'production' and s.created_at >= v_from group by 1
  ),
  bk as (
    select day, jsonb_object_agg(bucket, n) as j from (
      select (s.created_at at time zone 'utc')::date as day, s.bucket, count(*) as n
      from public.signup_sources s where s.env = 'production' and s.created_at >= v_from group by 1, 2) x group by day
  ),
  ff as (
    select day, jsonb_object_agg(funnel_from, n) as j from (
      select (s.created_at at time zone 'utc')::date as day, s.funnel_from, count(*) as n
      from public.signup_sources s
      where s.env = 'production' and s.created_at >= v_from and s.funnel_from is not null group by 1, 2) x group by day
  ),
  pc as (
    select (p.created_at at time zone 'utc')::date as day, count(*) as calls, count(*) filter (where p.ok) as ok,
           count(distinct p.caller_hash) as callers, count(distinct p.handle_hash) as handles
    from public.plugin_calls p where p.env = 'production' and p.created_at >= v_from and coalesce(p.caller_kind, '') <> 'outside' and not (p.created_at >= timestamptz '2026-11-19 13:28:00+00' and p.created_at < timestamptz '2026-11-19 13:30:00+00') group by 1
  ),
  pout as (   -- calls from outside ChatGPT (scripts, scanners): counted apart, never mixed into the real numbers
    select (p.created_at at time zone 'utc')::date as day, count(*) as n
    from public.plugin_calls p
    where p.env = 'production' and p.created_at >= v_from and p.caller_kind = 'outside' group by 1
  ),
  poc as (   -- v2: outside calls per PROGRAM name (client_name; old rows and empty names count as 'unknown')
    select day, jsonb_object_agg(client, n) as j from (
      select (p.created_at at time zone 'utc')::date as day, coalesce(nullif(to_jsonb(p) ->> 'client_name', ''), 'unknown') as client, count(*) as n
      from public.plugin_calls p
      where p.env = 'production' and p.created_at >= v_from and p.caller_kind = 'outside' group by 1, 2) x group by day
  ),
  pocall as ( -- v2: how many DIFFERENT outside callers that day (a count of scrambled caller ids, never the ids)
    select (p.created_at at time zone 'utc')::date as day, count(distinct p.caller_hash) as n
    from public.plugin_calls p
    where p.env = 'production' and p.created_at >= v_from and p.caller_kind = 'outside' group by 1
  ),
  prc as (   -- v2: real (not outside) calls per PROGRAM name, same rows as 'calls' (our 19 Nov tests left out)
    select day, jsonb_object_agg(client, n) as j from (
      select (p.created_at at time zone 'utc')::date as day, coalesce(nullif(to_jsonb(p) ->> 'client_name', ''), 'unknown') as client, count(*) as n
      from public.plugin_calls p where p.env = 'production' and p.created_at >= v_from and coalesce(p.caller_kind, '') <> 'outside' and not (p.created_at >= timestamptz '2026-11-19 13:28:00+00' and p.created_at < timestamptz '2026-11-19 13:30:00+00') group by 1, 2) x group by day
  ),
  pna as (    -- real ChatGPT callers who got NO ok answer that day
    select day, count(*) as n from (
      select (p.created_at at time zone 'utc')::date as day, p.caller_hash
      from public.plugin_calls p where p.env = 'production' and p.created_at >= v_from and coalesce(p.caller_kind, '') <> 'outside' and not (p.created_at >= timestamptz '2026-11-19 13:28:00+00' and p.created_at < timestamptz '2026-11-19 13:30:00+00') and p.caller_hash is not null
      group by 1, 2 having count(*) filter (where p.ok) = 0) x group by day
  ),
  pa_start as (  -- public /analyze reports STARTED (one unit of the daily budget each)
    select b.day, sum(b.count) as n from public.public_analysis_budget b
    where b.scope = 'production' and b.day >= v_from group by 1
  ),
  pa_done as (   -- public /analyze reports FINISHED (the AI call was made and logged)
    select (a.created_at at time zone 'utc')::date as day, count(*) as n
    from public.ai_calls a where a.env = 'production' and a.feature = 'public_analysis' and a.created_at >= v_from group by 1
  ),
  pt as (
    select day, jsonb_object_agg(tool, n) as j from (
      select (p.created_at at time zone 'utc')::date as day, p.tool, count(*) as n
      from public.plugin_calls p where p.env = 'production' and p.created_at >= v_from and coalesce(p.caller_kind, '') <> 'outside' and not (p.created_at >= timestamptz '2026-11-19 13:28:00+00' and p.created_at < timestamptz '2026-11-19 13:30:00+00') group by 1, 2) x group by day
  ),
  po as (
    select day, jsonb_object_agg(outcome, n) as j from (
      select (p.created_at at time zone 'utc')::date as day, p.outcome, count(*) as n
      from public.plugin_calls p where p.env = 'production' and p.created_at >= v_from and coalesce(p.caller_kind, '') <> 'outside' and not (p.created_at >= timestamptz '2026-11-19 13:28:00+00' and p.created_at < timestamptz '2026-11-19 13:30:00+00') group by 1, 2) x group by day
  ),
  shfs as (   -- v4: first day each real caller was seen, looking back 180 days (bounded scan; the plugin is days old, so this equals 'ever' for now)
    select p.caller_hash, min((p.created_at at time zone 'utc')::date) as fday
    from public.plugin_calls p
    where p.env = 'production' and coalesce(p.caller_kind, '') <> 'outside' and p.caller_hash is not null
      and p.created_at >= ((now() at time zone 'utc')::date - 180)::timestamptz
      and not (p.created_at >= timestamptz '2026-11-19 13:28:00+00' and p.created_at < timestamptz '2026-11-19 13:30:00+00')
    group by 1
  ),
  sh as (     -- v4: same-hours calls and callers, and new vs returning callers, per day
    select (p.created_at at time zone 'utc')::date as day,
           count(*) filter (where (p.created_at at time zone 'utc') - date_trunc('day', p.created_at at time zone 'utc') <= v_el) as calls,
           count(distinct p.caller_hash) filter (where (p.created_at at time zone 'utc') - date_trunc('day', p.created_at at time zone 'utc') <= v_el) as callers,
           count(distinct p.caller_hash) filter (where f.fday = (p.created_at at time zone 'utc')::date) as new_callers,
           count(distinct p.caller_hash) filter (where f.fday < (p.created_at at time zone 'utc')::date) as returning_callers
    from public.plugin_calls p left join shfs f on f.caller_hash = p.caller_hash
    where p.env = 'production' and p.created_at >= v_from and coalesce(p.caller_kind, '') <> 'outside'
      and not (p.created_at >= timestamptz '2026-11-19 13:28:00+00' and p.created_at < timestamptz '2026-11-19 13:30:00+00')
    group by 1
  ),
  vt as (     -- v3: visits per day (every visit, not only sign-ups)
    select (v.created_at at time zone 'utc')::date as day, count(*) as n
    from public.visit_sources v where v.env = 'production' and v.created_at >= v_from group by 1
  ),
  vb as (     -- v3: visits per source bucket
    select day, jsonb_object_agg(bucket, n) as j from (
      select (v.created_at at time zone 'utc')::date as day, v.bucket, count(*) as n
      from public.visit_sources v where v.env = 'production' and v.created_at >= v_from group by 1, 2) x group by day
  ),
  vai as (    -- v3: AI visits by where exactly they came from, the 10 biggest per day
    select day, jsonb_object_agg(src, n) as j from (
      select day, src, n, row_number() over (partition by day order by n desc, src) as rn from (
        select (v.created_at at time zone 'utc')::date as day,
               coalesce(lower(nullif(v.utm_source, '')), nullif(v.ref_host, ''), 'unknown')
                 || coalesce(' / ' || nullif(v.funnel_from, ''), '') as src,
               count(*) as n
        from public.visit_sources v
        where v.env = 'production' and v.created_at >= v_from and v.bucket = 'ai' group by 1, 2) y) x
    where rn <= 10 group by day
  )
  select jsonb_build_object(
    'today', to_char((now() at time zone 'utc')::date, 'YYYY-MM-DD'),
    'now_utc', to_char(now() at time zone 'utc', 'HH24:MI'),
    'days', (select jsonb_agg(jsonb_build_object(
               'day', to_char(d.day, 'YYYY-MM-DD'),
               'signups', coalesce(su.n, 0),
               'with_source', coalesce(src.n, 0),
               'buckets', coalesce(bk.j, '{}'::jsonb),
               'from', coalesce(ff.j, '{}'::jsonb),
               'analyze', jsonb_build_object('started', coalesce(pa_start.n, 0), 'finished', coalesce(pa_done.n, 0)),
               'plugin', jsonb_build_object('calls', coalesce(pc.calls, 0), 'ok', coalesce(pc.ok, 0),
                                            'no_answer_callers', coalesce(pna.n, 0), 'outside_calls', coalesce(pout.n, 0),
                                            'callers', coalesce(pc.callers, 0), 'handles', coalesce(pc.handles, 0),
                                            'by_tool', coalesce(pt.j, '{}'::jsonb), 'by_outcome', coalesce(po.j, '{}'::jsonb),
                                            'outside_by_client', coalesce(poc.j, '{}'::jsonb), 'outside_callers', coalesce(pocall.n, 0),
                                            'real_by_client', coalesce(prc.j, '{}'::jsonb),
                                            'same_hours', jsonb_build_object('calls', coalesce(sh.calls, 0), 'callers', coalesce(sh.callers, 0),
                                                          'new_callers', coalesce(sh.new_callers, 0), 'returning_callers', coalesce(sh.returning_callers, 0))),
               'visits', jsonb_build_object('total', coalesce(vt.n, 0), 'by_bucket', coalesce(vb.j, '{}'::jsonb),
                                            'ai_from', coalesce(vai.j, '{}'::jsonb))
             ) order by d.day desc)
             from d left join su on su.day = d.day left join src on src.day = d.day left join bk on bk.day = d.day
                    left join ff on ff.day = d.day left join pc on pc.day = d.day left join pt on pt.day = d.day
                    left join po on po.day = d.day left join pout on pout.day = d.day left join pna on pna.day = d.day
                    left join pa_start on pa_start.day = d.day left join pa_done on pa_done.day = d.day
                    left join poc on poc.day = d.day left join pocall on pocall.day = d.day left join prc on prc.day = d.day
                    left join sh on sh.day = d.day left join vt on vt.day = d.day left join vb on vb.day = d.day left join vai on vai.day = d.day),
    'paying_total', (select count(*) from public.profiles pr where pr.tier is not null and pr.tier <> 'free'),
    'paying_by_tier', coalesce((select jsonb_object_agg(tier, n) from (
        select pr.tier, count(*) as n from public.profiles pr
        where pr.tier is not null and pr.tier <> 'free' group by 1) x), '{}'::jsonb)
  ) into v_out;
  return v_out;
end;
$$;

-- Supabase gives EXECUTE on new functions to anon and authenticated by default, on top of PUBLIC: take all three away first.
revoke all on function public.ops_funnel_daily(text, integer) from public, anon, authenticated;
grant execute on function public.ops_funnel_daily(text, integer) to anon, service_role;
