-- Pilot metrics for Vedomo (Postgres). One snapshot of the 5-7 pilot numbers.
--
-- Run on the VPS (one command, all queries at once):
--   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
--     psql -U vedomo -d vedomo < scripts/pilot_metrics.sql
--
-- Tip: to exclude your own test/teacher accounts from the counts, add a filter
-- like  AND owner_user_id NOT IN (SELECT id FROM users WHERE email IN ('you@...'))
-- to the relevant subqueries. Kept simple/inclusive by default.

\echo '== Totals / day-0 baseline =='
select
  (select count(*) from users)                              as users,
  (select count(*) from documents)                          as documents,
  (select count(*) from workspaces where kind = 'course')   as courses,
  (select count(*) from usage_events where action = 'chat') as questions;

\echo '== 1. Activation (registered AND uploaded a material AND asked a question) =='
select
  (select count(*) from users)                                            as registered,
  (select count(distinct owner_user_id) from documents)                  as uploaded_material,
  (select count(distinct user_id) from usage_events where action='chat') as asked_question,
  (select count(*) from users u
     where exists (select 1 from documents d     where d.owner_user_id = u.id)
       and exists (select 1 from usage_events e  where e.user_id = u.id and e.action = 'chat')
  )                                                                       as activated;

\echo '== 2. Retention (users active on 2+ distinct UTC days) =='
select count(*) as returned_2plus_days from (
  select user_id
  from usage_events
  where user_id is not null
  group by user_id
  having count(distinct (created_at at time zone 'UTC')::date) >= 2
) t;

\echo '== 3. Activity per active user (averages) =='
select
  round(count(*) filter (where action='chat')::numeric    / nullif(count(distinct user_id),0), 1) as avg_questions,
  round(count(*) filter (where action='summary')::numeric / nullif(count(distinct user_id),0), 1) as avg_summaries,
  round(count(*) filter (where action='study')::numeric   / nullif(count(distinct user_id),0), 1) as avg_study
from usage_events where user_id is not null;

\echo '== 4. Volume (totals) =='
select
  (select count(*) from documents)                           as total_materials,
  (select count(*) from usage_events where action='chat')    as total_questions,
  (select count(*) from usage_events where action='summary') as total_summaries,
  (select count(*) from usage_events where action='study')   as total_study;

\echo '== 5. Course funnel =='
select
  (select count(*) from workspaces where kind='course')                        as courses,
  (select count(*) from workspace_members where role='student')                as student_memberships,
  (select count(distinct user_id) from workspace_members where role='student') as distinct_students,
  (select count(*) from assignments)                                           as assignments_total,
  (select count(*) from assignments where is_published)                        as assignments_published,
  (select count(*) from assignment_attempts)                                   as attempts;

\echo '-- RAG quality (#7) is qualitative: skim a few chats for grounded answers + source links.'
