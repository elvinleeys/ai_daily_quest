-- Supabase SQL Editor에서 한 번 실행
create table if not exists app_state (
  user_id    text primary key,
  data       jsonb not null,
  updated_at timestamptz not null default now()
);
-- 정책 없이 RLS만 켜면 anon 키로는 접근 불가. server.py의 service_role 키만 읽고 쓴다.
alter table app_state enable row level security;
