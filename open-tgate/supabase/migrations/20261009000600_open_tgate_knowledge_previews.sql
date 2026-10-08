-- Return bounded source-management previews before data leaves PostgreSQL.
-- The API verifies the requesting operator before calling this service-only RPC.
create or replace function public.open_tgate_list_knowledge_previews(
  page_limit integer default 101, page_offset integer default 0
)
returns table(
  id uuid, title text, content text, source_type text,
  approved boolean, enabled boolean, created_at timestamptz
)
language sql stable security invoker set search_path to '' as $$
  select source.id, source.title, left(source.content, 180), source.source_type,
    source.approved, source.enabled, source.created_at
  from public.open_tgate_knowledge_sources as source
  order by source.created_at desc, source.id asc
  limit greatest(1, least(coalesce(page_limit, 101), 101))
  offset greatest(0, coalesce(page_offset, 0));
$$;
revoke all on function public.open_tgate_list_knowledge_previews(integer, integer) from public, anon, authenticated;
grant execute on function public.open_tgate_list_knowledge_previews(integer, integer) to service_role;
comment on function public.open_tgate_list_knowledge_previews(integer, integer) is
  'Operator API management list: service-only stable pagination; at most 101 rows with 180-character content previews.';
notify pgrst, 'reload schema';
