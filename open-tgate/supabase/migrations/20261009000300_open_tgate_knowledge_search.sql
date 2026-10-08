-- Search the complete approved corpus without exporting whole source contents.
-- English stemming handles common business policy/support questions.
alter table public.open_tgate_knowledge_sources
  add column if not exists search_document tsvector generated always as (
    to_tsvector('english'::regconfig, coalesce(title, '') || ' ' || coalesce(content, ''))
  ) stored;
create index if not exists open_tgate_knowledge_search_idx
  on public.open_tgate_knowledge_sources using gin (search_document)
  where enabled and approved;

create or replace function public.open_tgate_search_knowledge(query_text text, result_limit integer default 5)
returns table(id uuid, title text, excerpt text)
language sql stable security invoker set search_path to '' as $$
  with words as (
    select distinct plainto_tsquery('english'::regconfig, term)::text as parsed
    from regexp_split_to_table(lower(left(coalesce(query_text, ''), 4000)), '[^[:alnum:]_]+') as tokens(term)
    where term <> ''
    limit 100
  ), encoded as (
    select string_agg('(' || parsed || ')', ' | ') filter (where parsed <> '') as value from words
  ), query as (
    select case when value is null or value = '' then null::tsquery
      else to_tsquery('english'::regconfig, value) end as value from encoded
  )
  select source.id, source.title,
    left(ts_headline('english'::regconfig, source.content, query.value,
      'StartSel=[, StopSel=], MaxWords=250, MinWords=30, MaxFragments=3, FragmentDelimiter= ... '), 1600)
  from public.open_tgate_knowledge_sources as source cross join query
  where source.enabled and source.approved and source.search_document @@ query.value
  order by ts_rank(source.search_document, query.value) desc, source.created_at desc, source.id asc
  limit greatest(1, least(coalesce(result_limit, 5), 5));
$$;
-- PUBLIC grants are implicit on new functions: revoke explicitly.
revoke all on function public.open_tgate_search_knowledge(text, integer) from public, anon, authenticated;
grant execute on function public.open_tgate_search_knowledge(text, integer) to service_role;
comment on function public.open_tgate_search_knowledge(text, integer) is
  'Service-only whole-corpus full-text retrieval. Enabled approved sources only; at most 5 excerpts of 1600 chars.';
-- Resolve one key owner's current operator entry without reading/exporting
-- every operator email, and without case-sensitive lookup mismatches.
create or replace function public.open_tgate_api_key_owner_active(owner_email text)
returns boolean language sql stable security invoker set search_path to '' as $$
  select exists (
    select 1 from public.open_tgate_operators as operator
    where operator.is_active and lower(operator.email) = lower(owner_email)
  );
$$;
revoke all on function public.open_tgate_api_key_owner_active(text) from public, anon, authenticated;
grant execute on function public.open_tgate_api_key_owner_active(text) to service_role;
notify pgrst, 'reload schema';
